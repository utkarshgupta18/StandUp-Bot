"""End-to-end smoke test: create a project, ingest docs+git, run retrieval.

Skips the Claude generation call (needs a real API key) and instead prints
the raw retrieved chunks, to verify the ingestion + embedding + retrieval
pipeline itself works correctly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import database as db
from ingestion.doc_ingestor import ingest_docs
from ingestion.git_ingestor import ingest_git
from rag.vector_store import query as vector_query

BASE = Path(__file__).resolve().parent
DOCS_PATH = str(BASE / "sample_data" / "docs")
REPO_PATH = str(BASE / "sample_data" / "sample_repo")

db.init_db()

project = db.get_project_by_name("demo-project")
if project:
    project_id = project["id"]
    print(f"Using existing project id={project_id}")
else:
    project_id = db.create_project("demo-project", DOCS_PATH, REPO_PATH)
    print(f"Created project id={project_id}")

print("\n--- Ingesting docs ---")
doc_report = ingest_docs(project_id, DOCS_PATH)
print(doc_report)

print("\n--- Ingesting git history ---")
git_report = ingest_git(project_id, REPO_PATH)
print(git_report)

print("\n--- Querying: 'What has been done so far?' ---")
results = vector_query("What has been done so far on this project?", top_k=4, project_id=project_id)
for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
    print(f"\n[{meta['source_type']}:{meta['source_ref']}] (distance={dist:.4f})")
    print(doc[:200])

print("\n--- Querying: 'What is still pending?' ---")
results = vector_query("What is still pending or next steps?", top_k=4, project_id=project_id)
for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
    print(f"\n[{meta['source_type']}:{meta['source_ref']}] (distance={dist:.4f})")
    print(doc[:200])

print("\n--- Re-running doc ingestion (should skip unchanged files) ---")
doc_report2 = ingest_docs(project_id, DOCS_PATH)
print(doc_report2)
assert len(doc_report2["ingested"]) == 0, "Expected unchanged files to be skipped"
print("\nSmoke test passed.")
