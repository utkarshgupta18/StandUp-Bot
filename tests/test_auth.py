"""Phase 4: authentication, roles, and per-project access control."""
import pytest

from app import auth
from app import config as cfg
from db import database as db


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(cfg, "AUTH_ENABLED", True)
    monkeypatch.setattr(cfg, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth, "_secret", None)
    yield
    auth._secret = None


def _login(client, username, password):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def admin_headers(client, auth_on):
    db.create_user("admin", auth.hash_password("correct horse"), "admin")
    return _login(client, "admin", "correct horse")


def test_auth_disabled_acts_as_local_admin(client):
    assert client.get("/auth/config").json() == {"auth_enabled": False}
    assert client.get("/auth/me").json()["role"] == "admin"
    assert client.post("/auth/login", json={"username": "x", "password": "y"}).status_code == 400


def test_password_hashing():
    stored = auth.hash_password("s3cret-pass")
    assert stored.startswith("pbkdf2_sha256$") and "s3cret" not in stored
    assert auth.verify_password("s3cret-pass", stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password("x", "garbage")


def test_everything_needs_a_token_except_health_and_login(client, auth_on):
    assert client.get("/health").status_code == 200
    assert client.get("/auth/config").json() == {"auth_enabled": True}
    for method, path in [("get", "/projects"), ("get", "/dashboard"), ("post", "/query"), ("get", "/auth/me")]:
        r = getattr(client, method)(path, **({"json": {"question": "x"}} if method == "post" else {}))
        assert r.status_code == 401, path


def test_login_and_bad_tokens(client, admin_headers):
    assert client.get("/auth/me", headers=admin_headers).json()["username"] == "admin"
    assert client.post("/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert client.post("/auth/login", json={"username": "ghost", "password": "nope"}).status_code == 401

    token = admin_headers["Authorization"].split()[1]
    payload, sig = token.split(".")
    tampered = {"Authorization": f"Bearer {payload}x.{sig}"}
    assert client.get("/auth/me", headers=tampered).status_code == 401

    expired = auth.create_token(db.get_user_by_username("admin")["id"], ttl_hours=-1)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_viewer_sees_only_granted_projects(client, admin_headers, sample_projects, fake_claude):
    ids = {}
    for key, spec in sample_projects.items():
        ids[key] = client.post("/projects", json=spec, headers=admin_headers).json()["id"]
    client.post("/ingest/all", params={"auto_status": False}, headers=admin_headers)

    r = client.post("/users", headers=admin_headers, json={
        "username": "vera", "password": "viewer-pass", "role": "viewer", "project_ids": [ids["invoice"]],
    })
    assert r.status_code == 200, r.text
    assert r.json()["project_ids"] == [ids["invoice"]]
    vera = _login(client, "vera", "viewer-pass")

    assert [p["name"] for p in client.get("/projects", headers=vera).json()] == ["invoice-service"]
    assert [p["name"] for p in client.get("/dashboard", headers=vera).json()] == ["invoice-service"]
    assert client.get(f"/projects/{ids['demo']}", headers=vera).status_code == 404
    assert client.get(f"/projects/{ids['demo']}/runs", headers=vera).status_code == 404
    assert client.post("/query", json={"question": "x", "project_id": ids["demo"]}, headers=vera).status_code == 404

    # Cross-project question only reaches the projects vera may see.
    r = client.post("/query", json={"question": "What is pending?"}, headers=vera)
    assert r.status_code == 200
    assert {s["project_name"] for s in r.json()["sources"]} == {"invoice-service"}
    assert "demo-project" not in fake_claude.calls[-1]["user"]

    # Viewers can't change anything.
    assert client.post("/projects", json={"name": "x"}, headers=vera).status_code == 403
    assert client.post(f"/projects/{ids['invoice']}/reingest", headers=vera).status_code == 403
    assert client.post(f"/projects/{ids['invoice']}/status", headers=vera).status_code == 403
    assert client.delete(f"/projects/{ids['invoice']}", headers=vera).status_code == 403
    assert client.get("/users", headers=vera).status_code == 403

    # Admin widens access; takes effect on vera's existing token.
    vid = db.get_user_by_username("vera")["id"]
    client.patch(f"/users/{vid}", json={"project_ids": list(ids.values())}, headers=admin_headers)
    assert len(client.get("/projects", headers=vera).json()) == 2

    # Deleting the user invalidates their token.
    client.delete(f"/users/{vid}", headers=admin_headers)
    assert client.get("/projects", headers=vera).status_code == 401


def test_user_management_guards(client, admin_headers):
    me = client.get("/auth/me", headers=admin_headers).json()
    assert client.delete(f"/users/{me['id']}", headers=admin_headers).status_code == 400
    assert client.patch(f"/users/{me['id']}", json={"role": "viewer"}, headers=admin_headers).status_code == 400
    assert client.post("/users", json={"username": "x", "password": "short"}, headers=admin_headers).status_code == 422
    assert client.post("/users", json={"username": "x", "password": "long-enough", "role": "root"},
                       headers=admin_headers).status_code == 400
    assert client.post("/users", json={"username": "x", "password": "long-enough", "project_ids": [999]},
                       headers=admin_headers).status_code == 400


def test_bootstrap_admin_from_env(auth_on, monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_USERNAME", "boss")
    monkeypatch.setattr(cfg, "ADMIN_PASSWORD", "boss-password")
    auth.bootstrap_admin()
    user = db.get_user_by_username("boss")
    assert user["role"] == "admin" and auth.verify_password("boss-password", user["password_hash"])
    auth.bootstrap_admin()  # no-op once users exist
    assert db.count_users() == 1


def test_manage_cli(sample_projects, capsys, monkeypatch):
    from app import manage

    pid = db.create_project("cli-project")
    manage.main(["create-user", "cli-admin", "--role", "admin", "--password", "cli-password"])
    manage.main(["create-user", "cli-viewer", "--password", "cli-password"])
    manage.main(["grant", "cli-viewer", "cli-project"])
    assert db.user_project_ids(db.get_user_by_username("cli-viewer")["id"]) == [pid]
    manage.main(["list-users"])
    out = capsys.readouterr().out
    assert "cli-viewer" in out and "cli-project" in out

    monkeypatch.setattr(cfg, "AUTH_SECRET", "")
    with pytest.raises(SystemExit):
        manage.main(["create-token", "cli-admin"])
    monkeypatch.setattr(cfg, "AUTH_SECRET", "cli-secret")
    monkeypatch.setattr(auth, "_secret", None)
    manage.main(["create-token", "cli-admin", "--days", "1"])
    token = capsys.readouterr().out.strip()
    assert auth.read_token(token)["sub"] == db.get_user_by_username("cli-admin")["id"]
    auth._secret = None
