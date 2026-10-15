"""Phase 3: structured status generation, storage, dashboard, and use in Q&A."""
from rag.status import STATUS_SCHEMA


def test_generate_status_stores_structured_update(client, two_projects, fake_claude):
    pid = two_projects["invoice"]
    client.post(f"/projects/{pid}/reingest", params={"auto_status": False})

    r = client.post(f"/projects/{pid}/status")
    assert r.status_code == 200, r.text
    update = r.json()
    assert update["status"]["health"] == "at_risk"
    assert update["status"]["blockers"][0]["text"] == "Waiting on SMTP credentials"
    assert update["summary"].startswith("Core pipeline works")
    assert "status.md" in update["source_refs"]

    call = fake_claude.calls[-1]
    assert call["json_schema"] == STATUS_SCHEMA
    # Newest commit first, docs included, labelled so Claude can cite them.
    assert call["user"].index("WIP: email invoices") < call["user"].index("Initial commit")
    assert "[doc:status.md]" in call["user"]

    latest = client.get(f"/projects/{pid}/status").json()
    assert latest["id"] == update["id"]


def test_status_requires_ingested_content(client, two_projects, fake_claude):
    r = client.post(f"/projects/{two_projects['demo']}/status")
    assert r.status_code == 400
    assert client.get(f"/projects/{two_projects['demo']}/status").status_code == 404


def test_status_without_api_key_is_a_clear_400(client, two_projects):
    pid = two_projects["demo"]
    client.post(f"/projects/{pid}/reingest", params={"auto_status": False})
    r = client.post(f"/projects/{pid}/status")
    assert r.status_code == 400


def test_reingest_regenerates_status_only_when_something_changed(client, two_projects, fake_claude):
    pid = two_projects["demo"]
    first = client.post(f"/projects/{pid}/reingest").json()
    assert first["status_regenerated"] is True
    second = client.post(f"/projects/{pid}/reingest").json()
    assert second["status_regenerated"] is False
    assert sum(1 for c in fake_claude.calls if c["json_schema"]) == 1


def test_malformed_status_items_are_normalised(client, two_projects, fake_claude):
    pid = two_projects["demo"]
    client.post(f"/projects/{pid}/reingest", params={"auto_status": False})
    fake_claude.status_reply = {
        "summary": "  ok  ", "health": "great",
        "done": ["plain string item", {"text": ""}], "in_progress": None,
        "next_steps": [], "blockers": [],
    }
    status = client.post(f"/projects/{pid}/status").json()["status"]
    assert status["summary"] == "ok"
    assert status["health"] == "unknown"
    assert status["done"] == [{"text": "plain string item", "sources": []}]
    assert status["in_progress"] == []


def test_dashboard_lists_every_project(client, two_projects, fake_claude):
    client.post("/ingest/all")
    rows = {row["name"]: row for row in client.get("/dashboard").json()}
    assert set(rows) == {"demo-project", "invoice-service"}
    inv = rows["invoice-service"]
    assert inv["counts"] == {"doc": 2, "commit": 4}
    assert inv["last_commit_date"]
    assert inv["last_run"]["status"] == "ok"
    assert inv["latest_update"]["status"]["health"] == "at_risk"


def test_query_includes_latest_status_as_context(client, two_projects, fake_claude):
    client.post("/ingest/all")  # generates status for both via the fake
    r = client.post("/query", json={"question": "What's blocked?", "project_id": two_projects["invoice"]})
    assert r.status_code == 200
    prompt = fake_claude.calls[-1]["user"]
    assert "Structured status summaries" in prompt
    assert "Waiting on SMTP credentials" in prompt
    assert "Project: invoice-service" in prompt
    assert "Project: demo-project" not in prompt  # scoped query, scoped status
    assert r.json()["sources"][0]["source_type"] == "status"
