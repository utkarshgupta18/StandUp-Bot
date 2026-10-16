"""Jira Cloud: issues matching a JQL query (or a project), one Item per issue.

Needs JIRA_BASE_URL (https://your-site.atlassian.net), JIRA_EMAIL and an API
token (JIRA_API_TOKEN, from id.atlassian.com → Security → API tokens).
Uses the /rest/api/3/search/jql endpoint with token-based pagination.
"""
from __future__ import annotations

from app import config as cfg
from connectors.base import Connector, ConnectorError, Field, Item

FIELDS = "summary,status,assignee,updated,description,issuetype,priority,labels,resolutiondate"


def adf_to_text(node) -> str:
    """Flatten Atlassian Document Format (Jira v3 rich text) to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji"):
        return (node.get("attrs") or {}).get("text", "")
    inner = adf_to_text(node.get("content") or [])
    if kind == "listItem":
        return "- " + inner.strip() + "\n"
    if kind in ("paragraph", "heading", "codeBlock", "blockquote"):
        return inner + "\n"
    return inner


class JiraConnector(Connector):
    kind = "jira"
    label = "Jira"
    fields = [
        Field("project_key", "Project key", help="e.g. ABC; used when no JQL is given"),
        Field("jql", "JQL", help="optional: overrides project key, e.g. project = ABC AND sprint in openSprints()"),
        Field("days", "Updated in the last N days", type="int", default=90),
        Field("max_issues", "Max issues", type="int", default=200),
    ]

    def credentials(self) -> dict[str, str]:
        return {"JIRA_BASE_URL": cfg.JIRA_BASE_URL, "JIRA_EMAIL": cfg.JIRA_EMAIL, "JIRA_API_TOKEN": cfg.JIRA_API_TOKEN}

    def validate_config(self, config: dict) -> dict:
        clean = super().validate_config(config)
        if not clean.get("jql") and not clean.get("project_key"):
            raise ConnectorError("Jira source needs either 'project_key' or 'jql'")
        return clean

    def fetch(self, config: dict) -> list[Item]:
        config = self.validate_config(config)
        creds = self.require_credentials()
        base = creds["JIRA_BASE_URL"].rstrip("/")
        jql = config.get("jql") or (
            f'project = "{config["project_key"]}" AND updated >= -{config["days"]}d ORDER BY updated DESC'
        )

        issues: list[dict] = []
        token = None
        while len(issues) < config["max_issues"]:
            params = {"jql": jql, "fields": FIELDS, "maxResults": 50}
            if token:
                params["nextPageToken"] = token
            data = self._request(
                "GET", f"{base}/rest/api/3/search/jql", params=params,
                auth=(creds["JIRA_EMAIL"], creds["JIRA_API_TOKEN"]),
                headers={"Accept": "application/json"},
            )
            issues.extend(data.get("issues") or [])
            token = data.get("nextPageToken")
            if data.get("isLast") or not token:
                break

        return [self._to_item(i, base) for i in issues[: config["max_issues"]]]

    @staticmethod
    def _to_item(issue: dict, base: str) -> Item:
        f = issue.get("fields") or {}
        status = f.get("status") or {}
        category = (status.get("statusCategory") or {}).get("name", "")
        lines = [
            f"Type: {(f.get('issuetype') or {}).get('name', '?')}",
            f"Status: {status.get('name', '?')}" + (f" ({category})" if category else ""),
            f"Assignee: {(f.get('assignee') or {}).get('displayName') or 'unassigned'}",
        ]
        if f.get("priority"):
            lines.append(f"Priority: {f['priority'].get('name')}")
        if f.get("labels"):
            lines.append(f"Labels: {', '.join(f['labels'])}")
        lines.append(f"Updated: {f.get('updated')}")
        if f.get("resolutiondate"):
            lines.append(f"Resolved: {f['resolutiondate']}")
        description = adf_to_text(f.get("description")).strip()
        if description:
            lines += ["", description]
        updated = f.get("updated") or ""
        # Jira returns e.g. 2026-10-01T12:00:00.000+0000; make it ISO-parsable.
        if len(updated) > 5 and updated[-5] in "+-" and updated[-3] != ":":
            updated = updated[:-2] + ":" + updated[-2:]
        return Item(
            ref=issue["key"],
            title=f"Jira {issue['key']}: {f.get('summary', '')}",
            text="\n".join(lines),
            updated_at=updated,
            url=f"{base}/browse/{issue['key']}",
        )
