-- Multi-project tracker schema, shared by SQLite and Postgres.
-- {pk} and {now} are filled in per dialect by db/database.py.
-- Vector embeddings live in the vector store (Chroma or pgvector); this
-- database holds the structured metadata the UI/API needs.
-- Timestamps are stored as UTC 'YYYY-MM-DD HH:MM:SS' text in both dialects.

CREATE TABLE IF NOT EXISTS projects (
    id          {pk},
    name        TEXT NOT NULL UNIQUE,
    docs_path   TEXT,            -- folder of markdown/text docs to ingest
    repo_path   TEXT,            -- local path to a git repo to scan
    created_at  TEXT NOT NULL DEFAULT {now}
);

-- One row per ingested source unit: a doc file, a git commit, or an item
-- from a connector (Slack day, Linear/Jira issue, Notion page).
-- chunk_ids stores the corresponding vector IDs (comma-separated) so we
-- know what to delete/refresh on re-ingestion.
CREATE TABLE IF NOT EXISTS documents (
    id              {pk},
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type     TEXT NOT NULL,   -- doc | commit | slack | linear | jira | notion
    source_ref      TEXT NOT NULL,   -- file path, commit hash, or connector item id
    content_hash    TEXT NOT NULL,   -- to detect changes and skip re-embedding
    chunk_ids       TEXT,            -- comma-separated vector IDs
    last_ingested_at TEXT NOT NULL DEFAULT {now},
    source_id       INTEGER,         -- sources.id for connector items, else NULL
    UNIQUE(project_id, source_type, source_ref)
);

-- Phase 4: external sources (connectors) attached to a project.
-- config holds non-secret settings as JSON (channel id, team key, JQL...);
-- credentials come from environment variables, never from this table.
CREATE TABLE IF NOT EXISTS sources (
    id              {pk},
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,   -- slack | linear | jira | notion
    name            TEXT NOT NULL,
    config          TEXT NOT NULL,   -- JSON
    created_at      TEXT NOT NULL DEFAULT {now},
    last_synced_at  TEXT,
    last_error      TEXT
);

-- Phase 2: one row per re-ingestion run (on-demand or scheduled), so the
-- UI/dashboard can show when a project was last refreshed and what changed.
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                {pk},
    project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trigger           TEXT NOT NULL DEFAULT 'manual',   -- manual | scheduled
    started_at        TEXT NOT NULL DEFAULT {now},
    finished_at       TEXT,
    status            TEXT NOT NULL DEFAULT 'running',  -- running | ok | error
    docs_ingested     INTEGER NOT NULL DEFAULT 0,
    docs_removed      INTEGER NOT NULL DEFAULT 0,
    commits_ingested  INTEGER NOT NULL DEFAULT 0,
    items_ingested    INTEGER NOT NULL DEFAULT 0,      -- connector items
    errors            TEXT             -- newline-separated error messages
);

-- Phase 3: structured status summaries generated per project.
CREATE TABLE IF NOT EXISTS updates (
    id              {pk},
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    summary         TEXT NOT NULL,   -- one-paragraph headline summary
    generated_at    TEXT NOT NULL DEFAULT {now},
    source_refs     TEXT,            -- comma-separated document source_refs used
    status_json     TEXT             -- full structured status (done/in_progress/next_steps/blockers)
);

-- Phase 4: users and per-project access (only used when AUTH_ENABLED=true).
-- Admins see every project; viewers only the projects granted to them.
CREATE TABLE IF NOT EXISTS users (
    id              {pk},
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',   -- admin | viewer
    created_at      TEXT NOT NULL DEFAULT {now}
);

CREATE TABLE IF NOT EXISTS project_access (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_updates_project ON updates(project_id);
CREATE INDEX IF NOT EXISTS idx_runs_project ON ingestion_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project_id);
