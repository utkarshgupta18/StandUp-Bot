"""Login rate limiting: cap failed sign-in attempts per client IP and per username.

Only failures count, so people who type their password right are never
slowed down. Two keys are tracked within a sliding window:

- per client IP: one machine trying many usernames or passwords;
- per username: many machines guessing one account's password.

When either key reaches its limit, further attempts for it are rejected with
429 until the oldest failure leaves the window, without checking the
password, so a blocked attacker also can't burn server CPU on PBKDF2.

A successful login clears that username's failures but not the IP's;
otherwise an attacker with one valid account could reset their IP counter
between guesses at other accounts.

Failures are stored in the `login_failures` table of the configured database
(SQLite or Postgres), so every API process or replica shares the same counts
and they survive restarts. Rows older than the window are pruned whenever a
new failure is recorded. The check and the record aren't one transaction, so
concurrent attempts can overshoot a limit by a few; that's fine for slowing
down guessing.

Behind a reverse proxy, run uvicorn with --proxy-headers
--forwarded-allow-ips=<proxy ip> so the real client IP is used instead of the
proxy's.
"""
from __future__ import annotations

import math
import time
from typing import Callable

from app import config as cfg
from db import database as db


class LoginRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.time):
        # Wall-clock time: timestamps are compared across processes and restarts.
        self._clock = clock

    @staticmethod
    def _user_key(username: str) -> str:
        return f"user:{username.strip().lower()}"

    @classmethod
    def _keys(cls, ip: str, username: str) -> list[tuple[str, int]]:
        """(counter key, limit) pairs that apply to this attempt; limit 0 = off."""
        return [
            (f"ip:{ip}", cfg.LOGIN_MAX_FAILURES_PER_IP),
            (cls._user_key(username), cfg.LOGIN_MAX_FAILURES_PER_USER),
        ]

    def retry_after(self, ip: str, username: str) -> int:
        """Seconds until another attempt is allowed; 0 means allowed now."""
        now = self._clock()
        window = cfg.LOGIN_RATE_LIMIT_WINDOW_SECONDS
        wait = 0.0
        for key, limit in self._keys(ip, username):
            if limit <= 0:
                continue
            newest = db.recent_login_failures(key, since=now - window, limit=limit)
            if len(newest) >= limit:
                # Allowed again once the limit-th newest failure ages out of the window.
                wait = max(wait, newest[-1] + window - now)
        return math.ceil(wait) if wait > 0 else 0

    def record_failure(self, ip: str, username: str) -> None:
        now = self._clock()
        keys = [key for key, limit in self._keys(ip, username) if limit > 0]
        if keys:
            db.add_login_failures(keys, now)
        db.prune_login_failures(before=now - cfg.LOGIN_RATE_LIMIT_WINDOW_SECONDS)

    def record_success(self, ip: str, username: str) -> None:
        db.clear_login_failures(self._user_key(username))

    def reset(self) -> None:
        db.clear_login_failures()


login_limiter = LoginRateLimiter()
