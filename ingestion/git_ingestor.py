"""Ingest git commit history for a project's repo into the vector store.

Each commit becomes one source document: its message plus a compact stat
summary of files changed. This is enough for "what changed and why"
questions without embedding full diffs (which would blow up chunk volume
fast on any real repo). Full diffs can be added later as a source_type if
Phase 3 needs deeper "what changed" answers.
"""
from __future__ import annotations

import hashlib

from git import Repo

from db import database as db
from ingestion.chunker import chunk_text
from rag.vector_store import delete_chunks, upsert_chunks


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _commit_to_text(commit) -> str:
    stats = commit.stats.total
    files_changed = ", ".join(list(commit.stats.files.keys())[:20])
    return (
        f"Commit {commit.hexsha[:10]} by {commit.author.name} on "
        f"{commit.committed_datetime.isoformat()}\n"
        f"Message: {commit.message.strip()}\n"
        f"Files changed ({stats['files']}): {files_changed}\n"
        f"Insertions: {stats['insertions']}, Deletions: {stats['deletions']}"
    )


def ingest_git(project_id: int, repo_path: str, max_commits: int = 500) -> dict:
    """Walk commit history (most recent first), chunk+embed new/changed commits.

    Commits are immutable once made, so a commit already ingested with the
    same hash never needs re-embedding -- this makes re-ingestion cheap:
    only genuinely new commits since the last run get processed.
    """
    repo = Repo(repo_path)
    if repo.bare:
        raise ValueError(f"{repo_path} is a bare repo with no working history")

    report = {"ingested": [], "skipped": [], "errors": []}

    try:
        commits = list(repo.iter_commits(max_count=max_commits))
    except Exception as e:
        raise ValueError(f"Could not read commit history for {repo_path}: {e}")

    for commit in commits:
        sha = commit.hexsha
        try:
            text = _commit_to_text(commit)
        except Exception as e:
            report["errors"].append(f"{sha[:10]}: {e}")
            continue

        content_hash = _hash(text)
        existing = db.get_document(project_id, "commit", sha)
        if existing and existing["content_hash"] == content_hash:
            report["skipped"].append(sha[:10])
            continue

        if existing and existing["chunk_ids"]:
            delete_chunks(existing["chunk_ids"].split(","))

        chunks = chunk_text(text)
        if not chunks:
            continue

        chunk_ids = [f"commit:{project_id}:{sha}:{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "project_id": project_id,
                "source_type": "commit",
                "source_ref": sha,
                "chunk_index": i,
                "commit_date": commit.committed_datetime.isoformat(),
            }
            for i in range(len(chunks))
        ]
        upsert_chunks(chunk_ids, chunks, metadatas)
        db.upsert_document(project_id, "commit", sha, content_hash, chunk_ids)
        report["ingested"].append(sha[:10])

    return report
