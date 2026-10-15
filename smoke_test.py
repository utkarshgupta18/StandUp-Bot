"""End-to-end smoke test: two projects, ingest docs+git, scoped and cross-project retrieval.

Skips the Claude generation call (needs a real API key) and instead prints
the raw retrieved chunks, to verify the ingestion + embedding + retrieval
pipeline itself works correctly. Run ./sample_data/init_sample_repo.sh first.
For the full test suite (including the API, status generation and the
scheduler, with Claude stubbed out) run `pytest`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import database as db
from ingestion.doc_ingestor import ingest_docs
from ingestion.runner import reingest_project
from rag.vector_store import query as vector_query
from rag.vector_store import query_across_projects

BASE = Path(__file__).resolve().parent
PROJECTS = {
    "demo-project": (BASE / "sample_data" / "docs", BASE / "sample_data" / "sample_repo"),
    "invoice-service": (BASE / "sample_data" / "second_project" / "docs", BASE / "sample_data" / "second_project" / "repo"),
}

db.init_db()


def show(results):
    for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
        print(f"\n[project {meta['project_id']} {meta['source_type']}:{meta['source_ref'][:40]}] (distance={dist:.4f})")
        print(doc[:160])


ids = {}
for name, (docs_path, repo_path) in PROJECTS.items():
    project = db.get_project_by_name(name)
    if project:
        ids[name] = project["id"]
        print(f"Using existing project '{name}' id={ids[name]}")
    else:
        ids[name] = db.create_project(name, str(docs_path), str(repo_path))
        print(f"Created project '{name}' id={ids[name]}")

    print(f"\n--- Re-ingesting {name} (docs + git) ---")
    report = reingest_project(ids[name], auto_status=False)
    print({k: report[k] for k in ("docs", "git", "errors", "changed")})
    assert not report["errors"], report["errors"]

demo_id = ids["demo-project"]
print("\n--- Scoped query (demo-project): 'What has been done so far?' ---")
results = vector_query("What has been done so far on this project?", top_k=4, project_id=demo_id)
show(results)
assert {m["project_id"] for m in results["metadatas"][0]} == {demo_id}, "scoped query leaked another project"

print("\n--- Cross-project query: 'Which projects have pending work?' ---")
results = query_across_projects("Which projects have pending work or next steps?", list(ids.values()), top_k=6)
show(results)
assert {m["project_id"] for m in results["metadatas"][0]} == set(ids.values()), "cross-project query missed a project"

print("\n--- Re-running doc ingestion (should skip unchanged files) ---")
doc_report2 = ingest_docs(demo_id, str(PROJECTS["demo-project"][0]))
print(doc_report2)
assert len(doc_report2["ingested"]) == 0, "Expected unchanged files to be skipped"
print("\nSmoke test passed.")
