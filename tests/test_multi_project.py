"""Phase 2: multi-project ingestion, scoping, cross-project retrieval, re-ingestion."""
import subprocess
from pathlib import Path

from db import database as db
from rag.vector_store import get_chunks, query, query_across_projects


def _reingest(client, project_id, **params):
    r = client.post(f"/projects/{project_id}/reingest", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_reingest_tags_every_chunk_with_its_project(client, two_projects):
    for key, pid in two_projects.items():
        report = _reingest(client, pid, auto_status=False)
        assert report["errors"] == []
        assert report["changed"] is True
        assert report["docs"]["ingested"]
        assert report["git"]["ingested"]

    for pid in two_projects.values():
        chunks = get_chunks(pid)
        assert chunks["ids"], "project should have chunks"
        assert {m["project_id"] for m in chunks["metadatas"]} == {pid}

    assert db.document_counts(two_projects["demo"]) == {"doc": 2, "commit": 3}
    assert db.document_counts(two_projects["invoice"]) == {"doc": 2, "commit": 4}


def test_scoped_query_never_leaks_other_projects(client, two_projects):
    client.post("/ingest/all", params={"auto_status": False})
    res = query("SMTP credentials email invoices", top_k=10, project_id=two_projects["demo"])
    assert {m["project_id"] for m in res["metadatas"][0]} == {two_projects["demo"]}


def test_cross_project_retrieval_covers_every_project(client, two_projects):
    client.post("/ingest/all", params={"auto_status": False})
    res = query_across_projects("what is still pending next steps", list(two_projects.values()), top_k=6)
    assert {m["project_id"] for m in res["metadatas"][0]} == set(two_projects.values())


def test_rerun_is_cheap_and_records_runs(client, two_projects):
    pid = two_projects["invoice"]
    _reingest(client, pid, auto_status=False)
    second = _reingest(client, pid, auto_status=False)
    assert second["changed"] is False
    assert second["docs"]["ingested"] == []
    assert second["git"]["ingested"] == []

    runs = client.get(f"/projects/{pid}/runs").json()
    assert len(runs) == 2
    assert runs[0]["status"] == "ok"
    assert runs[0]["commits_ingested"] == 0
    assert runs[1]["commits_ingested"] == 4


def test_reingest_picks_up_new_commits_and_doc_changes(client, two_projects, sample_projects):
    pid = two_projects["demo"]
    _reingest(client, pid, auto_status=False)

    repo = Path(sample_projects["demo"]["repo_path"])
    (repo / "CHANGELOG.md").write_text("- added changelog\n")
    subprocess.run(["git", "add", "CHANGELOG.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e.com", "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", "Add changelog"],
        cwd=repo, check=True,
    )
    docs = Path(sample_projects["demo"]["docs_path"])
    (docs / "notes.md").write_text("# Notes\nRetro scheduled for Friday.\n")
    (docs / "architecture.md").unlink()

    report = _reingest(client, pid, auto_status=False)
    assert len(report["git"]["ingested"]) == 1
    assert report["docs"]["ingested"] == ["notes.md"]
    assert report["docs"]["removed"] == ["architecture.md"]

    refs = {m["source_ref"] for m in get_chunks(pid, "doc")["metadatas"]}
    assert refs == {"status.md", "notes.md"}
    assert db.get_document(pid, "doc", "architecture.md") is None


def test_missing_paths_are_reported_not_fatal(client):
    pid = client.post("/projects", json={"name": "broken", "docs_path": "/nope/docs", "repo_path": "/nope/repo"}).json()["id"]
    report = _reingest(client, pid, auto_status=False)
    assert len(report["errors"]) == 2
    runs = client.get(f"/projects/{pid}/runs").json()
    assert runs[0]["status"] == "error"

    r = client.post("/ingest/git", json={"project_id": pid})
    assert r.status_code == 400


def test_delete_project_removes_its_chunks_only(client, two_projects):
    client.post("/ingest/all", params={"auto_status": False})
    r = client.delete(f"/projects/{two_projects['demo']}")
    assert r.status_code == 200
    assert get_chunks(two_projects["demo"])["ids"] == []
    assert get_chunks(two_projects["invoice"])["ids"]
    assert client.get(f"/projects/{two_projects['demo']}").status_code == 404


def test_cross_project_query_sends_all_projects_to_claude(client, two_projects, fake_claude):
    client.post("/ingest/all", params={"auto_status": False})
    r = client.post("/query", json={"question": "Which projects have pending work?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "Stub answer."
    assert {s["project_name"] for s in body["sources"]} == {"demo-project", "invoice-service"}
    prompt = fake_claude.calls[-1]["user"]
    assert "project=demo-project" in prompt and "project=invoice-service" in prompt
    assert "Scope: all projects" in prompt


def test_query_without_api_key_is_a_clear_400(client, two_projects):
    client.post("/ingest/all", params={"auto_status": False})
    r = client.post("/query", json={"question": "status?", "project_id": two_projects["demo"]})
    assert r.status_code == 400
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_scheduler_run_once_refreshes_everything(two_projects):
    import asyncio

    from app.scheduler import ReingestScheduler

    sched = ReingestScheduler(interval_minutes=5)
    reports = asyncio.run(sched.run_once())
    assert {r["project_id"] for r in reports} == set(two_projects.values())
    assert sched.info()["runs"] == 1 and sched.info()["last_error"] is None
    for pid in two_projects.values():
        assert db.latest_ingestion_run(pid)["trigger"] == "scheduled"
