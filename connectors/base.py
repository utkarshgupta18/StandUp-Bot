"""Shared pieces for connectors: the Item type, config validation, HTTP."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import requests

HTTP_TIMEOUT = 30


def new_session() -> requests.Session:
    """HTTP session for connectors (tests replace this to fake the APIs)."""
    return requests.Session()


class ConnectorError(Exception):
    """A source couldn't be fetched: missing credentials, bad config, or an API error."""


@dataclass
class Item:
    """One unit of content from an external source, ingested as one document."""

    ref: str            # stable id within the project, e.g. "ENG-42" or "C0123:2026-10-01"
    title: str
    text: str
    updated_at: str     # ISO 8601, used for "newest first" ordering in status prompts
    url: str | None = None

    @property
    def timestamp(self) -> int:
        try:
            return int(datetime.fromisoformat(self.updated_at.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0


@dataclass
class Field:
    name: str
    label: str
    type: str = "string"        # string | int | list
    required: bool = False
    default: object = None
    help: str = ""


class Connector:
    """Base class. Subclasses set the class attributes and implement fetch()."""

    kind: str = ""
    label: str = ""
    fields: list[Field] = []

    def __init__(self, session: requests.Session | None = None):
        self.session = session or new_session()

    def credentials(self) -> dict[str, str]:
        """Override: {ENV_VAR_NAME: value} for every credential this connector needs."""
        return {}

    def is_configured(self) -> bool:
        return all(self.credentials().values())

    def require_credentials(self) -> dict[str, str]:
        creds = self.credentials()
        missing = [env for env, value in creds.items() if not value]
        if missing:
            raise ConnectorError(f"{self.label} isn't configured: set {', '.join(missing)} in .env")
        return creds

    def validate_config(self, config: dict) -> dict:
        """Check required fields, coerce types, and fill defaults."""
        config = dict(config or {})
        clean: dict = {}
        for f in self.fields:
            value = config.get(f.name)
            if value in (None, "", []):
                if f.required:
                    raise ConnectorError(f"{self.label} source needs '{f.name}' ({f.help or f.label})")
                if f.default is not None:
                    clean[f.name] = f.default
                continue
            if f.type == "int":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ConnectorError(f"'{f.name}' must be a number")
            elif f.type == "list":
                if isinstance(value, str):
                    value = [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]
                value = [str(v) for v in value]
            else:
                value = str(value).strip()
            clean[f.name] = value
        return clean

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "configured": self.is_configured(),
            "env_vars": list(self.credentials()),
            "fields": [
                {"name": f.name, "label": f.label, "type": f.type, "required": f.required,
                 "default": f.default, "help": f.help}
                for f in self.fields
            ],
        }

    def fetch(self, config: dict) -> list[Item]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -------------------------------------------------------------- http
    def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            r = self.session.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)
        except requests.RequestException as e:
            raise ConnectorError(f"{self.label}: request failed: {e}")
        if r.status_code >= 400:
            raise ConnectorError(f"{self.label}: HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            raise ConnectorError(f"{self.label}: response wasn't JSON")


def iso_from_unix(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")
