"""Vector store for chunk embeddings, scoped by project via metadata.

Two backends behind one set of module-level functions:
- "chroma" (default): local, file-based Chroma under DATA_DIR. No infra.
- "pgvector": a `chunks` table in Postgres with a pgvector column, for when
  data volume or several API instances outgrow a local file.
Pick with VECTOR_BACKEND. Results always use Chroma's result shape
({"ids": [[...]], "documents": [[...]], "metadatas": [[...]], "distances": [[...]]}
for queries), so callers don't care which backend is active.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime

from app.config import CHROMA_DIR, EMBEDDING_MODEL, PGVECTOR_URL, VECTOR_BACKEND

# "sentence-transformers" (default, real semantic embeddings, needs internet
# the first time to download the model) or "hashing" (deterministic, fully
# offline, much lower quality -- only meant for environments with no
# internet access, e.g. CI or a sandboxed smoke test).
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "sentence-transformers")

_embedding_fn = None
_backend = None


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


def _get_embedding_fn():
    global _embedding_fn
    if _embedding_fn is None:
        if EMBEDDING_BACKEND == "hashing":
            _embedding_fn = _HashingEmbeddingFunction()
        else:
            from chromadb.utils import embedding_functions

            _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL
            )
    return _embedding_fn


def _embed(texts: list[str]) -> list[list[float]]:
    return [[float(x) for x in vec] for vec in _get_embedding_fn()(texts)]


# ------------------------------------------------------------------ chroma
class ChromaBackend:
    """A single Chroma collection holding chunks from every project."""

    def __init__(self):
        import chromadb

        client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
        self.collection = client.get_or_create_collection(
            name="project_chunks",
            embedding_function=_get_embedding_fn(),
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, ids, texts, metadatas):
        self.collection.upsert(ids=ids, documents=texts, metadatas=metadatas)

    def delete(self, ids):
        self.collection.delete(ids=ids)

    def delete_project(self, project_id):
        self.collection.delete(where={"project_id": project_id})

    def query(self, question, top_k, project_id):
        where = {"project_id": project_id} if project_id is not None else None
        return self.collection.query(query_texts=[question], n_results=top_k, where=where)

    def get(self, project_id, source_type):
        where: dict = {"project_id": project_id}
        if source_type is not None:
            where = {"$and": [{"project_id": project_id}, {"source_type": source_type}]}
        return self.collection.get(where=where, include=["documents", "metadatas"])


# ---------------------------------------------------------------- pgvector
class PgVectorBackend:
    """Chunks in a Postgres table with a pgvector embedding column.

    The table is created on first use with the embedding model's dimension.
    Cosine distance (`<=>`) matches the Chroma backend's "cosine" space.
    """

    def __init__(self, url: str):
        if not url:
            raise RuntimeError("VECTOR_BACKEND=pgvector needs PGVECTOR_URL or DATABASE_URL set to a Postgres URL")
        self.url = url
        dim = len(_embed(["dimension probe"])[0])
        with self._conn() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    id          TEXT PRIMARY KEY,
                    project_id  INTEGER NOT NULL,
                    source_type TEXT NOT NULL,
                    document    TEXT NOT NULL,
                    metadata    JSONB NOT NULL,
                    embedding   vector({dim}) NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_id, source_type)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)"
            )

    def _conn(self):
        import psycopg

        return psycopg.connect(self.url)

    @staticmethod
    def _vec(values: list[float]) -> str:
        return "[" + ",".join(f"{v:.7g}" for v in values) + "]"

    def upsert(self, ids, texts, metadatas):
        vectors = _embed(texts)
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO chunks (id, project_id, source_type, document, metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (id) DO UPDATE SET project_id = excluded.project_id,
                    source_type = excluded.source_type, document = excluded.document,
                    metadata = excluded.metadata, embedding = excluded.embedding
                """,
                [
                    (cid, meta["project_id"], meta["source_type"], text, json.dumps(meta), self._vec(vec))
                    for cid, text, meta, vec in zip(ids, texts, metadatas, vectors)
                ],
            )

    def delete(self, ids):
        with self._conn() as conn:
            conn.execute("DELETE FROM chunks WHERE id = ANY(%s)", (list(ids),))

    def delete_project(self, project_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM chunks WHERE project_id = %s", (project_id,))

    def query(self, question, top_k, project_id):
        qvec = self._vec(_embed([question])[0])
        sql = "SELECT id, document, metadata, embedding <=> %s::vector AS distance FROM chunks"
        params: list = [qvec]
        if project_id is not None:
            sql += " WHERE project_id = %s"
            params.append(project_id)
        sql += " ORDER BY distance LIMIT %s"
        params.append(top_k)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {
            "ids": [[r[0] for r in rows]],
            "documents": [[r[1] for r in rows]],
            "metadatas": [[r[2] for r in rows]],
            "distances": [[float(r[3]) for r in rows]],
        }

    def get(self, project_id, source_type):
        sql = "SELECT id, document, metadata FROM chunks WHERE project_id = %s"
        params: list = [project_id]
        if source_type is not None:
            sql += " AND source_type = %s"
            params.append(source_type)
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY id", params).fetchall()
        return {
            "ids": [r[0] for r in rows],
            "documents": [r[1] for r in rows],
            "metadatas": [r[2] for r in rows],
        }


def _get_backend():
    global _backend
    if _backend is None:
        if VECTOR_BACKEND == "pgvector":
            _backend = PgVectorBackend(PGVECTOR_URL)
        elif VECTOR_BACKEND == "chroma":
            _backend = ChromaBackend()
        else:
            raise RuntimeError(f"Unknown VECTOR_BACKEND '{VECTOR_BACKEND}' (use 'chroma' or 'pgvector')")
    return _backend


# ------------------------------------------------------------ public API
def upsert_chunks(ids: list[str], texts: list[str], metadatas: list[dict]) -> None:
    if ids:
        _get_backend().upsert(ids, texts, metadatas)


def delete_chunks(ids: list[str]) -> None:
    if ids:
        _get_backend().delete(ids)


def delete_project_chunks(project_id: int) -> None:
    """Drop every chunk belonging to one project (used when a project is deleted)."""
    _get_backend().delete_project(project_id)


def query(question: str, top_k: int = 6, project_id: int | None = None) -> dict:
    return _get_backend().query(question, top_k, project_id)


def get_chunks(project_id: int, source_type: str | None = None) -> dict:
    """Fetch all stored chunks for a project (no similarity ranking).

    Used by the status generator, which wants recent commits by date rather
    than by relevance to a question.
    """
    return _get_backend().get(project_id, source_type)


def query_across_projects(question: str, project_ids: list[int], top_k: int = 6) -> dict:
    """Cross-project retrieval that gives every project a fair share of results.

    A single unscoped query lets one project with lots of similar chunks crowd
    the others out, which breaks questions like "which projects have pending
    work?". Instead, query each project separately and merge.
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
    res = get_chunks(project_id, "commit")
    dates = [m.get("commit_date") for m in res.get("metadatas") or [] if m.get("commit_date")]
    if not dates:
        return None
    return max(dates, key=lambda d: datetime.fromisoformat(d).timestamp())
