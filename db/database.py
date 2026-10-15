"""Thin SQLite helper layer. No ORM -- this is a small side project."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from app.config import SQLITE_PATH

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't add
# them to a database created by an older version, so init_db adds them.
_ADDED_COLUMNS = {
    "updates": {"status_json": "TEXT"},
}


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA_PATH.read_text())
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, col_type in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


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


def delete_project(project_id: int) -> None:
    """Delete a project; documents, runs and updates cascade via foreign keys."""
    with get_conn() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


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


def delete_document(project_id: int, source_type: str, source_ref: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM documents WHERE project_id = ? AND source_type = ? AND source_ref = ?",
            (project_id, source_type, source_ref),
        )


def list_documents(project_id: int, source_type: Optional[str] = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if source_type:
            return conn.execute(
                "SELECT * FROM documents WHERE project_id = ? AND source_type = ?",
                (project_id, source_type),
            ).fetchall()
        return conn.execute("SELECT * FROM documents WHERE project_id = ?", (project_id,)).fetchall()


def document_counts(project_id: int) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source_type, COUNT(*) AS n FROM documents WHERE project_id = ? GROUP BY source_type",
            (project_id,),
        ).fetchall()
    counts = {"doc": 0, "commit": 0}
    counts.update({row["source_type"]: row["n"] for row in rows})
    return counts


# ---------------------------------------------------------- ingestion runs
def start_ingestion_run(project_id: int, trigger: str = "manual") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ingestion_runs (project_id, trigger) VALUES (?, ?)",
            (project_id, trigger),
        )
        return cur.lastrowid


def finish_ingestion_run(
    run_id: int,
    status: str,
    docs_ingested: int = 0,
    docs_removed: int = 0,
    commits_ingested: int = 0,
    errors: Optional[list[str]] = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ingestion_runs
            SET finished_at = datetime('now'), status = ?, docs_ingested = ?,
                docs_removed = ?, commits_ingested = ?, errors = ?
            WHERE id = ?
            """,
            (status, docs_ingested, docs_removed, commits_ingested, "\n".join(errors or []) or None, run_id),
        )


def list_ingestion_runs(project_id: int, limit: int = 20) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM ingestion_runs WHERE project_id = ? ORDER BY id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()


def latest_ingestion_run(project_id: int) -> Optional[sqlite3.Row]:
    runs = list_ingestion_runs(project_id, limit=1)
    return runs[0] if runs else None


# ----------------------------------------------------------------- updates
def add_update(project_id: int, summary: str, source_refs: list[str], status: Optional[dict] = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO updates (project_id, summary, source_refs, status_json) VALUES (?, ?, ?, ?)",
            (project_id, summary, ",".join(source_refs), json.dumps(status) if status is not None else None),
        )
        return cur.lastrowid


def get_update(update_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM updates WHERE id = ?", (update_id,)).fetchone()


def latest_update(project_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM updates WHERE project_id = ? ORDER BY generated_at DESC, id DESC LIMIT 1",
            (project_id,),
        ).fetchone()


def update_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    """Decode an `updates` row into a plain dict with the structured status parsed."""
    if row is None:
        return None
    data = dict(row)
    data["source_refs"] = [r for r in (data.get("source_refs") or "").split(",") if r]
    raw = data.pop("status_json", None)
    try:
        data["status"] = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        data["status"] = None
    return data
