"""Central configuration loaded from environment variables / .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
CHROMA_DIR = DATA_DIR / "chroma"
SQLITE_PATH = DATA_DIR / "tracker.db"

# Phase 4: storage backends.
# DATABASE_URL=postgresql://user:pass@host:5432/db switches metadata from
# SQLite to Postgres. VECTOR_BACKEND=pgvector stores embeddings in Postgres
# too (PGVECTOR_URL, defaulting to DATABASE_URL); the default is local Chroma.
DATABASE_URL = os.getenv("DATABASE_URL", "")
VECTOR_BACKEND = os.getenv("VECTOR_BACKEND", "chroma").strip().lower()
PGVECTOR_URL = os.getenv("PGVECTOR_URL", "") or DATABASE_URL

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
# Thinking depth / token spend: low | medium | high | xhigh | max. Leave empty
# to send no effort setting (needed for older models that reject it).
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium")
# Server-side refusal fallback (only sent for models that support it).
CLAUDE_REFUSAL_FALLBACK = _bool_env("CLAUDE_REFUSAL_FALLBACK", True)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# Chunking defaults
CHUNK_SIZE_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150
TOP_K = 6

# Phase 2: scheduled re-ingestion. 0 disables the background scheduler;
# on-demand re-ingestion via the API/UI always works.
REINGEST_INTERVAL_MINUTES = _int_env("REINGEST_INTERVAL_MINUTES", 0)

# Phase 3: structured status generation.
# Regenerate a project's status automatically after a re-ingestion run that
# picked up new docs/commits (only when an API key is configured).
STATUS_AUTO_GENERATE = _bool_env("STATUS_AUTO_GENERATE", True)
STATUS_MAX_COMMITS = _int_env("STATUS_MAX_COMMITS", 30)
STATUS_MAX_DOC_CHARS = _int_env("STATUS_MAX_DOC_CHARS", 20000)

# Phase 4: authentication. Off by default (single-user, local machine).
# With AUTH_ENABLED=true every endpoint except /health and /auth/login needs
# a bearer token; set AUTH_SECRET to a long random string so tokens survive
# restarts. ADMIN_USERNAME/ADMIN_PASSWORD create the first admin on startup
# if no users exist yet.
AUTH_ENABLED = _bool_env("AUTH_ENABLED", False)
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
AUTH_TOKEN_TTL_HOURS = _int_env("AUTH_TOKEN_TTL_HOURS", 24 * 7)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Browser origins allowed to call the API directly (comma-separated). Not
# needed when the React app is served by the API itself or via the dev proxy.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

# Phase 4: connector credentials (per-source settings live in the database).
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
LINEAR_API_KEY = os.getenv("LINEAR_API_KEY", "")
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
STATUS_MAX_ITEMS = _int_env("STATUS_MAX_ITEMS", 40)

DATA_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)
