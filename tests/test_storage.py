"""Phase 4: storage backends and migrations from older databases."""
import sqlite3

import pytest

from db import database as db

# The schema shipped before Phase 4 (documents.source_type limited by a CHECK,
# no sources/users tables, no items_ingested/status_json columns).
LEGACY_SCHEMA = """
CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    docs_path TEXT, repo_path TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('doc', 'commit')),
    source_ref TEXT NOT NULL, content_hash TEXT NOT NULL, chunk_ids TEXT,
    last_ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project_id, source_type, source_ref));
CREATE TABLE updates (id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    summary TEXT NOT NULL, generated_at TEXT NOT NULL DEFAULT (datetime('now')), source_refs TEXT);
INSERT INTO projects (name) VALUES ('old-project');
INSERT INTO documents (project_id, source_type, source_ref, content_hash, chunk_ids)
    VALUES (1, 'doc', 'readme.md', 'abc', 'doc:1:readme.md:0');
"""


@pytest.mark.skipif(db.IS_POSTGRES, reason="SQLite-only migration")
def test_legacy_sqlite_database_is_migrated(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.close()
    monkeypatch.setattr(db, "SQLITE_PATH", path)

    db.init_db()
    db.init_db()  # idempotent

    project = db.get_project_by_name("old-project")
    assert db.get_document(project["id"], "doc", "readme.md")["content_hash"] == "abc"
    sid = db.create_source(project["id"], "jira", "board", {"project_key": "X"})
    db.upsert_document(project["id"], "jira", "X-1", "h", ["jira:1:X-1:0"], source_id=sid)  # CHECK is gone
    assert db.list_source_documents(sid)[0]["source_ref"] == "X-1"
    run = db.start_ingestion_run(project["id"])
    db.finish_ingestion_run(run, "ok", items_ingested=3)
    assert db.latest_ingestion_run(project["id"])["items_ingested"] == 3
    db.add_login_failures(["ip:1.2.3.4"], 1_800_000_000.5)  # login_failures table added too
    assert db.recent_login_failures("ip:1.2.3.4", since=0, limit=5) == [1_800_000_000.5]
    db.add_update(project["id"], "s", [], {"health": "unknown"})
    assert db.update_to_dict(db.latest_update(project["id"]))["status"] == {"health": "unknown"}


def test_timestamps_use_one_format(two_projects):
    project = db.get_project(two_projects["demo"])
    assert len(project["created_at"]) == 19 and project["created_at"][10] == " "


def test_health_reports_backends(client):
    storage = client.get("/health").json()["storage"]
    assert storage["database"] == ("postgres" if db.IS_POSTGRES else "sqlite")
    assert storage["vectors"] in ("chroma", "pgvector")
