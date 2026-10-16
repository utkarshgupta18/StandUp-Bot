---
title: RAG Project Tracker — Scope & Phased Plan
last_updated: 2026-10-16
---

# RAG Project Tracker — Scope & Phased Plan

## Vision

A chatbot, backed by retrieval-augmented generation (RAG), that lets you query the state of your projects in natural language: what's the latest update, what's been done, what's pending next. Designed from the start to hold **multiple projects**, each with its own docs and codebase, so it can eventually act as a general tracker across all of your side projects — ingesting docs and scanning git history/code to infer and summarize progress.

## Decisions locked in

- **Scope**: multi-project data model from day one (not bolted on later).
- **Data sources**: written docs/notes AND git commits/code (commit messages, README, diffs) — both feed the "what's the latest" answer.
- **Stack**: Python full stack.
  - Backend: FastAPI
  - RAG pipeline: direct `anthropic` SDK calls (no LangChain dependency needed so far), all routed through `rag/llm.py`
  - Vector store: Chroma (local, file-based, no infra cost) by default; pgvector in Postgres optional (Phase 4)
  - Metadata store: SQLite by default; Postgres optional via `DATABASE_URL` (Phase 4)
  - Embeddings: **sentence-transformers (`all-MiniLM-L6-v2`), local and free** — Anthropic's API has no embeddings endpoint, so Claude is used only for generation
  - Frontend: React + TypeScript (Vite) web app served by the API (Phase 4); the original Streamlit UI is kept as a lightweight alternative
  - Hosting: runs locally or on a small VPS — no paid infra required to start; Dockerfile + docker-compose (with Postgres/pgvector) for a shared instance

## Core data model (sketch)

- `projects` — id, name, repo path/URL, doc source path(s), created_at
- `documents` — id, project_id, source_type (doc/commit/code/readme), path or commit hash, content, last_ingested_at
- `chunks` — id, document_id, text, embedding, metadata (project_id, source_type, date)
- `updates` — id, project_id, summary, generated_at, source_refs (which chunks it drew from)

Every chunk is tagged with `project_id` so retrieval can be scoped ("only answer about Project X") or run across all projects ("what's pending across everything").

## Phased plan

### Phase 0 — Foundations ✅ DONE
- Repo structure, Python env (venv), FastAPI skeleton, SQLite schema for `projects`/`documents`/`updates`.
- Claude API wiring in `rag/pipeline.py` (needs your own `ANTHROPIC_API_KEY` in `.env` to actually call it).
- Chroma running locally (persistent client in `rag/vector_store.py`), confirmed working via smoke test.

### Phase 1 — Single-project ingestion + Q&A (MVP core) ✅ DONE
- Doc ingestor (`ingestion/doc_ingestor.py`): chunks markdown/text files, embeds, content-hash based so unchanged files are skipped on re-ingestion.
- Git ingestor (`ingestion/git_ingestor.py`): pulls commit log via GitPython (message, date, files changed, insert/delete stats), embeds per commit; commits are immutable so already-ingested ones are always skipped cheaply.
- RAG query endpoint (`rag/pipeline.py` + `app/main.py` `/query`): retrieves top-k chunks scoped by `project_id`, Claude generates an answer, response includes cited sources (doc path / commit hash).
- Streamlit UI (`ui/streamlit_app.py`): project picker, ingest buttons, chat-style Q&A with a sources expander.
- Verified end-to-end via `smoke_test.py` and live FastAPI calls with a sample project — ingestion, retrieval, and chunk ranking all confirmed working.

### Phase 2 — Multi-project support ✅ DONE
- Per-project re-ingestion in one step (`ingestion/runner.py`, `POST /projects/{id}/reingest`, `POST /ingest/all`): re-scans docs (changed files re-embedded, **deleted files removed from the index**) and pulls new commits. Every run is recorded in a new `ingestion_runs` table (`GET /projects/{id}/runs`).
- Cross-project retrieval (`query_across_projects` in `rag/vector_store.py`): queries each project separately and merges, so every project gets a fair share of the context; each chunk is labelled with its project name in the prompt and in the returned sources.
- Scheduled re-ingestion (`app/scheduler.py`): background task in the API process, every `REINGEST_INTERVAL_MINUTES` (0 = off). A process-wide lock stops scheduled and on-demand runs from overlapping.
- UI: "(all projects)" mode, per-project and all-project refresh buttons, per-scope chat history, project deletion (`DELETE /projects/{id}` also drops its vectors), sources show which project they came from.
- Second demo project (`sample_data/second_project`, "invoice-service") so multi-project behaviour is exercised for real.
- Success check: covered by `tests/test_multi_project.py` (scoped queries never leak, cross-project retrieval and prompt cover every project, re-runs are cheap, new commits / doc edits / deletions are picked up, scheduler refreshes everything).

### Phase 3 — Structured "status" intelligence ✅ DONE
- `rag/status.py`: Claude reads a project's newest commits (by commit time) and its docs and returns a structured status via structured outputs (JSON schema): summary, health (`on_track` / `at_risk` / `blocked` / `unknown`), done, in progress, next steps, blockers — each item citing a doc path or commit hash. Stored in `updates` (new `status_json` column, migrated automatically on existing databases).
- Generated on demand (`POST /projects/{id}/status`) and automatically after any refresh that found changes (`STATUS_AUTO_GENERATE`), so scheduled runs don't spend tokens on idle projects.
- The chatbot includes the latest status of the projects in scope as extra context, and is told to prefer newer, more specific retrieved commits/docs when they disagree.
- Dashboard: `GET /dashboard` plus a Streamlit Dashboard tab (per project: doc/commit counts, last commit, last refresh and its errors, health badge, summary, and the four status columns).
- Covered by `tests/test_status.py` (Claude stubbed out).

### Phase 4 — Polish & scale ✅ DONE
- **React frontend** (`web/`, Vite + React + TypeScript): chat with per-project or all-projects scope and linked sources, dashboard (health, summary, done / in progress / next / blockers, refresh and regenerate), project and source management with run history, user management, and login when auth is on. It's served by the API at `/app/`, works at phone width and in dark mode, and was checked end to end in Chromium. Streamlit stays as an alternative and supports a bearer token.
- **Postgres + pgvector**: `DATABASE_URL` switches metadata to Postgres, and `VECTOR_BACKEND=pgvector` stores embeddings in a `chunks` table with an HNSW cosine index. Both backends sit behind the same functions, and the full test suite runs against SQLite + Chroma and Postgres + pgvector (`STANDUP_TEST_DATABASE_URL`). Older SQLite databases are migrated in place: the `documents.source_type` CHECK is dropped and new columns are added. `docker-compose.yml` runs Postgres + pgvector and the API.
- **Auth** (`app/auth.py`, off by default): username/password login, PBKDF2 password hashes, and HMAC-signed expiring bearer tokens. There are two roles: admin (everything) and viewer (read and chat). Viewers only see the projects granted to them, including in "all projects" questions, and other projects return 404. Users are managed from the web app or `python -m app.manage`, and the first admin can be bootstrapped from env.
- **Connectors** (`connectors/`): Slack (channel history and threads, one document per day), Linear (GraphQL issues per team), Jira Cloud (JQL search, rich-text descriptions flattened), and Notion (pages and databases with properties and blocks). Sources are configured per project, credentials come from env, and sources sync with every refresh, with hash-based skipping. Their items feed the status prompt ("issues, messages and pages, newest first") and chat answers, with links back. A failing source is reported, and the rest of the refresh still runs.
- Covered by `tests/test_connectors.py` (HTTP faked with payloads shaped like each API), `tests/test_auth.py`, and `tests/test_storage.py`.

## Open questions to revisit later
- ~~How often should re-ingestion run (on-demand vs. scheduled)?~~ Both are supported; the interval is configurable (`REINGEST_INTERVAL_MINUTES`), off by default.
- Should code itself (not just commits) be embedded for deeper "what changed and why" answers, or is commit history + docs enough?
- ~~Any projects with private/sensitive repos that need access-control thought?~~ Per-project access for viewers (Phase 4 auth).
- Push instead of poll: Slack/Linear/Jira webhooks could trigger a source sync right away instead of waiting for the scheduler.
- SSO (OIDC) instead of local passwords if this is shared across an organisation.

## Implementation notes
- `EMBEDDING_BACKEND=hashing` is an offline fallback embedder used only for testing in network-restricted environments — leave it unset for real use so it defaults to `sentence-transformers`.
- Git ingestion embeds commit message + changed-files + stats, not full diffs, to keep chunk volume sane on real repos.
- The Anthropic SDK was upgraded from 0.34.2 to 1.15.0: 0.34 can't construct a client against current `httpx` releases, and the status generator needs structured outputs. Default model is `claude-opus-5-5` (`CLAUDE_MODEL`, `CLAUDE_EFFORT` in `.env`).
- Run `pytest` for the full offline suite; `smoke_test.py` still checks ingestion + retrieval on the demo data.
- Connector credentials are only ever read from the environment; the `sources` table holds non-secret settings (channel ids, team keys, JQL, page ids), so a database dump doesn't leak tokens.
- Switching storage backends doesn't migrate data between them; refresh projects after switching to re-ingest.
