"""Phase 4: optional authentication and per-project access control.

Off by default (AUTH_ENABLED=false): every request acts as a local admin,
which keeps single-user, on-your-own-machine use friction-free. When on:

- Users log in with username + password (POST /auth/login) and get a
  signed bearer token (HMAC-SHA256 over a small JSON payload, with expiry).
  No token store is needed; deleting a user or changing their role takes
  effect immediately because each request re-reads the user.
- Roles: `admin` can do everything (manage projects, sources, users, run
  ingestion and status generation). `viewer` can read dashboards, status and
  ask questions -- only for the projects an admin has granted them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time

from fastapi import Depends, HTTPException, Request

from app import config as cfg
from db import database as db

log = logging.getLogger(__name__)

ROLES = ("admin", "viewer")
_PBKDF2_ITERATIONS = 240_000
_LOCAL_ADMIN = {"id": None, "username": "local", "role": "admin"}

_secret: bytes | None = None


def _get_secret() -> bytes:
    global _secret
    if _secret is None:
        if cfg.AUTH_SECRET:
            _secret = cfg.AUTH_SECRET.encode()
        else:
            if cfg.AUTH_ENABLED:
                log.warning("AUTH_SECRET is not set: using a random secret, so tokens stop working on restart")
            _secret = secrets.token_bytes(32)
    return _secret


# ------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------------------------------------------------------------- tokens
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_token(user_id: int, ttl_hours: int | None = None) -> str:
    ttl = (ttl_hours if ttl_hours is not None else cfg.AUTH_TOKEN_TTL_HOURS) * 3600
    payload = _b64(json.dumps({"sub": user_id, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_get_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def read_token(token: str) -> dict | None:
    """Return the token payload if the signature is valid and it hasn't expired."""
    try:
        payload, signature = token.split(".")
    except ValueError:
        return None
    expected = _b64(hmac.new(_get_secret(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


# ------------------------------------------------------------------ users
def public_user(user: dict) -> dict:
    """A user row without the password hash, plus their project grants."""
    data = {k: v for k, v in user.items() if k != "password_hash"}
    if user.get("id") is not None:
        data["project_ids"] = db.user_project_ids(user["id"])
    return data


def authenticate(username: str, password: str) -> dict | None:
    user = db.get_user_by_username(username)
    if user and verify_password(password, user["password_hash"]):
        return user
    return None


def bootstrap_admin() -> None:
    """Create the first admin from ADMIN_USERNAME/ADMIN_PASSWORD if there are no users yet."""
    if cfg.AUTH_ENABLED and cfg.ADMIN_USERNAME and cfg.ADMIN_PASSWORD and db.count_users() == 0:
        db.create_user(cfg.ADMIN_USERNAME, hash_password(cfg.ADMIN_PASSWORD), "admin")
        log.info("Created initial admin user '%s'", cfg.ADMIN_USERNAME)


# ------------------------------------------------------- FastAPI deps
def current_user(request: Request) -> dict:
    if not cfg.AUTH_ENABLED:
        return _LOCAL_ADMIN
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    payload = read_token(token) if token else None
    user = db.get_user(payload["sub"]) if payload else None
    if not user:
        raise HTTPException(401, "Not signed in or session expired", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def visible_project_ids(user: dict) -> list[int] | None:
    """None means every project (admins, or auth disabled)."""
    if user["role"] == "admin":
        return None
    return db.user_project_ids(user["id"])


def can_see_project(user: dict, project_id: int) -> bool:
    allowed = visible_project_ids(user)
    return allowed is None or project_id in allowed
