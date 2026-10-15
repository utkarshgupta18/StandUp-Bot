-- Multi-project tracker schema.
-- Vector embeddings live in Chroma; SQLite holds the structured metadata
-- that the UI/API needs (project registry, ingestion bookkeeping, and
-- Phase 3's structured status updates).

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    docs_path   TEXT,            -- folder of markdown/text docs to ingest
    repo_path   TEXT,            -- local path to a git repo to scan
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per ingested source unit (a doc file, or a git commit).
-- chunk_ids stores the corresponding Chroma vector IDs (comma-separated)
-- so we know what to delete/refresh on re-ingestion.
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type     TEXT NOT NULL CHECK (source_type IN ('doc', 'commit')),
    source_ref      TEXT NOT NULL,   -- file path, or commit hash
    content_hash    TEXT NOT NULL,   -- to detect changes and skip re-embedding
    chunk_ids       TEXT,            -- comma-separated Chroma IDs
    last_ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project_id, source_type, source_ref)
);

-- Phase 2: one row per re-ingestion run (on-demand or scheduled), so the
-- UI/dashboard can show when a project was last refreshed and what changed.
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trigger           TEXT NOT NULL DEFAULT 'manual',   -- manual | scheduled
    started_at        TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at       TEXT,
    status            TEXT NOT NULL DEFAULT 'running',  -- running | ok | error
    docs_ingested     INTEGER NOT NULL DEFAULT 0,
    docs_removed      INTEGER NOT NULL DEFAULT 0,
    commits_ingested  INTEGER NOT NULL DEFAULT 0,
    errors            TEXT             -- newline-separated error messages
);

-- Phase 3: structured status summaries generated per project.
CREATE TABLE IF NOT EXISTS updates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    summary         TEXT NOT NULL,   -- one-paragraph headline summary
    generated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    source_refs     TEXT,            -- comma-separated document source_refs used
    status_json     TEXT             -- full structured status (done/in_progress/next_steps/blockers)
);

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_updates_project ON updates(project_id);
CREATE INDEX IF NOT EXISTS idx_runs_project ON ingestion_runs(project_id);
