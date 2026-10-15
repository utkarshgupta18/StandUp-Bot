"""Local persistent Chroma vector store, scoped by project via metadata."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime

import chromadb
from chromadb.utils import embedding_functions

from app.config import CHROMA_DIR, EMBEDDING_MODEL

_client = None
_collection = None
_embedding_fn = None

# "sentence-transformers" (default, real semantic embeddings, needs internet
# the first time to download the model) or "hashing" (deterministic, fully
# offline, much lower quality -- only meant for environments with no
# internet access, e.g. CI or a sandboxed smoke test).
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "sentence-transformers")


class _HashingEmbeddingFunction:
    """Deterministic bag-of-words hashing embedding. No network, no model.

    Good enough to prove the ingestion/retrieval plumbing works when a real
    embedding model can't be downloaded; swap back to sentence-transformers
    for actual semantic search quality.
    """

    _DIM = 384

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in input]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self._DIM
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            idx = int(hashlib.md5(word.encode()).hexdigest(), 16) % self._DIM
            vec[idx] += 1.0
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def _get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
    return _client


def _get_embedding_fn():
    global _embedding_fn
    if _embedding_fn is None:
        if EMBEDDING_BACKEND == "hashing":
            _embedding_fn = _HashingEmbeddingFunction()
        else:
            _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL
            )
    return _embedding_fn


def get_collection():
    """A single Chroma collection holding chunks from every project.

    Every chunk carries a `project_id` metadata field, so retrieval can be
    scoped to one project (`where={"project_id": id}`) or run unscoped
    across all projects for cross-project questions.
    """
    global _collection
    if _collection is None:
        _collection = _get_client().get_or_create_collection(
            name="project_chunks",
            embedding_function=_get_embedding_fn(),
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def upsert_chunks(ids: list[str], texts: list[str], metadatas: list[dict]) -> None:
    if not ids:
        return
    get_collection().upsert(ids=ids, documents=texts, metadatas=metadatas)


def delete_chunks(ids: list[str]) -> None:
    if not ids:
        return
    get_collection().delete(ids=ids)


def query(
    question: str,
    top_k: int = 6,
    project_id: int | None = None,
) -> dict:
    where = {"project_id": project_id} if project_id is not None else None
    return get_collection().query(
        query_texts=[question],
        n_results=top_k,
        where=where,
    )


def delete_project_chunks(project_id: int) -> None:
    """Drop every chunk belonging to one project (used when a project is deleted)."""
    get_collection().delete(where={"project_id": project_id})


def get_chunks(project_id: int, source_type: str | None = None) -> dict:
    """Fetch all stored chunks for a project (no similarity ranking).

    Used by the status generator, which wants recent commits by date rather
    than by relevance to a question.
    """
    where: dict = {"project_id": project_id}
    if source_type is not None:
        where = {"$and": [{"project_id": project_id}, {"source_type": source_type}]}
    return get_collection().get(where=where, include=["documents", "metadatas"])


def query_across_projects(question: str, project_ids: list[int], top_k: int = 6) -> dict:
    """Cross-project retrieval that gives every project a fair share of results.

    A single unscoped query lets one project with lots of similar chunks crowd
    the others out, which breaks questions like "which projects have pending
    work?". Instead, query each project separately and merge, keeping the
    standard Chroma result shape so callers don't need to care.
    """
    merged = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
    if not project_ids:
        return merged
    per_project = max(2, -(-top_k // len(project_ids)))  # ceil(top_k / n), at least 2
    for pid in project_ids:
        res = query(question, top_k=per_project, project_id=pid)
        for key in merged:
            merged[key][0].extend((res.get(key) or [[]])[0] or [])
    return merged


def latest_commit_date(project_id: int) -> str | None:
    """ISO date of the newest ingested commit for a project, if any."""
    res = get_collection().get(
        where={"$and": [{"project_id": project_id}, {"source_type": "commit"}]},
        include=["metadatas"],
    )
    dates = [m.get("commit_date") for m in res.get("metadatas") or [] if m.get("commit_date")]
    if not dates:
        return None
    return max(dates, key=lambda d: datetime.fromisoformat(d).timestamp())
