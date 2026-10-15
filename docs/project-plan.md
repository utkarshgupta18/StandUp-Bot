---
title: RAG Project Tracker — Scope & Phased Plan
last_updated: 2026-10-15
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
  - Vector store: Chroma (local, file-based, no infra cost)
  - Metadata store: SQLite (projects, documents, tasks, ingestion runs)
  - Embeddings: **sentence-transformers (`all-MiniLM-L6-v2`), local and free** — Anthropic's API has no embeddings endpoint, so Claude is used only for generation
  - Frontend: Streamlit to start (fastest to build); can swap to React later if the UI needs grow
  - Hosting: runs locally or on a small VPS — no paid infra required to start

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

### Phase 4 — Polish & scale (as needed)
- Swap Streamlit for a React/Next.js frontend if you want a nicer UI or plan to share this with others.
- Move SQLite → Postgres and Chroma → pgvector or a hosted vector DB if data volume or concurrent use grows.
- Add auth if this ever needs to be accessible beyond your own machine.
- Add more source connectors (Slack, Linear/Jira, Notion) if "project updates" should pull from more than docs + git.

## Open questions to revisit later
- ~~How often should re-ingestion run (on-demand vs. scheduled)?~~ Both are supported; the interval is configurable (`REINGEST_INTERVAL_MINUTES`), off by default.
- Should code itself (not just commits) be embedded for deeper "what changed and why" answers, or is commit history + docs enough?
- Any projects with private/sensitive repos that need access-control thought before Phase 2?

## Implementation notes
- `EMBEDDING_BACKEND=hashing` is an offline fallback embedder used only for testing in network-restricted environments — leave it unset for real use so it defaults to `sentence-transformers`.
- Git ingestion embeds commit message + changed-files + stats, not full diffs, to keep chunk volume sane on real repos.
- The Anthropic SDK was upgraded from 0.34.2 to 1.15.0: 0.34 can't construct a client against current `httpx` releases, and the status generator needs structured outputs. Default model is `claude-opus-5-5` (`CLAUDE_MODEL`, `CLAUDE_EFFORT` in `.env`).
- Run `pytest` for the full offline suite; `smoke_test.py` still checks ingestion + retrieval on the demo data.
