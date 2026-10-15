"""The one place that talks to Claude.

Both the Q&A pipeline and the structured status generator go through
`generate()`, so model, effort, and refusal handling are configured once.
"""
from __future__ import annotations

import anthropic

from app.config import ANTHROPIC_API_KEY, CLAUDE_EFFORT, CLAUDE_MODEL, CLAUDE_REFUSAL_FALLBACK

# Models that accept the `fallbacks: "default"` server-side refusal fallback.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """Claude was reachable but didn't produce a usable answer (refusal, truncation)."""


def is_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)


def _client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def generate(system: str, user: str, *, json_schema: dict | None = None, max_tokens: int = 16000) -> str:
    """Send one system+user prompt to Claude and return the text of the reply.

    With `json_schema`, the reply is constrained to JSON matching that schema
    (structured outputs) and the returned string is that JSON.
    """
    output_config: dict = {}
    if CLAUDE_EFFORT:
        output_config["effort"] = CLAUDE_EFFORT
    if json_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": json_schema}

    kwargs: dict = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if output_config:
        kwargs["output_config"] = output_config
    if CLAUDE_REFUSAL_FALLBACK and CLAUDE_MODEL in _FALLBACK_MODELS:
        kwargs["betas"] = [_FALLBACK_BETA]
        kwargs["fallbacks"] = "default"

    response = _client().beta.messages.create(**kwargs)

    if response.stop_reason == "refusal":
        raise LLMError("Claude declined to answer this request.")
    if response.stop_reason == "max_tokens" and json_schema is not None:
        raise LLMError("Claude's structured reply was cut off before it finished.")

    return "".join(block.text for block in response.content if block.type == "text")
