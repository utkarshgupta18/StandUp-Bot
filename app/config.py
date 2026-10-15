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

DATA_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)
