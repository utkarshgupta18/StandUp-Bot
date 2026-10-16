"""Phase 4: Slack / Linear / Jira / Notion connectors and project sources.

HTTP is faked with handlers that return payloads shaped like each real API,
so these run offline.
"""
import time
from urllib.parse import urlsplit

import pytest

import connectors.base
from app import config as cfg
from connectors import ConnectorError, get_connector
from connectors.jira import adf_to_text
from connectors.notion import notion_id
from db import database as db
from rag.vector_store import get_chunks


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    """requests.Session stand-in: routes (METHOD, url-without-query) to handlers."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, **kwargs):
        path = urlsplit(url)._replace(query="").geturl()
        self.calls.append({"method": method, "url": path, **kwargs})
        handler = self.routes.get((method, path))
        if handler is None:
            return FakeResponse({"error": f"no route {method} {path}"}, 404)
        result = handler(**kwargs)
        return result if isinstance(result, FakeResponse) else FakeResponse(result)


@pytest.fixture
def fake_http(monkeypatch):
    holder = {}

    def install(routes):
        holder["session"] = FakeSession(routes)
        monkeypatch.setattr(connectors.base, "new_session", lambda: holder["session"])
        return holder["session"]

    return install


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(cfg, "LINEAR_API_KEY", "lin_api_test")
    monkeypatch.setattr(cfg, "JIRA_BASE_URL", "https://acme.atlassian.net")
    monkeypatch.setattr(cfg, "JIRA_EMAIL", "me@acme.test")
    monkeypatch.setattr(cfg, "JIRA_API_TOKEN", "jira-token")
    monkeypatch.setattr(cfg, "NOTION_TOKEN", "secret_notion")


# ------------------------------------------------------------ fake APIs
NOW = time.time()


def slack_routes():
    base = "https://slack.com/api/"
    t1, t2, t3 = NOW - 3600, NOW - 1800, NOW - 90000  # two today-ish, one ~a day earlier
    history_pages = [
        {"ok": True, "messages": [
            {"ts": f"{t2:.6f}", "user": "U1", "text": "Deploy is blocked on SMTP creds", "reply_count": 1},
            {"ts": f"{t1:.6f}", "user": "U2", "text": "PDF generation shipped"},
        ], "has_more": True, "response_metadata": {"next_cursor": "c2"}},
        {"ok": True, "messages": [
            {"ts": f"{t3:.6f}", "user": "U1", "text": "Kickoff for invoice emails"},
            {"ts": f"{t3 + 1:.6f}", "subtype": "channel_join", "user": "U3", "text": "joined"},
        ], "has_more": False},
    ]

    def history(params, headers, **_):
        assert headers["Authorization"] == "Bearer xoxb-test"
        return history_pages[1] if params.get("cursor") == "c2" else history_pages[0]

    def replies(params, **_):
        return {"ok": True, "messages": [
            {"ts": params["ts"], "user": "U1", "text": "parent"},
            {"ts": f"{float(params['ts']) + 5:.6f}", "user": "U2", "text": "Ops says Friday"},
        ], "has_more": False}

    users = {"U1": {"name": "ana", "profile": {"display_name": "Ana"}}, "U2": {"name": "ben", "real_name": "Ben K", "profile": {}}}
    return {
        ("GET", base + "conversations.info"): lambda **_: {"ok": True, "channel": {"name": "billing"}},
        ("GET", base + "conversations.history"): history,
        ("GET", base + "conversations.replies"): replies,
        ("GET", base + "users.info"): lambda params, **_: {"ok": True, "user": users[params["user"]]},
    }


def linear_routes():
    def graphql(json, headers, **_):
        assert headers["Authorization"] == "lin_api_test"
        assert json["variables"]["filter"]["team"] == {"key": {"eq": "INV"}}
        if json["variables"]["after"] is None:
            nodes = [{
                "identifier": "INV-7", "title": "Multi-currency support", "description": "Add EUR and GBP",
                "url": "https://linear.app/acme/issue/INV-7", "priorityLabel": "High",
                "updatedAt": "2026-10-10T09:00:00.000Z", "completedAt": None,
                "state": {"name": "In Progress", "type": "started"}, "assignee": {"name": "Ana"},
                "project": {"name": "Q4"}, "labels": {"nodes": [{"name": "billing"}]},
            }]
            return {"data": {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": True, "endCursor": "n1"}}}}
        nodes = [{
            "identifier": "INV-3", "title": "Webhook tests", "description": None, "url": "https://linear.app/acme/issue/INV-3",
            "priorityLabel": None, "updatedAt": "2026-10-01T09:00:00.000Z", "completedAt": "2026-10-01T09:00:00.000Z",
            "state": {"name": "Done", "type": "completed"}, "assignee": None, "project": None, "labels": {"nodes": []},
        }]
        return {"data": {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}

    return {("POST", "https://api.linear.app/graphql"): graphql}


def jira_routes():
    def search(params, auth, **_):
        assert auth == ("me@acme.test", "jira-token")
        assert 'project = "INV"' in params["jql"]
        return {"isLast": True, "issues": [{
            "key": "INV-12",
            "fields": {
                "summary": "Email invoices", "updated": "2026-10-12T08:30:00.000+0000",
                "status": {"name": "Blocked", "statusCategory": {"name": "In Progress"}},
                "assignee": {"displayName": "Ben K"}, "issuetype": {"name": "Story"},
                "priority": {"name": "High"}, "labels": ["smtp"], "resolutiondate": None,
                "description": {"type": "doc", "version": 1, "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Waiting on SMTP credentials."}]},
                    {"type": "bulletList", "content": [{"type": "listItem", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "ask ops"}]}]}]},
                ]},
            },
        }]}

    return {("GET", "https://acme.atlassian.net/rest/api/3/search/jql"): search}


PAGE_ID = "1234567890abcdef1234567890abcdef"
DB_ID = "fedcba0987654321fedcba0987654321"


def notion_routes():
    base = "https://api.notion.com/v1"

    def page(headers, **_):
        assert headers["Notion-Version"] == "2022-06-28"
        return {"id": "12345678-90ab-cdef-1234-567890abcdef", "url": "https://notion.so/Roadmap-123",
                "last_edited_time": "2026-10-11T10:00:00.000Z",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "Roadmap"}]}}}

    def query(json, **_):
        return {"has_more": False, "results": [{
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "url": "https://notion.so/Spec",
            "last_edited_time": "2026-10-09T10:00:00.000Z",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Revenue report spec"}]},
                "Status": {"type": "status", "status": {"name": "Not started"}},
            },
        }]}

    def children(**_):
        return {"has_more": False, "results": [
            {"id": "b1", "type": "heading_2", "has_children": False, "heading_2": {"rich_text": [{"plain_text": "Q4"}]}},
            {"id": "b2", "type": "to_do", "has_children": False, "to_do": {"checked": True, "rich_text": [{"plain_text": "PDFs"}]}},
            {"id": "b3", "type": "to_do", "has_children": False, "to_do": {"checked": False, "rich_text": [{"plain_text": "Multi-currency"}]}},
        ]}

    return {
        ("GET", f"{base}/pages/{PAGE_ID}"): page,
        ("POST", f"{base}/databases/{DB_ID}/query"): query,
        ("GET", f"{base}/blocks/12345678-90ab-cdef-1234-567890abcdef/children"): children,
        ("GET", f"{base}/blocks/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/children"): children,
    }


# ------------------------------------------------------ connector units
def test_slack_groups_messages_by_day_with_threads_and_names(fake_http, creds):
    fake_http(slack_routes())
    items = get_connector("slack").fetch({"channel": "C042"})
    assert len(items) in (2, 3)  # depends on where UTC midnight falls; join message is skipped
    text = "\n".join(i.text for i in items)
    assert "Ana: Deploy is blocked on SMTP creds" in text
    assert "↳ Ben K: Ops says Friday" in text
    assert "joined" not in text
    assert all(i.ref.startswith("C042:") and i.title.startswith("Slack #billing") for i in items)


def test_slack_api_error_is_reported(fake_http, creds):
    fake_http({("GET", "https://slack.com/api/conversations.info"): lambda **_: {"ok": False, "error": "x"},
               ("GET", "https://slack.com/api/conversations.history"): lambda **_: {"ok": False, "error": "not_in_channel"}})
    with pytest.raises(ConnectorError, match="not_in_channel"):
        get_connector("slack").fetch({"channel": "C042"})


def test_linear_paginates_and_formats_issues(fake_http, creds):
    fake_http(linear_routes())
    items = get_connector("linear").fetch({"team_key": "INV"})
    assert [i.ref for i in items] == ["INV-7", "INV-3"]
    assert "State: In Progress (started)" in items[0].text and "Priority: High" in items[0].text
    assert items[0].url == "https://linear.app/acme/issue/INV-7"
    assert items[0].timestamp > items[1].timestamp


def test_linear_graphql_errors_raise(fake_http, creds):
    fake_http({("POST", "https://api.linear.app/graphql"): lambda **_: {"errors": [{"message": "Authentication required"}]}})
    with pytest.raises(ConnectorError, match="Authentication required"):
        get_connector("linear").fetch({"team_key": "INV"})


def test_jira_search_flattens_adf_and_parses_dates(fake_http, creds):
    fake_http(jira_routes())
    [item] = get_connector("jira").fetch({"project_key": "INV"})
    assert item.ref == "INV-12"
    assert "Status: Blocked (In Progress)" in item.text
    assert "Waiting on SMTP credentials." in item.text and "- ask ops" in item.text
    assert item.url == "https://acme.atlassian.net/browse/INV-12"
    assert item.timestamp > 0


def test_jira_needs_project_or_jql():
    with pytest.raises(ConnectorError):
        get_connector("jira").validate_config({})


def test_adf_to_text_handles_strings_and_nesting():
    assert adf_to_text("plain") == "plain"
    assert adf_to_text({"type": "paragraph", "content": [{"type": "text", "text": "a"}, {"type": "hardBreak"},
                                                         {"type": "text", "text": "b"}]}) == "a\nb\n"


def test_notion_pages_and_databases(fake_http, creds):
    fake_http(notion_routes())
    items = get_connector("notion").fetch({
        "page_ids": f"https://www.notion.so/acme/Roadmap-{PAGE_ID}",
        "database_ids": [DB_ID],
    })
    by_title = {i.title: i for i in items}
    assert set(by_title) == {"Notion: Roadmap", "Notion: Revenue report spec"}
    spec = by_title["Notion: Revenue report spec"]
    assert "Status: Not started" in spec.text
    assert "[x] PDFs" in spec.text and "[ ] Multi-currency" in spec.text and "## Q4" in spec.text


def test_notion_id_parsing():
    assert notion_id("12345678-90ab-cdef-1234-567890abcdef") == PAGE_ID
    with pytest.raises(ConnectorError):
        notion_id("not-an-id")


def test_missing_credentials_name_the_env_var():
    with pytest.raises(ConnectorError, match="SLACK_BOT_TOKEN"):
        get_connector("slack").fetch({"channel": "C1"})


# ---------------------------------------------------- sources end-to-end
def _all_routes():
    routes = {}
    for r in (slack_routes(), linear_routes(), jira_routes(), notion_routes()):
        routes.update(r)
    return routes


def test_connectors_listing(client, creds):
    kinds = {c["kind"]: c for c in client.get("/connectors").json()}
    assert set(kinds) == {"slack", "linear", "jira", "notion"}
    assert kinds["jira"]["configured"] is True
    assert "JIRA_API_TOKEN" in kinds["jira"]["env_vars"]


def test_sources_are_ingested_in_refresh_and_feed_status(client, two_projects, fake_http, creds, fake_claude):
    fake_http(_all_routes())
    pid = two_projects["invoice"]
    specs = [
        {"kind": "slack", "name": "#billing", "config": {"channel": "C042", "days": "7"}},
        {"kind": "linear", "name": "INV team", "config": {"team_key": "INV"}},
        {"kind": "jira", "name": "INV board", "config": {"project_key": "INV"}},
        {"kind": "notion", "name": "Specs", "config": {"page_ids": PAGE_ID, "database_ids": DB_ID}},
    ]
    for spec in specs:
        r = client.post(f"/projects/{pid}/sources", json=spec)
        assert r.status_code == 200, r.text
    sources = client.get(f"/projects/{pid}/sources").json()
    assert [s["kind"] for s in sources] == ["slack", "linear", "jira", "notion"]
    assert sources[0]["config"]["days"] == 7  # coerced to int

    report = client.post(f"/projects/{pid}/reingest").json()
    assert report["errors"] == []
    assert {s["kind"]: len(s["ingested"]) for s in report["sources"]}["linear"] == 2
    assert report["status_regenerated"] is True
    run = client.get(f"/projects/{pid}/runs").json()[0]
    assert run["items_ingested"] == sum(len(s["ingested"]) for s in report["sources"])

    counts = db.document_counts(pid)
    assert counts["linear"] == 2 and counts["jira"] == 1 and counts["notion"] == 2 and counts["slack"] >= 2
    status_prompt = next(c for c in fake_claude.calls if c["json_schema"])["user"]
    assert "## Issues, messages and pages (newest first)" in status_prompt
    assert "[jira:INV-12]" in status_prompt and "[linear:INV-7]" in status_prompt
    # Newest items first: the Jira issue (Oct 12) comes before the Linear one (Oct 10).
    assert status_prompt.index("[jira:INV-12]") < status_prompt.index("[linear:INV-7]")

    # Re-running skips unchanged items.
    again = client.post(f"/projects/{pid}/reingest").json()
    assert all(s["ingested"] == [] for s in again["sources"])

    r = client.post("/query", json={"question": "Email invoices SMTP blocked", "project_id": pid, "top_k": 12})
    urls = [s.get("url") for s in r.json()["sources"]]
    assert "https://acme.atlassian.net/browse/INV-12" in urls


def test_sync_and_delete_one_source(client, two_projects, fake_http, creds):
    fake_http(_all_routes())
    pid = two_projects["demo"]
    sid = client.post(f"/projects/{pid}/sources", json={"kind": "jira", "config": {"project_key": "INV"}}).json()["id"]
    report = client.post(f"/sources/{sid}/sync").json()
    assert report["ingested"] == ["INV-12"]
    assert db.get_source(sid)["last_synced_at"]
    assert any(m["source_type"] == "jira" for m in get_chunks(pid)["metadatas"])

    r = client.delete(f"/sources/{sid}")
    assert r.json()["documents_removed"] == 1
    assert not any(m["source_type"] == "jira" for m in get_chunks(pid)["metadatas"])
    assert db.get_document(pid, "jira", "INV-12") is None


def test_bad_source_config_and_kind_are_400(client, two_projects):
    pid = two_projects["demo"]
    assert client.post(f"/projects/{pid}/sources", json={"kind": "trello", "config": {}}).status_code == 400
    r = client.post(f"/projects/{pid}/sources", json={"kind": "slack", "config": {}})
    assert r.status_code == 400 and "channel" in r.json()["detail"]


def test_unconfigured_source_reports_error_but_refresh_continues(client, two_projects):
    pid = two_projects["demo"]
    sid = client.post(f"/projects/{pid}/sources", json={"kind": "slack", "config": {"channel": "C1"}}).json()["id"]
    report = client.post(f"/projects/{pid}/reingest", params={"auto_status": False}).json()
    assert report["git"]["ingested"]  # git still ingested
    assert any("SLACK_BOT_TOKEN" in e for e in report["errors"])
    assert "SLACK_BOT_TOKEN" in db.get_source(sid)["last_error"]
    assert client.post(f"/sources/{sid}/sync").status_code == 400


def test_jira_follows_next_page_token(fake_http, creds):
    pages = {
        None: {"issues": [{"key": "INV-1", "fields": {"summary": "a", "updated": "2026-10-01T00:00:00.000+0000"}}],
               "nextPageToken": "t2"},
        "t2": {"issues": [{"key": "INV-2", "fields": {"summary": "b", "updated": "2026-10-02T00:00:00.000+0000"}}],
               "isLast": True},
    }
    fake_http({("GET", "https://acme.atlassian.net/rest/api/3/search/jql"):
               lambda params, **_: pages[params.get("nextPageToken")]})
    assert [i.ref for i in get_connector("jira").fetch({"jql": "project = INV"})] == ["INV-1", "INV-2"]
