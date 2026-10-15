"""FastAPI app: project registry, ingestion, scheduled refresh, status, and RAG query."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config import REINGEST_INTERVAL_MINUTES
from app.scheduler import ReingestScheduler
from db import database as db
from ingestion.doc_ingestor import ingest_docs
from ingestion.git_ingestor import ingest_git
from ingestion.runner import ingestion_lock, reingest_all, reingest_project
from rag import llm
from rag.pipeline import answer_question
from rag.status import generate_status
from rag.vector_store import delete_project_chunks, latest_commit_date

logging.basicConfig(level=logging.INFO)

scheduler = ReingestScheduler(REINGEST_INTERVAL_MINUTES)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(title="StandUp Bot", lifespan=lifespan)


# --------------------------------------------------------------- schemas
class CreateProjectRequest(BaseModel):
    name: str
    docs_path: str | None = None
    repo_path: str | None = None


class IngestDocsRequest(BaseModel):
    project_id: int
    docs_path: str | None = None  # falls back to the project's stored docs_path


class IngestGitRequest(BaseModel):
    project_id: int
    repo_path: str | None = None  # falls back to the project's stored repo_path


class QueryRequest(BaseModel):
    question: str
    project_id: int | None = None  # None = search across all projects
    top_k: int = 6


# --------------------------------------------------------------- helpers
def _require_project(project_id: int):
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


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


# ------------------------------------------------------------- projects
@app.post("/projects")
def create_project(req: CreateProjectRequest):
    if db.get_project_by_name(req.name):
        raise HTTPException(400, f"Project '{req.name}' already exists")
    project_id = db.create_project(req.name, req.docs_path, req.repo_path)
    return {"id": project_id, "name": req.name}


@app.get("/projects")
def list_projects():
    return [dict(row) for row in db.list_projects()]


@app.get("/projects/{project_id}")
def get_project(project_id: int):
    return dict(_require_project(project_id))


@app.delete("/projects/{project_id}")
def delete_project(project_id: int):
    project = _require_project(project_id)
    with ingestion_lock:
        delete_project_chunks(project_id)
        db.delete_project(project_id)
    return {"deleted": project_id, "name": project["name"]}


# ------------------------------------------------------------ ingestion
@app.post("/ingest/docs")
def ingest_docs_endpoint(req: IngestDocsRequest):
    project = _require_project(req.project_id)
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
def ingest_git_endpoint(req: IngestGitRequest):
    project = _require_project(req.project_id)
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
def reingest_project_endpoint(project_id: int, auto_status: bool | None = None):
    """Refresh one project: re-scan docs, pull new commits, update status if anything changed."""
    _require_project(project_id)
    return reingest_project(project_id, trigger="manual", auto_status=auto_status)


@app.post("/ingest/all")
def reingest_all_endpoint(auto_status: bool | None = None):
    """Refresh every project (what the scheduler runs on its interval)."""
    return reingest_all(trigger="manual", auto_status=auto_status)


@app.get("/projects/{project_id}/runs")
def list_runs(project_id: int, limit: int = 20):
    _require_project(project_id)
    return [_run_to_dict(r) for r in db.list_ingestion_runs(project_id, limit)]


@app.get("/scheduler")
def scheduler_info():
    return scheduler.info()


# --------------------------------------------------------------- status
@app.post("/projects/{project_id}/status")
def generate_status_endpoint(project_id: int):
    _require_project(project_id)
    try:
        return _call_claude(generate_status, project_id)
    except ValueError as e:  # nothing ingested yet
        raise HTTPException(400, str(e))


@app.get("/projects/{project_id}/status")
def get_status(project_id: int):
    _require_project(project_id)
    update = db.update_to_dict(db.latest_update(project_id))
    if not update:
        raise HTTPException(404, "No status generated yet for this project")
    return update


@app.get("/dashboard")
def dashboard():
    """One row per project: what's ingested, when it was refreshed, and its latest status."""
    rows = []
    for project in db.list_projects():
        pid = project["id"]
        rows.append({
            **dict(project),
            "counts": db.document_counts(pid),
            "last_commit_date": latest_commit_date(pid),
            "last_run": _run_to_dict(db.latest_ingestion_run(pid)),
            "latest_update": db.update_to_dict(db.latest_update(pid)),
        })
    return rows


# ---------------------------------------------------------------- query
@app.post("/query")
def query_endpoint(req: QueryRequest):
    if req.project_id is not None:
        _require_project(req.project_id)
    return _call_claude(answer_question, req.question, project_id=req.project_id, top_k=req.top_k)


@app.get("/health")
def health():
    return {"status": "ok", "llm_configured": llm.is_configured(), "scheduler": scheduler.info()}
