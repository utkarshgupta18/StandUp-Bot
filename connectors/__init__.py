"""Phase 4: external source connectors (Slack, Linear, Jira, Notion).

Each connector turns one configured source into a list of `Item`s (a Slack
channel-day, an issue, a page); `ingestion/source_ingestor.py` embeds them
like docs and commits. Credentials come from environment variables; the
per-source settings (which channel, team, JQL, pages) live in the database.
"""
from connectors.base import ConnectorError, Item
from connectors.jira import JiraConnector
from connectors.linear import LinearConnector
from connectors.notion import NotionConnector
from connectors.slack import SlackConnector

CONNECTORS = {
    c.kind: c for c in (SlackConnector, LinearConnector, JiraConnector, NotionConnector)
}


def get_connector(kind: str):
    """Return a connector instance for `kind`, or raise ConnectorError."""
    try:
        return CONNECTORS[kind]()
    except KeyError:
        raise ConnectorError(f"Unknown source kind '{kind}'. Available: {', '.join(sorted(CONNECTORS))}")


def describe_connectors() -> list[dict]:
    return [c().describe() for c in CONNECTORS.values()]


__all__ = ["CONNECTORS", "ConnectorError", "Item", "describe_connectors", "get_connector"]
