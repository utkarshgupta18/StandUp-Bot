"""Notion: specific pages and/or every page in a database, one Item per page.

Needs an internal integration token (NOTION_TOKEN) and the pages/databases
must be shared with that integration (page menu → Connections).
"""
from __future__ import annotations

import re

from app import config as cfg
from connectors.base import Connector, ConnectorError, Field, Item

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_BLOCKS = 500
MAX_DEPTH = 2

_PREFIX = {
    "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
    "bulleted_list_item": "- ", "numbered_list_item": "1. ", "quote": "> ", "callout": "> ",
}


def notion_id(value: str) -> str:
    """Accept a raw id, a dashed UUID, or a Notion URL; return the 32-char id."""
    found = re.findall(r"[0-9a-fA-F]{32}", value.replace("-", ""))
    if not found:
        raise ConnectorError(f"Not a Notion page/database id or URL: {value}")
    return found[-1].lower()


def _plain(rich_text: list) -> str:
    return "".join(rt.get("plain_text", "") for rt in rich_text or [])


def _property_text(prop: dict) -> str:
    kind = prop.get("type")
    value = prop.get(kind)
    if value is None:
        return ""
    if kind in ("title", "rich_text"):
        return _plain(value)
    if kind in ("select", "status"):
        return value.get("name", "")
    if kind == "multi_select":
        return ", ".join(v.get("name", "") for v in value)
    if kind == "people":
        return ", ".join(p.get("name") or p.get("id", "") for p in value)
    if kind == "date":
        return value.get("start", "") + (f" → {value['end']}" if value.get("end") else "")
    if kind == "checkbox":
        return "yes" if value else "no"
    if kind in ("number", "url", "email"):
        return str(value)
    return ""


class NotionConnector(Connector):
    kind = "notion"
    label = "Notion"
    fields = [
        Field("page_ids", "Page ids or URLs", type="list", help="comma-separated"),
        Field("database_ids", "Database ids or URLs", type="list", help="comma-separated; every page is ingested"),
        Field("max_pages", "Max pages per database", type="int", default=100),
    ]

    def credentials(self) -> dict[str, str]:
        return {"NOTION_TOKEN": cfg.NOTION_TOKEN}

    def validate_config(self, config: dict) -> dict:
        clean = super().validate_config(config)
        if not clean.get("page_ids") and not clean.get("database_ids"):
            raise ConnectorError("Notion source needs 'page_ids' and/or 'database_ids'")
        clean["page_ids"] = [notion_id(p) for p in clean.get("page_ids", [])]
        clean["database_ids"] = [notion_id(d) for d in clean.get("database_ids", [])]
        return clean

    def _call(self, method: str, path: str, **kwargs) -> dict:
        token = self.require_credentials()["NOTION_TOKEN"]
        headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}
        return self._request(method, f"{API}{path}", headers=headers, **kwargs)

    def fetch(self, config: dict) -> list[Item]:
        config = self.validate_config(config)
        pages: dict[str, dict] = {}
        for page_id in config["page_ids"]:
            page = self._call("GET", f"/pages/{page_id}")
            pages[page["id"]] = page
        for db_id in config["database_ids"]:
            cursor = None
            count = 0
            while count < config["max_pages"]:
                body: dict = {"page_size": 50, "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]}
                if cursor:
                    body["start_cursor"] = cursor
                data = self._call("POST", f"/databases/{db_id}/query", json=body)
                for page in data.get("results") or []:
                    if count >= config["max_pages"]:
                        break
                    pages[page["id"]] = page
                    count += 1
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
        return [self._to_item(page) for page in pages.values()]

    def _blocks(self, block_id: str, depth: int = 0, budget: list | None = None) -> list[str]:
        budget = budget if budget is not None else [MAX_BLOCKS]
        lines: list[str] = []
        cursor = None
        while budget[0] > 0:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._call("GET", f"/blocks/{block_id}/children", params=params)
            for block in data.get("results") or []:
                budget[0] -= 1
                kind = block.get("type", "")
                content = block.get(kind) or {}
                text = _plain(content.get("rich_text"))
                if kind == "to_do":
                    text = ("[x] " if content.get("checked") else "[ ] ") + text
                elif kind == "child_page":
                    text = f"(subpage) {content.get('title', '')}"
                else:
                    text = _PREFIX.get(kind, "") + text
                if text.strip():
                    lines.append("  " * depth + text)
                if block.get("has_children") and depth < MAX_DEPTH and kind != "child_page":
                    lines.extend(self._blocks(block["id"], depth + 1, budget))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return lines

    def _to_item(self, page: dict) -> Item:
        props = page.get("properties") or {}
        title = next((_plain(p.get("title")) for p in props.values() if p.get("type") == "title"), "") or "Untitled"
        prop_lines = [
            f"{name}: {_property_text(p)}" for name, p in props.items()
            if p.get("type") != "title" and _property_text(p)
        ]
        body = self._blocks(page["id"])
        text = "\n".join(prop_lines + ([""] if prop_lines and body else []) + body)
        return Item(
            ref=page["id"].replace("-", ""),
            title=f"Notion: {title}",
            text=text,
            updated_at=page.get("last_edited_time") or "",
            url=page.get("url"),
        )
