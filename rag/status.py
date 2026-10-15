"""Phase 3: structured per-project status generation.

Instead of re-deriving "what's done / what's pending" from raw retrieval on
every question, Claude periodically reads a project's recent commits and its
docs and writes a structured status object (done, in progress, next steps,
blockers). It's stored in the `updates` table, shown on the dashboard, and
fed to the chatbot as extra context so answers are faster and consistent.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.config import STATUS_MAX_COMMITS, STATUS_MAX_DOC_CHARS
from db import database as db
from rag import llm
from rag.vector_store import get_chunks

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "sources"],
    "additionalProperties": False,
}

STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "health": {"type": "string", "enum": ["on_track", "at_risk", "blocked", "unknown"]},
        "done": {"type": "array", "items": _ITEM_SCHEMA},
        "in_progress": {"type": "array", "items": _ITEM_SCHEMA},
        "next_steps": {"type": "array", "items": _ITEM_SCHEMA},
        "blockers": {"type": "array", "items": _ITEM_SCHEMA},
    },
    "required": ["summary", "health", "done", "in_progress", "next_steps", "blockers"],
    "additionalProperties": False,
}

STATUS_SECTIONS = ("done", "in_progress", "next_steps", "blockers")

SYSTEM_PROMPT = """You maintain the status report for a software project. You are given \
the project's most recent git commits (newest first) and its documentation. Produce a \
structured status that a teammate could read before a standup.

How to fill each field:
- summary: two or three sentences on where the project stands right now.
- health: on_track, at_risk (slipping or significant open risks), blocked (an explicit \
blocker stops progress), or unknown (not enough information).
- done: work that is finished, per commits or docs.
- in_progress: work that has started but isn't finished.
- next_steps: work that is planned or explicitly pending and hasn't started.
- blockers: things that stop progress, as stated in the material. Leave empty if none are stated.

Use only the material provided. Every item needs at least one source: a doc path or a \
commit short-hash exactly as labelled in the material. Keep items short (one line each), \
merge duplicates, and prefer what the newest commits say when docs and commits disagree, \
since docs are often updated less often than code."""


def _commit_sort_key(meta: dict) -> float:
    if "commit_ts" in meta:
        return float(meta["commit_ts"])
    try:
        return datetime.fromisoformat(meta.get("commit_date", "")).timestamp()
    except ValueError:
        return 0.0


def _collect_context(project_id: int) -> tuple[str, list[str]]:
    """Build the prompt material: recent commits + docs, with source labels."""
    sections: list[str] = []
    source_refs: list[str] = []

    commits = get_chunks(project_id, "commit")
    commit_rows = sorted(
        zip(commits.get("documents") or [], commits.get("metadatas") or []),
        key=lambda pair: _commit_sort_key(pair[1]),
        reverse=True,
    )
    seen_commits: set[str] = set()
    commit_blocks: list[str] = []
    for text, meta in commit_rows:
        sha = meta["source_ref"]
        if sha in seen_commits:
            continue
        seen_commits.add(sha)
        if len(seen_commits) > STATUS_MAX_COMMITS:
            break
        commit_blocks.append(f"[commit:{sha[:10]}]\n{text}")
        source_refs.append(sha)
    if commit_blocks:
        sections.append("## Recent commits (newest first)\n\n" + "\n\n".join(commit_blocks))

    docs = get_chunks(project_id, "doc")
    doc_rows = sorted(
        zip(docs.get("documents") or [], docs.get("metadatas") or []),
        key=lambda pair: (pair[1]["source_ref"], pair[1].get("chunk_index", 0)),
    )
    doc_texts: dict[str, list[str]] = {}
    for text, meta in doc_rows:
        doc_texts.setdefault(meta["source_ref"], []).append(text)

    budget = STATUS_MAX_DOC_CHARS
    doc_blocks: list[str] = []
    for path, parts in doc_texts.items():
        if budget <= 0:
            break
        # Chunks overlap slightly; that's harmless for a summary prompt.
        body = "\n".join(parts)[:budget]
        budget -= len(body)
        doc_blocks.append(f"[doc:{path}]\n{body}")
        source_refs.append(path)
    if doc_blocks:
        sections.append("## Documentation\n\n" + "\n\n".join(doc_blocks))

    return "\n\n".join(sections), source_refs


def _normalise(status: dict) -> dict:
    """Defensive cleanup so downstream code can rely on the shape."""
    clean = {
        "summary": str(status.get("summary", "")).strip(),
        "health": status.get("health") if status.get("health") in STATUS_SCHEMA["properties"]["health"]["enum"] else "unknown",
    }
    for section in STATUS_SECTIONS:
        items = []
        for item in status.get(section) or []:
            if isinstance(item, str):
                item = {"text": item, "sources": []}
            text = str(item.get("text", "")).strip()
            if text:
                items.append({"text": text, "sources": [str(s) for s in item.get("sources") or []]})
        clean[section] = items
    return clean


def generate_status(project_id: int) -> dict:
    """Generate, store and return a fresh structured status for one project.

    Raises ValueError if the project has nothing ingested yet, RuntimeError if
    no API key is configured, and llm.LLMError if Claude's reply is unusable.
    """
    project = db.get_project(project_id)
    if not project:
        raise ValueError("Project not found")

    material, source_refs = _collect_context(project_id)
    if not material:
        raise ValueError(
            f"Nothing ingested yet for '{project['name']}'. Ingest its docs or git history first."
        )

    user_message = f"Project: {project['name']}\n\n{material}"
    raw = llm.generate(SYSTEM_PROMPT, user_message, json_schema=STATUS_SCHEMA)
    try:
        status = _normalise(json.loads(raw))
    except (json.JSONDecodeError, AttributeError) as e:
        raise llm.LLMError(f"Claude returned a status that isn't valid JSON: {e}")

    update_id = db.add_update(project_id, status["summary"], source_refs, status)
    return db.update_to_dict(db.get_update(update_id))


def format_status_for_prompt(project_name: str, update: dict) -> str:
    """Render a stored status as compact text for the Q&A prompt."""
    status = update.get("status") or {}
    lines = [
        f"Project: {project_name} (status generated {update.get('generated_at')}, health: {status.get('health', 'unknown')})",
        f"Summary: {update.get('summary', '')}",
    ]
    for section in STATUS_SECTIONS:
        items = status.get(section) or []
        label = section.replace("_", " ").title()
        if not items:
            lines.append(f"{label}: (none)")
            continue
        lines.append(f"{label}:")
        for item in items:
            refs = ", ".join(item.get("sources") or [])
            lines.append(f"  - {item['text']}" + (f" [{refs}]" if refs else ""))
    return "\n".join(lines)
