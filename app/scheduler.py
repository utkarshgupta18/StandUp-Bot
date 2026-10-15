"""Background re-ingestion scheduler (Phase 2).

Runs inside the FastAPI process: every REINGEST_INTERVAL_MINUTES it
re-ingests all projects (new commits, changed/deleted docs) and, when an API
key is configured, regenerates structured status for projects that changed.
Disabled when the interval is 0.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ingestion.runner import reingest_all

log = logging.getLogger(__name__)


class ReingestScheduler:
    def __init__(self, interval_minutes: int):
        self.interval_minutes = interval_minutes
        self.last_run_at: str | None = None
        self.last_error: str | None = None
        self.runs = 0
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return self.interval_minutes > 0

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._loop())
            log.info("Re-ingestion scheduler started (every %s min)", self.interval_minutes)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_once(self) -> list[dict]:
        # Ingestion is blocking (embedding, git, SQLite) -- keep it off the event loop.
        try:
            reports = await asyncio.to_thread(reingest_all, "scheduled")
            self.last_error = None
            return reports
        except Exception as e:
            log.exception("Scheduled re-ingestion failed")
            self.last_error = str(e)
            return []
        finally:
            self.runs += 1
            self.last_run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_minutes * 60)
            await self.run_once()

    def info(self) -> dict:
        return {
            "enabled": self.enabled,
            "interval_minutes": self.interval_minutes,
            "runs": self.runs,
            "last_run_at": self.last_run_at,
            "last_error": self.last_error,
        }
