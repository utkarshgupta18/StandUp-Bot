"""Test setup: isolated data dir, offline embeddings, and a stubbed Claude.

Env vars must be set before any project module is imported, because
app.config and rag.vector_store read them at import time.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="standup-bot-tests-"))
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["EMBEDDING_BACKEND"] = "hashing"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["REINGEST_INTERVAL_MINUTES"] = "0"
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from git import Actor, Repo  # noqa: E402

from db import database as db  # noqa: E402
from rag import llm  # noqa: E402
from rag.vector_store import delete_project_chunks  # noqa: E402

db.init_db()

AUTHOR = Actor("Test User", "test@example.com")


def _make_repo(src: Path, dest: Path, commits: list[tuple[list[str], str]]) -> Path:
    """Copy src's files into dest and commit them in the given groups."""
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
    repo = Repo.init(dest)
    for files, message in commits:
        repo.index.add(files)
        repo.index.commit(message, author=AUTHOR, committer=AUTHOR)
    return dest


@pytest.fixture(scope="session")
def sample_projects() -> dict:
    """Two demo projects (docs folder + git repo each) in a temp dir."""
    base = _TMP / "samples"
    demo_repo = _make_repo(
        ROOT / "sample_data" / "sample_repo",
        base / "demo_repo",
        [
            (["README.md"], "Initial commit: add README"),
            (["app/main.py"], "Add FastAPI app skeleton"),
            (["app/ingest.py"], "Add doc ingestion stub, pending git ingestion next"),
        ],
    )
    invoice_repo = _make_repo(
        ROOT / "sample_data" / "second_project" / "repo",
        base / "invoice_repo",
        [
            (["README.md"], "Initial commit: invoice service README"),
            (["src/invoices.py"], "Generate invoice PDFs from order data"),
            (["src/webhooks.py"], "Handle Stripe payment_succeeded webhook"),
            (["src/mailer.py"], "WIP: email invoices to customers, blocked on SMTP credentials"),
        ],
    )
    demo_docs = base / "demo_docs"
    invoice_docs = base / "invoice_docs"
    shutil.copytree(ROOT / "sample_data" / "docs", demo_docs)
    shutil.copytree(ROOT / "sample_data" / "second_project" / "docs", invoice_docs)
    return {
        "demo": {"name": "demo-project", "docs_path": str(demo_docs), "repo_path": str(demo_repo)},
        "invoice": {"name": "invoice-service", "docs_path": str(invoice_docs), "repo_path": str(invoice_repo)},
    }


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts with no projects, chunks, runs or updates."""
    for project in db.list_projects():
        delete_project_chunks(project["id"])
        db.delete_project(project["id"])
    yield


class FakeClaude:
    """Stands in for rag.llm.generate; records prompts and returns canned replies."""

    def __init__(self):
        self.calls: list[dict] = []
        self.status_reply: dict = {
            "summary": "Core pipeline works; email delivery is blocked.",
            "health": "at_risk",
            "done": [{"text": "Invoice PDF generation", "sources": ["status.md"]}],
            "in_progress": [{"text": "Email delivery", "sources": ["status.md"]}],
            "next_steps": [{"text": "Multi-currency support", "sources": ["status.md"]}],
            "blockers": [{"text": "Waiting on SMTP credentials", "sources": ["status.md"]}],
        }

    def __call__(self, system, user, *, json_schema=None, max_tokens=16000):
        self.calls.append({"system": system, "user": user, "json_schema": json_schema})
        if json_schema is not None:
            return json.dumps(self.status_reply)
        return "Stub answer."


@pytest.fixture
def fake_claude(monkeypatch) -> FakeClaude:
    fake = FakeClaude()
    monkeypatch.setattr(llm, "generate", fake)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    return fake


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def two_projects(client, sample_projects) -> dict:
    """Both demo projects registered through the API (not yet ingested)."""
    ids = {}
    for key, spec in sample_projects.items():
        r = client.post("/projects", json=spec)
        assert r.status_code == 200, r.text
        ids[key] = r.json()["id"]
    return ids
