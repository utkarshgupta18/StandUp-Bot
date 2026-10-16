"""Phase 4: ingest one external source (Slack/Linear/Jira/Notion) into the vector store.

Mirrors the doc ingestor: each connector Item becomes one document, chunked
and embedded, and skipped on later runs if its content hasn't changed.
Items that drop out of a source's window (old Slack days, issues no longer
matched) are kept, since "what happened earlier" is still useful history.
"""
from __future__ import annotations

import hashlib

from connectors import ConnectorError, get_connector
from db import database as db
from ingestion.chunker import chunk_text
from rag.vector_store import delete_chunks, upsert_chunks


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingest_source(source: dict) -> dict:
    """Fetch a source's items and embed new/changed ones.

    Returns {"ingested": [...], "skipped": [...], "errors": [...]}; raises
    ConnectorError if the source can't be fetched at all.
    """
    project_id = source["project_id"]
    kind = source["kind"]
    report = {"source_id": source["id"], "name": source["name"], "kind": kind,
              "ingested": [], "skipped": [], "errors": []}

    try:
        items = get_connector(kind).fetch(source["config"])
    except ConnectorError as e:
        db.mark_source_synced(source["id"], error=str(e))
        raise

    for item in items:
        text = f"{item.title}\n{item.text}".strip()
        content_hash = _hash(text)
        existing = db.get_document(project_id, kind, item.ref)
        if existing and existing["content_hash"] == content_hash:
            report["skipped"].append(item.ref)
            continue
        if existing and existing["chunk_ids"]:
            delete_chunks(existing["chunk_ids"].split(","))

        chunks = chunk_text(text)
        if not chunks:
            continue
        chunk_ids = [f"{kind}:{project_id}:{item.ref}:{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "project_id": project_id,
                "source_type": kind,
                "source_ref": item.ref,
                "source_id": source["id"],
                "chunk_index": i,
                "title": item.title,
                "url": item.url or "",
                "item_date": item.updated_at,
                "item_ts": item.timestamp,
            }
            for i in range(len(chunks))
        ]
        upsert_chunks(chunk_ids, chunks, metadatas)
        db.upsert_document(project_id, kind, item.ref, content_hash, chunk_ids, source_id=source["id"])
        report["ingested"].append(item.ref)

    db.mark_source_synced(source["id"])
    return report


def remove_source(source: dict) -> int:
    """Delete a source and everything ingested from it. Returns documents removed."""
    docs = db.list_source_documents(source["id"])
    for doc in docs:
        if doc["chunk_ids"]:
            delete_chunks(doc["chunk_ids"].split(","))
        db.delete_document(doc["project_id"], doc["source_type"], doc["source_ref"])
    db.delete_source(source["id"])
    return len(docs)
