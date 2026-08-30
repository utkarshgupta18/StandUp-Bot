"""FastAPI app: project registry, ingestion endpoints, and RAG query endpoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from db import database as db
from ingestion.doc_ingestor import ingest_docs
from ingestion.git_ingestor import ingest_git
from rag.pipeline import answer_question


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="RAG Project Tracker", lifespan=lifespan)


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


# ------------------------------------------------------------- endpoints
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
    row = db.get_project(project_id)
    if not row:
        raise HTTPException(404, "Project not found")
    return dict(row)


@app.post("/ingest/docs")
def ingest_docs_endpoint(req: IngestDocsRequest):
    project = db.get_project(req.project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    docs_path = req.docs_path or project["docs_path"]
    if not docs_path:
        raise HTTPException(400, "No docs_path provided or stored for this project")
    db.upsert_project_paths(req.project_id, docs_path=docs_path)
    try:
        report = ingest_docs(req.project_id, docs_path)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    return report


@app.post("/ingest/git")
def ingest_git_endpoint(req: IngestGitRequest):
    project = db.get_project(req.project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    repo_path = req.repo_path or project["repo_path"]
    if not repo_path:
        raise HTTPException(400, "No repo_path provided or stored for this project")
    db.upsert_project_paths(req.project_id, repo_path=repo_path)
    try:
        report = ingest_git(req.project_id, repo_path)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return report


@app.post("/query")
def query_endpoint(req: QueryRequest):
    if req.project_id is not None and not db.get_project(req.project_id):
        raise HTTPException(404, "Project not found")
    try:
        result = answer_question(req.question, project_id=req.project_id, top_k=req.top_k)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return result


@app.get("/health")
def health():
    return {"status": "ok"}
