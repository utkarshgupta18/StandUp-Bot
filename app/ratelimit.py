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

Counters live in process memory: they reset on restart, and each API process
(e.g. several uvicorn workers or replicas) keeps its own. Behind a reverse
proxy, run uvicorn with --proxy-headers --forwarded-allow-ips=<proxy ip> so the
real client IP is used instead of the proxy's.
"""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Callable

from app import config as cfg


class LoginRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def _keys(ip: str, username: str) -> list[tuple[str, int]]:
        """(counter key, limit) pairs that apply to this attempt; limit 0 = off."""
        return [
            (f"ip:{ip}", cfg.LOGIN_MAX_FAILURES_PER_IP),
            (f"user:{username.strip().lower()}", cfg.LOGIN_MAX_FAILURES_PER_USER),
        ]

    def _prune(self, key: str, now: float) -> deque[float]:
        window = cfg.LOGIN_RATE_LIMIT_WINDOW_SECONDS
        failures = self._failures[key]
        while failures and failures[0] <= now - window:
            failures.popleft()
        if not failures:
            del self._failures[key]  # keep memory bounded to recently failing keys
            return deque()
        return failures

    def retry_after(self, ip: str, username: str) -> int:
        """Seconds until another attempt is allowed; 0 means allowed now."""
        now = self._clock()
        wait = 0.0
        with self._lock:
            for key, limit in self._keys(ip, username):
                if limit <= 0:
                    continue
                failures = self._prune(key, now)
                if len(failures) >= limit:
                    # Allowed again once enough old failures age out of the window.
                    oldest_relevant = failures[len(failures) - limit]
                    wait = max(wait, oldest_relevant + cfg.LOGIN_RATE_LIMIT_WINDOW_SECONDS - now)
        return math.ceil(wait) if wait > 0 else 0

    def record_failure(self, ip: str, username: str) -> None:
        now = self._clock()
        with self._lock:
            for key, limit in self._keys(ip, username):
                if limit > 0:
                    self._failures[key].append(now)

    def record_success(self, ip: str, username: str) -> None:
        with self._lock:
            self._failures.pop(f"user:{username.strip().lower()}", None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_limiter = LoginRateLimiter()
