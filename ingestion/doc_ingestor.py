"""Ingest a folder of markdown/text docs for a project into the vector store."""
from __future__ import annotations

import hashlib
from pathlib import Path

from db import database as db
from ingestion.chunker import chunk_text
from rag.vector_store import delete_chunks, upsert_chunks

DOC_EXTENSIONS = {".md", ".markdown", ".txt", ".rst"}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingest_docs(project_id: int, docs_path: str) -> dict:
    """Walk docs_path, chunk+embed any changed files, skip unchanged ones.

    Returns a small report dict: {"ingested": [...], "skipped": [...], "errors": [...]}
    """
    root = Path(docs_path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"docs_path does not exist: {root}")

    report = {"ingested": [], "skipped": [], "errors": []}

    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in DOC_EXTENSIONS]
    for path in files:
        rel_path = str(path.relative_to(root))
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:  # unreadable file, skip but record it
            report["errors"].append(f"{rel_path}: {e}")
            continue

        content_hash = _hash(content)
        existing = db.get_document(project_id, "doc", rel_path)
        if existing and existing["content_hash"] == content_hash:
            report["skipped"].append(rel_path)
            continue

        # Re-ingesting a changed file: drop its old chunks first.
        if existing and existing["chunk_ids"]:
            delete_chunks(existing["chunk_ids"].split(","))

        chunks = chunk_text(content)
        if not chunks:
            continue

        chunk_ids = [f"doc:{project_id}:{rel_path}:{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "project_id": project_id,
                "source_type": "doc",
                "source_ref": rel_path,
                "chunk_index": i,
            }
            for i in range(len(chunks))
        ]
        upsert_chunks(chunk_ids, chunks, metadatas)
        db.upsert_document(project_id, "doc", rel_path, content_hash, chunk_ids)
        report["ingested"].append(rel_path)

    return report
