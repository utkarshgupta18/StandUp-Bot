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

-- Phase 3 hook: structured status summaries generated periodically.
-- Created now so the schema doesn't need to change later.
CREATE TABLE IF NOT EXISTS updates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    summary         TEXT NOT NULL,
    generated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    source_refs     TEXT             -- comma-separated document source_refs used
);

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_updates_project ON updates(project_id);
