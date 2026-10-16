"""Login rate limiting: per-IP and per-username caps on failed sign-ins."""
import pytest

from app import auth
from app import config as cfg
from app.ratelimit import LoginRateLimiter, login_limiter
from db import database as db


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setattr(cfg, "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 900)
    monkeypatch.setattr(cfg, "LOGIN_MAX_FAILURES_PER_IP", 5)
    monkeypatch.setattr(cfg, "LOGIN_MAX_FAILURES_PER_USER", 3)


# ------------------------------------------------------------- unit level
def test_user_limit_blocks_until_failures_age_out(limits):
    clock = Clock()
    rl = LoginRateLimiter(clock)
    for _ in range(3):
        assert rl.retry_after("1.1.1.1", "alice") == 0
        rl.record_failure("1.1.1.1", "alice")
        clock.now += 60
    # Failures at t=1000, 1060, 1120; now t=1180. Oldest leaves the window at 1900.
    assert rl.retry_after("1.1.1.1", "alice") == 720
    assert rl.retry_after("2.2.2.2", "ALICE ") == 720   # username key is normalised, any IP
    assert rl.retry_after("1.1.1.1", "bob") == 0        # IP still under its own limit
    clock.now = 1900
    assert rl.retry_after("1.1.1.1", "alice") == 0      # one failure aged out


def test_ip_limit_spans_usernames(limits):
    rl = LoginRateLimiter(Clock())
    for name in ["a", "b", "c", "d", "e"]:
        rl.record_failure("9.9.9.9", name)
    assert rl.retry_after("9.9.9.9", "someone-new") > 0
    assert rl.retry_after("8.8.8.8", "someone-new") == 0


def test_success_clears_username_but_not_ip(limits):
    rl = LoginRateLimiter(Clock())
    for _ in range(3):
        rl.record_failure("1.1.1.1", "alice")
    rl.record_success("1.1.1.1", "alice")
    assert rl.retry_after("3.3.3.3", "alice") == 0
    rl.record_failure("1.1.1.1", "x")
    rl.record_failure("1.1.1.1", "y")
    assert rl.retry_after("1.1.1.1", "z") > 0  # 3 + 2 = 5 failures from this IP


def test_zero_disables_a_limit(limits, monkeypatch):
    monkeypatch.setattr(cfg, "LOGIN_MAX_FAILURES_PER_USER", 0)
    monkeypatch.setattr(cfg, "LOGIN_MAX_FAILURES_PER_IP", 0)
    rl = LoginRateLimiter(Clock())
    for _ in range(50):
        rl.record_failure("1.1.1.1", "alice")
    assert rl.retry_after("1.1.1.1", "alice") == 0


def test_expired_keys_are_dropped(limits):
    clock = Clock()
    rl = LoginRateLimiter(clock)
    rl.record_failure("1.1.1.1", "alice")
    clock.now += 901
    rl.retry_after("1.1.1.1", "alice")
    assert rl._failures == {}


# -------------------------------------------------------------- API level
@pytest.fixture
def auth_on(monkeypatch, limits):
    monkeypatch.setattr(cfg, "AUTH_ENABLED", True)
    monkeypatch.setattr(cfg, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth, "_secret", None)
    db.create_user("alice", auth.hash_password("right-password"), "admin")
    yield
    auth._secret = None


def _login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_login_returns_429_after_repeated_failures(client, auth_on, monkeypatch):
    calls = []
    real_verify = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda *a: calls.append(1) or real_verify(*a))

    for _ in range(3):
        assert _login(client, "alice", "wrong").status_code == 401
    r = _login(client, "alice", "right-password")  # even the right password is held back
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert "Too many failed sign-in attempts" in r.json()["detail"]
    assert len(calls) == 3  # blocked attempt never ran the password hash


def test_successful_login_resets_user_counter(client, auth_on):
    for _ in range(2):
        assert _login(client, "alice", "wrong").status_code == 401
    assert _login(client, "alice", "right-password").status_code == 200
    for _ in range(2):
        assert _login(client, "alice", "wrong").status_code == 401
    assert _login(client, "alice", "right-password").status_code == 200


def test_unknown_usernames_count_against_the_ip(client, auth_on):
    for i in range(5):
        assert _login(client, f"ghost{i}", "x").status_code == 401
    assert _login(client, "alice", "right-password").status_code == 429


def test_limiter_is_not_used_when_auth_disabled(client):
    assert _login(client, "alice", "x").status_code == 400
    assert login_limiter._failures == {}
