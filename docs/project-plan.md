---
title: RAG Project Tracker — Scope & Phased Plan
last_updated: 2026-08-30
---

# RAG Project Tracker — Scope & Phased Plan

## Vision

A chatbot, backed by retrieval-augmented generation (RAG), that lets you query the state of your projects in natural language: what's the latest update, what's been done, what's pending next. Designed from the start to hold **multiple projects**, each with its own docs and codebase, so it can eventually act as a general tracker across all of your side projects — ingesting docs and scanning git history/code to infer and summarize progress.

## Decisions locked in

- **Scope**: multi-project data model from day one (not bolted on later).
- **Data sources**: written docs/notes AND git commits/code (commit messages, README, diffs) — both feed the "what's the latest" answer.
- **Stack**: Python full stack.
  - Backend: FastAPI
  - RAG pipeline: direct `anthropic` SDK calls (no LangChain dependency needed so far)
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

### Phase 2 — Multi-project support (NEXT)
- Extend ingestion to run per-project and tag everything correctly (already modeled in Phase 0, now exercised for real with 2+ projects).
- Add a project switcher / "all projects" mode in the UI. (`/query` already supports `project_id=None` for cross-project search — needs UI wiring and testing with real multi-project data.)
- Add a scheduled or on-demand re-ingestion step (re-scan docs + pull new commits) so answers stay current without manual reload.
- Success check: ask a cross-project question ("which projects have pending work?") and get a correct answer spanning multiple projects.

### Phase 3 — Structured "status" intelligence
- Move beyond raw retrieval: have the system periodically generate a structured status object per project — done, in-progress, next steps, blockers — derived from recent commits + docs, stored in `updates` (table already exists, unused so far).
- Chatbot can then answer "what's pending" from this structured summary rather than re-deriving it every time, which is faster and more consistent.
- Add a lightweight dashboard view (per project: last update date, status summary) in the Streamlit UI.

### Phase 4 — Polish & scale (as needed)
- Swap Streamlit for a React/Next.js frontend if you want a nicer UI or plan to share this with others.
- Move SQLite → Postgres and Chroma → pgvector or a hosted vector DB if data volume or concurrent use grows.
- Add auth if this ever needs to be accessible beyond your own machine.
- Add more source connectors (Slack, Linear/Jira, Notion) if "project updates" should pull from more than docs + git.

## Open questions to revisit later
- How often should re-ingestion run (on-demand vs. scheduled)?
- Should code itself (not just commits) be embedded for deeper "what changed and why" answers, or is commit history + docs enough?
- Any projects with private/sensitive repos that need access-control thought before Phase 2?

## Implementation notes
- `EMBEDDING_BACKEND=hashing` is an offline fallback embedder used only for testing in network-restricted environments — leave it unset for real use so it defaults to `sentence-transformers`.
- Git ingestion embeds commit message + changed-files + stats, not full diffs, to keep chunk volume sane on real repos.
