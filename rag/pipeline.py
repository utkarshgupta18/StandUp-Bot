"""RAG query pipeline: retrieve relevant chunks, ask Claude to answer with citations."""
from __future__ import annotations

import anthropic

from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, TOP_K
from rag.vector_store import query as vector_query

SYSTEM_PROMPT = """You are a project-tracking assistant. You answer questions about \
the state of a software project using only the provided context, which comes from \
project documentation and git commit history.

Rules:
- Answer only from the given context. If the context doesn't contain the answer, say so plainly.
- When you state something as done, in progress, or pending, ground it in a specific \
doc or commit from the context.
- Keep answers concise and factual. Prefer a short direct answer over a long one.
- At the end, list the sources you drew from (doc paths and/or commit short-hashes)."""


def _client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _format_context(results: dict) -> tuple[str, list[dict]]:
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]

    if not docs:
        return "(no matching context found)", []

    blocks = []
    sources = []
    for i, (doc, meta) in enumerate(zip(docs, metas)):
        source_type = meta.get("source_type")
        source_ref = meta.get("source_ref")
        label = f"[{i+1}] {source_type}:{source_ref}"
        blocks.append(f"{label}\n{doc}")
        sources.append({"source_type": source_type, "source_ref": source_ref})

    return "\n\n---\n\n".join(blocks), sources


def answer_question(question: str, project_id: int | None = None, top_k: int = TOP_K) -> dict:
    """Retrieve context (optionally scoped to one project) and generate an answer.

    project_id=None runs the query across all projects, for questions like
    "which projects have pending work?"
    """
    results = vector_query(question, top_k=top_k, project_id=project_id)
    context_text, sources = _format_context(results)

    if not sources:
        return {
            "answer": "I couldn't find any ingested documents or commits relevant to that question. "
            "Make sure the project has been ingested first.",
            "sources": [],
        }

    user_message = (
        f"Context:\n{context_text}\n\n"
        f"Question: {question}"
    )

    client = _client()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    answer_text = "".join(
        block.text for block in response.content if block.type == "text"
    )

    return {"answer": answer_text, "sources": sources}
