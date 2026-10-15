"""RAG query pipeline: retrieve relevant chunks, ask Claude to answer with citations."""
from __future__ import annotations

from app.config import TOP_K
from db import database as db
from rag import llm
from rag.status import format_status_for_prompt
from rag.vector_store import query as vector_query
from rag.vector_store import query_across_projects

SYSTEM_PROMPT = """You are a project-tracking assistant. You answer questions about \
the state of one or more software projects using only the provided context, which comes \
from project documentation, git commit history, and (when available) a structured status \
summary generated earlier from that same material.

Rules:
- Answer only from the given context. If the context doesn't contain the answer, say so plainly.
- When you state something as done, in progress, or pending, ground it in a specific \
doc or commit from the context.
- Structured status summaries are a quick overview; if a retrieved commit or doc is newer \
or more specific, prefer it.
- When the context covers several projects, say which project each point belongs to, and \
cover every project that's relevant to the question.
- Keep answers concise and factual. Prefer a short direct answer over a long one.
- At the end, list the sources you drew from (doc paths and/or commit short-hashes)."""


def _project_names() -> dict[int, str]:
    return {row["id"]: row["name"] for row in db.list_projects()}


def _format_context(results: dict, names: dict[int, str]) -> tuple[str, list[dict]]:
    docs = (results.get("documents") or [[]])[0] or []
    metas = (results.get("metadatas") or [[]])[0] or []

    blocks = []
    sources = []
    for i, (doc, meta) in enumerate(zip(docs, metas)):
        source_type = meta.get("source_type")
        source_ref = meta.get("source_ref")
        project_id = meta.get("project_id")
        project_name = names.get(project_id, f"project {project_id}")
        shown_ref = source_ref[:10] if source_type == "commit" else source_ref
        label = f"[{i+1}] project={project_name} {source_type}:{shown_ref}"
        blocks.append(f"{label}\n{doc}")
        sources.append({
            "project_id": project_id,
            "project_name": project_name,
            "source_type": source_type,
            "source_ref": source_ref,
        })

    return "\n\n---\n\n".join(blocks), sources


def _status_context(project_ids: list[int], names: dict[int, str]) -> tuple[str, list[dict]]:
    blocks = []
    sources = []
    for pid in project_ids:
        update = db.update_to_dict(db.latest_update(pid))
        if not update or not update.get("status"):
            continue
        blocks.append(format_status_for_prompt(names.get(pid, str(pid)), update))
        sources.append({
            "project_id": pid,
            "project_name": names.get(pid, str(pid)),
            "source_type": "status",
            "source_ref": f"status #{update['id']} ({update['generated_at']})",
        })
    return "\n\n".join(blocks), sources


def answer_question(question: str, project_id: int | None = None, top_k: int = TOP_K) -> dict:
    """Retrieve context (optionally scoped to one project) and generate an answer.

    project_id=None runs the query across all projects, for questions like
    "which projects have pending work?". Cross-project retrieval gives every
    project a share of the results, and each project's latest structured
    status (Phase 3) is included so no project is silently left out.
    """
    names = _project_names()
    if project_id is not None:
        project_ids = [project_id]
        results = vector_query(question, top_k=top_k, project_id=project_id)
    else:
        project_ids = list(names)
        results = query_across_projects(question, project_ids, top_k=top_k)

    chunk_text, sources = _format_context(results, names)
    status_text, status_sources = _status_context(project_ids, names)

    if not sources and not status_sources:
        return {
            "answer": "I couldn't find any ingested documents or commits relevant to that question. "
            "Make sure the project has been ingested first.",
            "sources": [],
        }

    parts = []
    if status_text:
        parts.append(f"Structured status summaries:\n\n{status_text}")
    if chunk_text:
        parts.append(f"Retrieved docs and commits:\n\n{chunk_text}")
    scope = names.get(project_id, "this project") if project_id is not None else "all projects"
    user_message = "\n\n===\n\n".join(parts) + f"\n\n===\n\nScope: {scope}\nQuestion: {question}"

    answer_text = llm.generate(SYSTEM_PROMPT, user_message)
    return {"answer": answer_text, "sources": status_sources + sources}
