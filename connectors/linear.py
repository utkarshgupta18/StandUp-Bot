"""Linear: recently updated issues for one team, one Item per issue.

Needs a personal API key (LINEAR_API_KEY, Settings → Security & access).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import config as cfg
from connectors.base import Connector, ConnectorError, Field, Item

API = "https://api.linear.app/graphql"

QUERY = """
query Issues($filter: IssueFilter, $after: String) {
  issues(filter: $filter, first: 50, after: $after, orderBy: updatedAt) {
    nodes {
      identifier
      title
      description
      url
      priorityLabel
      updatedAt
      completedAt
      state { name type }
      assignee { name }
      project { name }
      labels { nodes { name } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


class LinearConnector(Connector):
    kind = "linear"
    label = "Linear"
    fields = [
        Field("team_key", "Team key", required=True, help="the prefix of issue ids, e.g. ENG for ENG-123"),
        Field("project", "Project name", help="optional: only issues in this Linear project"),
        Field("days", "Updated in the last N days", type="int", default=90),
        Field("max_issues", "Max issues", type="int", default=200),
    ]

    def credentials(self) -> dict[str, str]:
        return {"LINEAR_API_KEY": cfg.LINEAR_API_KEY}

    def fetch(self, config: dict) -> list[Item]:
        config = self.validate_config(config)
        key = self.require_credentials()["LINEAR_API_KEY"]
        since = (datetime.now(timezone.utc) - timedelta(days=config["days"])).isoformat()
        issue_filter: dict = {"team": {"key": {"eq": config["team_key"]}}, "updatedAt": {"gt": since}}
        if config.get("project"):
            issue_filter["project"] = {"name": {"eq": config["project"]}}

        nodes: list[dict] = []
        after = None
        while len(nodes) < config["max_issues"]:
            data = self._request(
                "POST", API,
                json={"query": QUERY, "variables": {"filter": issue_filter, "after": after}},
                headers={"Authorization": key, "Content-Type": "application/json"},
            )
            if data.get("errors"):
                raise ConnectorError(f"Linear: {data['errors'][0].get('message', data['errors'])}")
            issues = (data.get("data") or {}).get("issues") or {}
            nodes.extend(issues.get("nodes") or [])
            page = issues.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")

        return [self._to_item(n) for n in nodes[: config["max_issues"]]]

    @staticmethod
    def _to_item(n: dict) -> Item:
        state = n.get("state") or {}
        labels = ", ".join(lbl["name"] for lbl in ((n.get("labels") or {}).get("nodes") or []))
        lines = [
            f"State: {state.get('name', '?')} ({state.get('type', '?')})",
            f"Assignee: {(n.get('assignee') or {}).get('name') or 'unassigned'}",
        ]
        if n.get("project"):
            lines.append(f"Project: {n['project']['name']}")
        if n.get("priorityLabel"):
            lines.append(f"Priority: {n['priorityLabel']}")
        if labels:
            lines.append(f"Labels: {labels}")
        lines.append(f"Updated: {n.get('updatedAt')}")
        if n.get("completedAt"):
            lines.append(f"Completed: {n['completedAt']}")
        if n.get("description"):
            lines.append("")
            lines.append(n["description"])
        return Item(
            ref=n["identifier"],
            title=f"Linear {n['identifier']}: {n.get('title', '')}",
            text="\n".join(lines),
            updated_at=n.get("updatedAt") or "",
            url=n.get("url"),
        )
