# StandUp Bot

A RAG-powered chatbot that gives you your own project standup, on demand.
Ask it what's been done, what's still pending, or what the latest update
is on a project, and it answers by retrieving from that project's docs and
git commit history — with sources cited back to the exact doc or commit.

Built as a multi-project tracker from the start: point it at more than one
project and it can answer questions scoped to one of them, or across all
of them at once.

**Status:** all planned phases (0–4) are done: single- and multi-project
ingestion and Q&A, cross-project questions ("which projects have pending
work?"), on-demand and scheduled re-ingestion, Claude-generated structured
status (done / in progress / next steps / blockers), a React web app,
optional Postgres + pgvector storage, optional login with per-project
access, and Slack / Linear / Jira / Notion connectors. See
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

- `app/` — FastAPI backend (projects, ingestion, connectors, status,
  dashboard, query, users), the background re-ingestion scheduler, auth
  (`auth.py`) and the user-management CLI (`manage.py`)
- `web/` — React + TypeScript web app (Vite), served by the API at `/app/`
- `db/` — schema + helpers for SQLite or Postgres (projects, documents,
  sources, ingestion runs, status updates, users)
- `ingestion/` — doc ingestor (markdown/text folders), git ingestor (commit
  history), connector-source ingestor, and the runner that refreshes a
  whole project in one step
- `connectors/` — Slack, Linear, Jira and Notion connectors
- `rag/` — vector store (Chroma or pgvector), the retrieve-then-generate
  pipeline, the structured status generator, and the Claude client wrapper (`llm.py`)
- `ui/` — the original Streamlit app, kept as a lightweight alternative UI
- `Dockerfile`, `docker-compose.yml` — one image for API + web app, plus Postgres with pgvector
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

Build the web app once (needs Node 18+), then start the API, which serves
it:
```bash
cd web && npm ci && npm run build && cd ..
uvicorn app.main:app --reload
```

Open http://localhost:8000/ (it redirects to `/app/`). Under **Projects &
sources**, add a project (name + a docs folder path + a git repo path;
"Ingest right away" is on by default) and optionally connect Slack,
Linear, Jira or Notion sources. Then ask questions in **Chat**. Pick
"All projects" in the top bar to ask across every project at once. The
**Dashboard** shows each project's counts, last commit, last refresh,
and its structured status.

While working on the frontend, `cd web && npm run dev` serves it with hot
reload at http://localhost:5173/app/ and proxies API calls to port 8000.

The Streamlit UI still works too: `streamlit run ui/streamlit_app.py`
(set `STANDUP_API_TOKEN` in `.env` if auth is on).

To keep answers current without clicking anything, set
`REINGEST_INTERVAL_MINUTES` (e.g. `30`) in `.env`: the API then re-scans
every project's docs and pulls new commits on that interval, and
regenerates the structured status of any project that changed.

## Run with Docker (Postgres + pgvector)

```bash
cp .env.example .env     # set ANTHROPIC_API_KEY, and auth/connector settings if wanted
./sample_data/init_sample_repo.sh
docker compose up --build
```

This runs Postgres with pgvector and the API (with the web app) at
http://localhost:8000/. Docs folders and repos have to be visible inside
the container: they're mounted read-only from `PROJECTS_DIR` (default
`./sample_data`) at `/projects`, so the demo projects use paths like
`/projects/docs` and `/projects/sample_repo`.

## Storage backends

| Setting | Metadata | Embeddings |
| --- | --- | --- |
| default | SQLite file in `DATA_DIR` | Chroma in `DATA_DIR` |
| `DATABASE_URL=postgresql://...` | Postgres | Chroma |
| plus `VECTOR_BACKEND=pgvector` | Postgres | pgvector table in the same database |

Tables are created on startup. Existing SQLite databases from earlier
versions are migrated in place. Switching backends doesn't copy data:
point the new backend at your projects and refresh them to re-ingest.

## Auth and access control

Auth is off by default, which suits one person on their own machine.
To share an instance, set `AUTH_ENABLED=true` and a long random
`AUTH_SECRET`, then create the first admin with
`ADMIN_USERNAME`/`ADMIN_PASSWORD` in `.env` or with the CLI:

```bash
python -m app.manage create-user alice --role admin   # prompts for a password
python -m app.manage create-user bob                   # viewer
python -m app.manage grant bob invoice-service         # projects bob can see
python -m app.manage create-token alice --days 365     # token for scripts / Streamlit
```

- **Admins** can do everything: manage projects, sources and users, run
  refreshes, and generate status.
- **Viewers** can read the dashboard and status and use chat, but only for
  the projects they've been granted. Projects they can't see return 404,
  and "all projects" questions only search their projects.
- Admins also manage users in the web app under **Users**.

Tokens are HMAC-signed and expire after `AUTH_TOKEN_TTL_HOURS` (7 days by
default). Each request re-reads the user, so deleting a user or changing
their access takes effect immediately.

**Login rate limiting.** Failed sign-ins are capped per client IP (20) and
per username (10) within a 15-minute sliding window. Past either cap,
`/auth/login` returns `429` with a `Retry-After` header, even for the right
password, and doesn't check the password at all until older failures leave the
window. Successful sign-ins aren't counted, and one clears that username's
failures. Tune or disable the caps with `LOGIN_RATE_LIMIT_WINDOW_SECONDS`,
`LOGIN_MAX_FAILURES_PER_IP` and `LOGIN_MAX_FAILURES_PER_USER` (`0` turns a cap
off). Things to know:

- Counters are kept in memory per API process. They reset on restart, and
  several workers or replicas each count separately.
- The per-username cap means someone who knows a username can keep that
  account locked out by failing on purpose. If that matters more to you than
  password guessing, set `LOGIN_MAX_FAILURES_PER_USER=0` and rely on the
  per-IP cap.
- Behind a reverse proxy, every request appears to come from the proxy, so all
  users would share one per-IP counter. Tell uvicorn to trust the proxy's
  `X-Forwarded-For`: `uvicorn app.main:app --proxy-headers --forwarded-allow-ips=<proxy ip>`
  (or set `FORWARDED_ALLOW_IPS` in the container's environment).

## Connectors

Connect a project to Slack, Linear, Jira or Notion under **Projects &
sources** (or `POST /projects/{id}/sources`). Credentials go in `.env`;
per-source settings (which channel, team, JQL or pages) are stored with
the project.

| Kind | Credentials | Source settings | What's ingested |
| --- | --- | --- | --- |
| Slack | `SLACK_BOT_TOKEN` (scopes `channels:history`, `channels:read`, `users:read`; invite the bot to the channel) | `channel` (ID), `days` (14) | one document per channel-day, with thread replies |
| Linear | `LINEAR_API_KEY` | `team_key`, optional `project`, `days` (90), `max_issues` | one document per issue: state, assignee, priority, labels, description |
| Jira Cloud | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | `project_key` or `jql`, `days` (90), `max_issues` | one document per issue, rich-text description flattened |
| Notion | `NOTION_TOKEN` (share pages/databases with the integration) | `page_ids` and/or `database_ids` (ids or URLs) | one document per page, with properties and block text |

Sources sync with every refresh, manual or scheduled. Unchanged items are
skipped, and items that leave a source's window are kept as history.
Their content feeds the structured status (as "issues, messages and
pages, newest first") and chat answers, with links back to the issue or
page. A source whose credentials are missing or wrong reports an error on
the dashboard, and the rest of the refresh still runs.

## Run the tests

```bash
pytest                                            # full suite, offline, no API key
EMBEDDING_BACKEND=hashing python smoke_test.py    # ingestion + retrieval on the demo data

# the same suite against Postgres + pgvector (needs a database with the vector extension available)
STANDUP_TEST_DATABASE_URL=postgresql://postgres@localhost:5432/standup_test pytest

cd web && npm run typecheck                       # frontend type-check
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
| `POST /projects`, `GET /projects`, `GET/PATCH/DELETE /projects/{id}` | Project registry (delete also removes its vectors) |
| `POST /ingest/docs`, `POST /ingest/git` | Ingest one source for one project |
| `POST /projects/{id}/reingest` | Refresh one project: docs, git and sources, then status if anything changed |
| `POST /ingest/all` | Refresh every project (what the scheduler runs) |
| `GET /projects/{id}/runs` | Ingestion run history (manual and scheduled) |
| `POST /projects/{id}/status`, `GET /projects/{id}/status` | Generate / read the latest structured status |
| `GET /dashboard` | Per-project counts, last commit, last refresh, latest status |
| `POST /query` | RAG Q&A; omit `project_id` to ask across all (visible) projects |
| `GET /connectors` | Available connector kinds, their settings, and whether credentials are set |
| `GET/POST /projects/{id}/sources`, `POST /sources/{id}/sync`, `DELETE /sources/{id}` | Connector sources for a project |
| `GET /auth/config`, `POST /auth/login`, `GET /auth/me` | Auth (login returns a bearer token) |
| `GET/POST /users`, `PATCH/DELETE /users/{id}` | User management (admins only) |
| `GET /scheduler`, `GET /health` | Scheduler state; health, storage backends, and whether a key is configured |

`/projects/{id}/reingest` and `/ingest/all` take an optional
`?auto_status=false` to skip status regeneration. With auth on, send
`Authorization: Bearer <token>`. Every write endpoint needs an admin.

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
- **Multi-project**: every chunk in the vector store carries a `project_id`.
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

## What's next

Every phase in [`docs/project-plan.md`](docs/project-plan.md) is done.
Ideas for later, if usage calls for them: SSO (OIDC) instead of local
passwords, webhooks from Slack/Linear/Jira for near-real-time updates
instead of polling, embedding full code diffs, and a hosted vector
database for very large deployments.
