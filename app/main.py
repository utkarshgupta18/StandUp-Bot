"""FastAPI app: projects, ingestion, connectors, scheduled refresh, status, auth, and RAG query."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auth
from app import config as cfg
from app.scheduler import ReingestScheduler
from connectors import ConnectorError, describe_connectors, get_connector
from db import database as db
from ingestion.doc_ingestor import ingest_docs
from ingestion.git_ingestor import ingest_git
from ingestion.runner import ingestion_lock, reingest_all, reingest_project
from ingestion.source_ingestor import ingest_source, remove_source
from rag import llm
from rag.pipeline import answer_question
from rag.status import generate_status
from rag.vector_store import delete_project_chunks, latest_commit_date

logging.basicConfig(level=logging.INFO)

scheduler = ReingestScheduler(cfg.REINGEST_INTERVAL_MINUTES)
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    auth.bootstrap_admin()
    scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(title="StandUp Bot", lifespan=lifespan)
if cfg.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

Viewer = Depends(auth.current_user)
Admin = Depends(auth.require_admin)


# --------------------------------------------------------------- schemas
class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    docs_path: str | None = None
    repo_path: str | None = None


class UpdateProjectRequest(BaseModel):
    docs_path: str | None = None
    repo_path: str | None = None


class IngestDocsRequest(BaseModel):
    project_id: int
    docs_path: str | None = None  # falls back to the project's stored docs_path


class IngestGitRequest(BaseModel):
    project_id: int
    repo_path: str | None = None  # falls back to the project's stored repo_path


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    project_id: int | None = None  # None = search across all (visible) projects
    top_k: int = Field(6, ge=1, le=30)


class CreateSourceRequest(BaseModel):
    kind: str
    name: str | None = None
    config: dict = {}


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    role: str = "viewer"
    project_ids: list[int] = []


class UpdateUserRequest(BaseModel):
    password: str | None = Field(None, min_length=8)
    role: str | None = None
    project_ids: list[int] | None = None


# --------------------------------------------------------------- helpers
def _require_project(project_id: int, user: dict) -> dict:
    """The project, or 404 -- also for projects the user isn't allowed to see."""
    project = db.get_project(project_id)
    if not project or not auth.can_see_project(user, project_id):
        raise HTTPException(404, "Project not found")
    return project


def _require_source(source_id: int, user: dict) -> dict:
    source = db.get_source(source_id)
    if not source or not auth.can_see_project(user, source["project_id"]):
        raise HTTPException(404, "Source not found")
    return source


def _call_claude(fn, *args, **kwargs):
    """Run something that calls Claude and map its failures to HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except RuntimeError as e:  # missing API key
        raise HTTPException(400, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    except anthropic.APIError as e:
        raise HTTPException(502, f"Claude API error: {e}")


def _run_to_dict(row) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    data["errors"] = [e for e in (data.get("errors") or "").split("\n") if e]
    return data


def _check_role(role: str | None) -> None:
    if role is not None and role not in auth.ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(auth.ROLES)}")


def _check_projects(project_ids: list[int]) -> None:
    missing = [pid for pid in project_ids if not db.get_project(pid)]
    if missing:
        raise HTTPException(400, f"Unknown project ids: {missing}")


# ------------------------------------------------------------------ auth
@app.get("/auth/config")
def auth_config():
    """Public: tells the frontend whether it needs to show a login screen."""
    return {"auth_enabled": cfg.AUTH_ENABLED}


@app.post("/auth/login")
def login(req: LoginRequest):
    if not cfg.AUTH_ENABLED:
        raise HTTPException(400, "Authentication is disabled (AUTH_ENABLED=false)")
    user = auth.authenticate(req.username, req.password)
    if not user:
        raise HTTPException(401, "Wrong username or password")
    return {"token": auth.create_token(user["id"]), "user": auth.public_user(user)}


@app.get("/auth/me")
def me(user: dict = Viewer):
    return auth.public_user(user)


@app.get("/users")
def list_users(_: dict = Admin):
    return [auth.public_user(u) for u in db.list_users()]


@app.post("/users")
def create_user(req: CreateUserRequest, _: dict = Admin):
    _check_role(req.role)
    _check_projects(req.project_ids)
    if db.get_user_by_username(req.username):
        raise HTTPException(400, f"User '{req.username}' already exists")
    user_id = db.create_user(req.username, auth.hash_password(req.password), req.role)
    db.set_user_projects(user_id, req.project_ids)
    return auth.public_user(db.get_user(user_id))


@app.patch("/users/{user_id}")
def update_user(user_id: int, req: UpdateUserRequest, current: dict = Admin):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    _check_role(req.role)
    if req.role and req.role != "admin" and user_id == current["id"]:
        raise HTTPException(400, "You can't remove your own admin role")
    db.update_user(
        user_id,
        password_hash=auth.hash_password(req.password) if req.password else None,
        role=req.role,
    )
    if req.project_ids is not None:
        _check_projects(req.project_ids)
        db.set_user_projects(user_id, req.project_ids)
    return auth.public_user(db.get_user(user_id))


@app.delete("/users/{user_id}")
def delete_user(user_id: int, current: dict = Admin):
    if user_id == current["id"]:
        raise HTTPException(400, "You can't delete yourself")
    if not db.get_user(user_id):
        raise HTTPException(404, "User not found")
    db.delete_user(user_id)
    return {"deleted": user_id}


# ------------------------------------------------------------- projects
@app.post("/projects")
def create_project(req: CreateProjectRequest, _: dict = Admin):
    if db.get_project_by_name(req.name):
        raise HTTPException(400, f"Project '{req.name}' already exists")
    project_id = db.create_project(req.name, req.docs_path or None, req.repo_path or None)
    return {"id": project_id, "name": req.name}


@app.get("/projects")
def list_projects(user: dict = Viewer):
    return db.list_projects(auth.visible_project_ids(user))


@app.get("/projects/{project_id}")
def get_project(project_id: int, user: dict = Viewer):
    return _require_project(project_id, user)


@app.patch("/projects/{project_id}")
def update_project(project_id: int, req: UpdateProjectRequest, user: dict = Admin):
    _require_project(project_id, user)
    db.upsert_project_paths(project_id, docs_path=req.docs_path, repo_path=req.repo_path)
    return db.get_project(project_id)


@app.delete("/projects/{project_id}")
def delete_project(project_id: int, user: dict = Admin):
    project = _require_project(project_id, user)
    with ingestion_lock:
        delete_project_chunks(project_id)
        db.delete_project(project_id)
    return {"deleted": project_id, "name": project["name"]}


# ------------------------------------------------------------ ingestion
@app.post("/ingest/docs")
def ingest_docs_endpoint(req: IngestDocsRequest, user: dict = Admin):
    project = _require_project(req.project_id, user)
    docs_path = req.docs_path or project["docs_path"]
    if not docs_path:
        raise HTTPException(400, "No docs_path provided or stored for this project")
    db.upsert_project_paths(req.project_id, docs_path=docs_path)
    try:
        with ingestion_lock:
            report = ingest_docs(req.project_id, docs_path)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    return report


@app.post("/ingest/git")
def ingest_git_endpoint(req: IngestGitRequest, user: dict = Admin):
    project = _require_project(req.project_id, user)
    repo_path = req.repo_path or project["repo_path"]
    if not repo_path:
        raise HTTPException(400, "No repo_path provided or stored for this project")
    db.upsert_project_paths(req.project_id, repo_path=repo_path)
    try:
        with ingestion_lock:
            report = ingest_git(req.project_id, repo_path)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return report


@app.post("/projects/{project_id}/reingest")
def reingest_project_endpoint(project_id: int, auto_status: bool | None = None, user: dict = Admin):
    """Refresh one project: docs, git and sources, then status if anything changed."""
    _require_project(project_id, user)
    return reingest_project(project_id, trigger="manual", auto_status=auto_status)


@app.post("/ingest/all")
def reingest_all_endpoint(auto_status: bool | None = None, _: dict = Admin):
    """Refresh every project (what the scheduler runs on its interval)."""
    return reingest_all(trigger="manual", auto_status=auto_status)


@app.get("/projects/{project_id}/runs")
def list_runs(project_id: int, limit: int = 20, user: dict = Viewer):
    _require_project(project_id, user)
    return [_run_to_dict(r) for r in db.list_ingestion_runs(project_id, limit)]


@app.get("/scheduler")
def scheduler_info(_: dict = Viewer):
    return scheduler.info()


# ------------------------------------------------------------ connectors
@app.get("/connectors")
def list_connectors(_: dict = Viewer):
    """Available source kinds, whether their credentials are set, and their settings."""
    return describe_connectors()


@app.get("/projects/{project_id}/sources")
def list_sources(project_id: int, user: dict = Viewer):
    _require_project(project_id, user)
    return db.list_sources(project_id)


@app.post("/projects/{project_id}/sources")
def create_source(project_id: int, req: CreateSourceRequest, user: dict = Admin):
    _require_project(project_id, user)
    try:
        connector = get_connector(req.kind)
        config = connector.validate_config(req.config)
    except ConnectorError as e:
        raise HTTPException(400, str(e))
    name = (req.name or "").strip() or f"{connector.label} source"
    source_id = db.create_source(project_id, req.kind, name, config)
    return db.get_source(source_id)


@app.post("/sources/{source_id}/sync")
def sync_source(source_id: int, user: dict = Admin):
    source = _require_source(source_id, user)
    try:
        with ingestion_lock:
            return ingest_source(source)
    except ConnectorError as e:
        raise HTTPException(400, str(e))


@app.delete("/sources/{source_id}")
def delete_source(source_id: int, user: dict = Admin):
    source = _require_source(source_id, user)
    with ingestion_lock:
        removed = remove_source(source)
    return {"deleted": source_id, "documents_removed": removed}


# --------------------------------------------------------------- status
@app.post("/projects/{project_id}/status")
def generate_status_endpoint(project_id: int, user: dict = Admin):
    _require_project(project_id, user)
    try:
        return _call_claude(generate_status, project_id)
    except ValueError as e:  # nothing ingested yet
        raise HTTPException(400, str(e))


@app.get("/projects/{project_id}/status")
def get_status(project_id: int, user: dict = Viewer):
    _require_project(project_id, user)
    update = db.update_to_dict(db.latest_update(project_id))
    if not update:
        raise HTTPException(404, "No status generated yet for this project")
    return update


@app.get("/dashboard")
def dashboard(user: dict = Viewer):
    """One row per visible project: what's ingested, when it was refreshed, and its latest status."""
    rows = []
    for project in db.list_projects(auth.visible_project_ids(user)):
        pid = project["id"]
        rows.append({
            **project,
            "counts": db.document_counts(pid),
            "sources": db.list_sources(pid),
            "last_commit_date": latest_commit_date(pid),
            "last_run": _run_to_dict(db.latest_ingestion_run(pid)),
            "latest_update": db.update_to_dict(db.latest_update(pid)),
        })
    return rows


# ---------------------------------------------------------------- query
@app.post("/query")
def query_endpoint(req: QueryRequest, user: dict = Viewer):
    if req.project_id is not None:
        _require_project(req.project_id, user)
    return _call_claude(
        answer_question, req.question, project_id=req.project_id, top_k=req.top_k,
        allowed_project_ids=auth.visible_project_ids(user),
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "llm_configured": llm.is_configured(),
        "auth_enabled": cfg.AUTH_ENABLED,
        "storage": {"database": "postgres" if db.IS_POSTGRES else "sqlite", "vectors": cfg.VECTOR_BACKEND},
        "scheduler": scheduler.info(),
    }


# ------------------------------------------------------------- frontend
# The React app (web/, built with `npm run build`) is served at /app.
if WEB_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=WEB_DIST, html=True), name="web")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/app/")
