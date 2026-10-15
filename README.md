# StandUp Bot

A RAG-powered chatbot that gives you your own project standup, on demand.
Ask it what's been done, what's still pending, or what the latest update
is on a project, and it answers by retrieving from that project's docs and
git commit history — with sources cited back to the exact doc or commit.

Built as a multi-project tracker from the start: point it at more than one
project and it can answer questions scoped to one of them, or across all
of them at once.

**Status:** Phases 0–3 are done: single- and multi-project ingestion and
Q&A, cross-project questions ("which projects have pending work?"),
on-demand and scheduled re-ingestion, and Claude-generated structured
status (done / in progress / next steps / blockers) shown on a dashboard
and used by the chatbot. Phase 4 (React UI, Postgres, auth, more
connectors) is optional, as-needed polish. See
[`docs/project-plan.md`](docs/project-plan.md) for the full roadmap.

## Business use case

Status reporting is a recurring tax on anyone who owns more than one
project: "what did we ship this sprint," "what's still open," "where did
we leave off" — questions that currently get answered by manually
re-reading commit logs, digging through docs, or writing a status update
by hand before a standup or a client check-in. StandUp Bot answers those
questions on demand, straight from the ground truth (your actual docs and
commit history) instead of a summary someone has to remember to write.

Who this is for:

- **Solo developers and freelancers** juggling several client or side
  projects, who need to reconstruct "where did I leave off" in seconds
  instead of scrolling through old commits.
- **Freelancers and agencies** who bill by project and need a quick, honest
  answer to "what have you actually done" before a client update — with
  sources attached, so the answer is verifiable, not just asserted.
- **Small teams** that want to cut down on manual standup prep — instead of
  everyone writing "yesterday/today/blockers" by hand, the bot derives it
  from what was actually committed and documented.
- **Engineering managers and PMs** overseeing multiple repos or workstreams,
  who need a single place to ask "what's the status across everything"
  rather than pinging five people or opening five dashboards.

Because retrieval is grounded in real docs and commits rather than a
model's memory, answers come with citations back to the exact source —
useful anywhere status has to be reported honestly, not just plausibly.
The long-term aim (see the roadmap) is a general-purpose tracker that can
sit across every project someone owns and answer "what's going on" for
any of them from one interface.

## What's included

- `app/` — FastAPI backend (project registry, ingestion, refresh, status,
  dashboard and query endpoints) and the background re-ingestion scheduler
- `db/` — SQLite schema + helpers (projects, documents, ingestion runs, status updates)
- `ingestion/` — doc ingestor (markdown/text folders), git ingestor (commit
  history), and the runner that refreshes a whole project in one step
- `rag/` — Chroma vector store wrapper, the retrieve-then-generate pipeline,
  the structured status generator, and the single Claude client wrapper (`llm.py`)
- `ui/` — Streamlit app: chat (per project or across all) and a status dashboard
- `sample_data/` — two tiny demo projects (docs + a git repo each)
- `tests/` — pytest suite (offline: hashing embedder, Claude stubbed out)
- `smoke_test.py` — end-to-end pipeline check (ingestion + retrieval, no API key needed)

## Setup

```bash
git clone https://github.com/utkarshgupta18/StandUp-Bot.git
cd StandUp-Bot
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

The first run downloads the embedding model (`all-MiniLM-L6-v2`, ~90MB,
via sentence-transformers) — needs internet once, then it's cached locally
and everything runs offline except the actual Claude API calls.

## Run it

Terminal 1 — API:
```bash
uvicorn app.main:app --reload
```

Terminal 2 — UI:
```bash
streamlit run ui/streamlit_app.py
```

Open the Streamlit URL it prints, add a project (name + a docs folder path
+ a git repo path; "Ingest right away" is on by default), then ask questions
in the chat box. Pick "(all projects)" in the sidebar to ask across every
project at once. The **Dashboard** tab shows each project's doc/commit
counts, last commit, last refresh, and its structured status.

To keep answers current without clicking anything, set
`REINGEST_INTERVAL_MINUTES` (e.g. `30`) in `.env`: the API then re-scans
every project's docs and pulls new commits on that interval, and
regenerates the structured status of any project that changed.

## Run the tests

```bash
pytest                                            # full suite, offline, no API key
EMBEDDING_BACKEND=hashing python smoke_test.py    # ingestion + retrieval on the demo data
```

## Try it with the included demo project

There are two demo projects: `demo-project` (`sample_data/docs` +
`sample_data/sample_repo`) and `invoice-service`
(`sample_data/second_project/docs` + `sample_data/second_project/repo`).
First set up their git histories (generated by a script rather than
committed as nested repos):
```bash
./sample_data/init_sample_repo.sh
```

```bash
curl -X POST http://localhost:8000/projects -H 'Content-Type: application/json' \
  -d '{"name":"demo-project","docs_path":"sample_data/docs","repo_path":"sample_data/sample_repo"}'
curl -X POST http://localhost:8000/projects -H 'Content-Type: application/json' \
  -d '{"name":"invoice-service","docs_path":"sample_data/second_project/docs","repo_path":"sample_data/second_project/repo"}'

# Ingest docs + git for every project (and generate status if an API key is set)
curl -X POST http://localhost:8000/ingest/all

# Ask about one project...
curl -X POST http://localhost:8000/query -H 'Content-Type: application/json' \
  -d '{"question":"What has been done and what is still pending?","project_id":1}'
# ...or across all of them
curl -X POST http://localhost:8000/query -H 'Content-Type: application/json' \
  -d '{"question":"Which projects have pending work or blockers?"}'

# Structured status and the dashboard
curl -X POST http://localhost:8000/projects/2/status
curl http://localhost:8000/dashboard
```

Or just open the Streamlit UI, create the same projects, and chat.

## API overview

| Method & path | What it does |
| --- | --- |
| `POST /projects`, `GET /projects`, `GET/DELETE /projects/{id}` | Project registry (delete also removes its vectors) |
| `POST /ingest/docs`, `POST /ingest/git` | Ingest one source for one project |
| `POST /projects/{id}/reingest` | Refresh one project: docs + git, then status if anything changed |
| `POST /ingest/all` | Refresh every project (what the scheduler runs) |
| `GET /projects/{id}/runs` | Ingestion run history (manual and scheduled) |
| `POST /projects/{id}/status`, `GET /projects/{id}/status` | Generate / read the latest structured status |
| `GET /dashboard` | Per-project counts, last commit, last refresh, latest status |
| `POST /query` | RAG Q&A; omit `project_id` to ask across all projects |
| `GET /scheduler`, `GET /health` | Scheduler state; API health and whether a key is configured |

`/projects/{id}/reingest` and `/ingest/all` take an optional
`?auto_status=false` to skip status regeneration.

## Notes on design choices

- **Embeddings**: Anthropic doesn't offer an embeddings endpoint, so this
  uses `sentence-transformers` (local, free, no extra API key) for
  embeddings and Claude only for generating the final answer. There's an
  `EMBEDDING_BACKEND=hashing` env var escape hatch that swaps in a
  deterministic, fully offline embedder — much lower quality, only meant
  for environments with no internet access (that's what `smoke_test.py`
  used to verify the pipeline in the sandbox this was built in). Leave it
  unset for real use.
- **Re-ingestion is cheap by design**: docs are only re-embedded if their
  content hash changed; commits are immutable so an already-ingested
  commit hash is always skipped. Re-running ingestion after new commits or
  doc edits only processes what's new.
- **Multi-project**: every chunk in Chroma carries a `project_id`.
  `/query` with a `project_id` scopes retrieval to one project. Omitting it
  queries each project separately and merges the results, so one project
  with lots of similar text can't crowd the others out of a cross-project
  answer. Re-ingestion also removes docs that were deleted from disk.
- **Structured status**: Claude reads a project's newest commits and its
  docs and returns JSON (via structured outputs) with a summary, a health
  flag, and done / in progress / next steps / blockers, each item citing a
  doc path or commit. It's stored in the `updates` table, shown on the
  dashboard, and included as extra context in Q&A so "what's pending"
  answers are consistent. Status is regenerated only when a refresh found
  changes, so scheduled runs don't spend tokens on idle projects.
- **Claude calls** all go through `rag/llm.py` (model `claude-opus-5-5` by
  default, `CLAUDE_EFFORT=medium`, server-side refusal fallback on for
  models that support it). Change the model or effort in `.env`.
- **git ingestion** embeds commit message + changed-files + insertion/deletion
  stats, not full diffs, to keep chunk volume sane on real repos. Full
  diffs can be added as a new `source_type` later if deeper "what changed
  and why" answers are needed.

## What's next (Phase 4, as needed)

See [`docs/project-plan.md`](docs/project-plan.md): a React frontend,
Postgres/pgvector, auth, and more source connectors (Slack, Linear/Jira,
Notion) — only if usage calls for them.
