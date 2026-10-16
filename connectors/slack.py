"""Slack: recent messages (and thread replies) from one channel, one Item per day.

Needs a bot token (SLACK_BOT_TOKEN) with channels:history, channels:read and
users:read (plus groups:history / groups:read for private channels), and the
bot must be a member of the channel.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from app import config as cfg
from connectors.base import Connector, ConnectorError, Field, Item, iso_from_unix

API = "https://slack.com/api"
MAX_MESSAGES = 2000
MAX_THREADS = 50
_SKIP_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "bot_add", "bot_remove"}


class SlackConnector(Connector):
    kind = "slack"
    label = "Slack"
    fields = [
        Field("channel", "Channel ID", required=True, help="e.g. C0123456789 (channel details → About)"),
        Field("days", "Days of history", type="int", default=14),
    ]

    def credentials(self) -> dict[str, str]:
        return {"SLACK_BOT_TOKEN": cfg.SLACK_BOT_TOKEN}

    def _call(self, method: str, **params) -> dict:
        token = self.require_credentials()["SLACK_BOT_TOKEN"]
        data = self._request("GET", f"{API}/{method}", params=params, headers={"Authorization": f"Bearer {token}"})
        if not data.get("ok"):
            raise ConnectorError(f"Slack {method} failed: {data.get('error', 'unknown error')}")
        return data

    def _paginate(self, method: str, limit: int, **params) -> list[dict]:
        messages: list[dict] = []
        cursor = None
        while len(messages) < limit:
            page_params = dict(params, limit=200)
            if cursor:
                page_params["cursor"] = cursor
            data = self._call(method, **page_params)
            messages.extend(data.get("messages") or [])
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not data.get("has_more") or not cursor:
                break
        return messages[:limit]

    def fetch(self, config: dict) -> list[Item]:
        config = self.validate_config(config)
        channel = config["channel"]
        oldest = time.time() - config["days"] * 86400

        try:
            channel_name = self._call("conversations.info", channel=channel)["channel"].get("name") or channel
        except ConnectorError:
            channel_name = channel  # missing channels:read scope -- not fatal

        messages = [
            m for m in self._paginate("conversations.history", MAX_MESSAGES, channel=channel, oldest=f"{oldest:.6f}")
            if m.get("text") and m.get("subtype") not in _SKIP_SUBTYPES
        ]

        replies: dict[str, list[dict]] = {}
        for m in [m for m in messages if m.get("reply_count")][:MAX_THREADS]:
            thread = self._paginate("conversations.replies", 200, channel=channel, ts=m["ts"])
            replies[m["ts"]] = [r for r in thread if r.get("ts") != m["ts"] and r.get("text")]

        names: dict[str, str] = {}

        def name_of(msg: dict) -> str:
            user = msg.get("user")
            if not user:
                return msg.get("username") or (msg.get("bot_profile") or {}).get("name") or "bot"
            if user not in names:
                try:
                    u = self._call("users.info", user=user)["user"]
                    profile = u.get("profile") or {}
                    names[user] = profile.get("display_name") or u.get("real_name") or u.get("name") or user
                except ConnectorError:
                    names[user] = user
            return names[user]

        by_day: dict[str, list[dict]] = defaultdict(list)
        for m in messages:
            day = datetime.fromtimestamp(float(m["ts"]), tz=timezone.utc).date().isoformat()
            by_day[day].append(m)

        items = []
        for day, day_messages in sorted(by_day.items()):
            day_messages.sort(key=lambda m: float(m["ts"]))
            lines = []
            latest = 0.0
            for m in day_messages:
                stamp = datetime.fromtimestamp(float(m["ts"]), tz=timezone.utc).strftime("%H:%M")
                lines.append(f"[{stamp}] {name_of(m)}: {m['text']}")
                latest = max(latest, float(m["ts"]))
                for r in replies.get(m["ts"], []):
                    lines.append(f"    ↳ {name_of(r)}: {r['text']}")
                    latest = max(latest, float(r["ts"]))
            items.append(Item(
                ref=f"{channel}:{day}",
                title=f"Slack #{channel_name} on {day}",
                text="\n".join(lines),
                updated_at=iso_from_unix(latest),
            ))
        return items
