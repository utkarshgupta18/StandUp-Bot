"""Thin SQLite helper layer. No ORM -- this is a small side project."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from app.config import SQLITE_PATH

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA_PATH.read_text())


@contextmanager
def get_conn():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- projects
def create_project(name: str, docs_path: Optional[str] = None, repo_path: Optional[str] = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, docs_path, repo_path) VALUES (?, ?, ?)",
            (name, docs_path, repo_path),
        )
        return cur.lastrowid


def get_project_by_name(name: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()


def get_project(project_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def list_projects() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM projects ORDER BY name").fetchall()


def upsert_project_paths(project_id: int, docs_path: Optional[str] = None, repo_path: Optional[str] = None) -> None:
    with get_conn() as conn:
        if docs_path is not None:
            conn.execute("UPDATE projects SET docs_path = ? WHERE id = ?", (docs_path, project_id))
        if repo_path is not None:
            conn.execute("UPDATE projects SET repo_path = ? WHERE id = ?", (repo_path, project_id))


# --------------------------------------------------------------- documents
def get_document(project_id: int, source_type: str, source_ref: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM documents WHERE project_id = ? AND source_type = ? AND source_ref = ?",
            (project_id, source_type, source_ref),
        ).fetchone()


def upsert_document(project_id: int, source_type: str, source_ref: str, content_hash: str, chunk_ids: list[str]) -> None:
    chunk_ids_str = ",".join(chunk_ids)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO documents (project_id, source_type, source_ref, content_hash, chunk_ids, last_ingested_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(project_id, source_type, source_ref)
            DO UPDATE SET content_hash=excluded.content_hash,
                          chunk_ids=excluded.chunk_ids,
                          last_ingested_at=datetime('now')
            """,
            (project_id, source_type, source_ref, content_hash, chunk_ids_str),
        )


def list_documents(project_id: int, source_type: Optional[str] = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if source_type:
            return conn.execute(
                "SELECT * FROM documents WHERE project_id = ? AND source_type = ?",
                (project_id, source_type),
            ).fetchall()
        return conn.execute("SELECT * FROM documents WHERE project_id = ?", (project_id,)).fetchall()


# ----------------------------------------------------------------- updates
def add_update(project_id: int, summary: str, source_refs: list[str]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO updates (project_id, summary, source_refs) VALUES (?, ?, ?)",
            (project_id, summary, ",".join(source_refs)),
        )
        return cur.lastrowid


def latest_update(project_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM updates WHERE project_id = ? ORDER BY generated_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
