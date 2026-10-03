from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.config import ServerConfig
from tests.test_web_control import FakeProcess, _client


def _add(manager, sid, owner, shared=None):
    manager.configs[sid] = ServerConfig(
        id=sid, name=sid, script="a.bat", working_directory="C:/", game="valheim",
        executable_directory="C:/", owner_id=owner, shared_with=list(shared or []),
    )
    manager.processes[sid] = FakeProcess()


def _backend_users(client):
    # The app closure holds one UserManager; reach it through the app state hook below.
    return client.app.state.user_manager


def _session_for(client_factory, user_id, password):
    c = TestClient(client_factory.app)
    r = c.post("/api/auth/login", json={"user_id": user_id, "password": password})
    return c, r


@pytest.fixture
def env():
    client, manager = _client()
    um = client.app.state.user_manager
    um.set_password("admin-local", "adminpass1")
    um.set_password("member-local", "memberpass1")
    _add(manager, "srv-a", "admin-local")
    _add(manager, "srv-s", "admin-local", ["member-local"])
    _add(manager, "srv-m", "member-local")
    return client, manager, um


def _login(client, user_id, password):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"user_id": user_id, "password": password})
    assert r.status_code == 200, r.text
    return c


def _ids(c, headers=None):
    r = c.get("/api/v1/servers", headers=headers or {})
    assert r.status_code == 200
    return {s["id"] for s in r.json()["servers"]}


def test_sessions_resolve_to_distinct_users(env):
    client, _, _ = env
    admin = _login(client, "admin-local", "adminpass1")
    member = _login(client, "member-local", "memberpass1")
    owner = _login(client, "owner-local", "secret123")
    assert admin.get("/api/v1/me").json()["user"]["id"] == "admin-local"
    assert member.get("/api/v1/me").json()["user"]["id"] == "member-local"
    assert owner.get("/api/v1/me").json()["user"]["id"] == "owner-local"


def test_owner_shared_and_unauthorized(env):
    client, _, _ = env
    admin = _login(client, "admin-local", "adminpass1")
    member = _login(client, "member-local", "memberpass1")
    assert _ids(admin) == {"srv-a", "srv-s", "srv-m"} - {"srv-m"}
    assert _ids(member) == {"srv-s", "srv-m"}
    assert member.get("/api/v1/servers/srv-a").status_code == 404
    assert member.get("/api/v1/servers/srv-s").status_code == 200
    assert admin.get("/api/v1/servers/srv-m").status_code == 404


def test_shared_user_needs_no_user_header(env):
    client, _, _ = env
    member = _login(client, "member-local", "memberpass1")
    assert "srv-s" in _ids(member)


def test_user_header_cannot_change_session_identity(env):
    client, _, _ = env
    member = _login(client, "member-local", "memberpass1")
    spoof = {"X-SM-User": "owner-local"}
    assert _ids(member, spoof) == {"srv-s", "srv-m"}
    assert member.get("/api/v1/me", headers=spoof).json()["user"]["id"] == "member-local"
    assert member.get("/api/v1/servers/srv-a", headers=spoof).status_code == 404


def test_revoke_after_login_takes_effect(env):
    client, manager, _ = env
    admin = _login(client, "admin-local", "adminpass1")
    member = _login(client, "member-local", "memberpass1")
    assert member.get("/api/v1/servers/srv-s").status_code == 200
    csrf = admin.get("/api/auth/status").json()["csrf_token"]
    r = admin.delete("/api/v1/servers/srv-s/access/member-local", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert member.get("/api/v1/servers/srv-s").status_code == 404
    assert "srv-s" not in _ids(member)


def test_grant_via_session_gives_access(env):
    client, _, _ = env
    admin = _login(client, "admin-local", "adminpass1")
    member = _login(client, "member-local", "memberpass1")
    assert member.get("/api/v1/servers/srv-a").status_code == 404
    csrf = admin.get("/api/auth/status").json()["csrf_token"]
    r = admin.post("/api/v1/servers/srv-a/access", json={"user_id": "member-local"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert member.get("/api/v1/servers/srv-a").status_code == 200


@pytest.mark.parametrize("uid,pw", [
    ("admin-local", "bad"), ("admin-local", "secret123"), ("nobody", "x"), ("owner-local", "adminpass1"),
])
def test_login_rejects_wrong_credentials(env, uid, pw):
    client, _, _ = env
    r = TestClient(client.app).post("/api/auth/login", json={"user_id": uid, "password": pw})
    assert r.status_code == 401


def test_login_rejects_unprovisioned_user(env):
    client, _, um = env
    um._users["member-local"].password_hash = ""
    r = TestClient(client.app).post("/api/auth/login", json={"user_id": "member-local", "password": ""})
    assert r.status_code == 401


def test_deactivated_user_session_rejected(env):
    client, _, um = env
    member = _login(client, "member-local", "memberpass1")
    um._users["member-local"].active = False
    assert member.get("/api/v1/servers").status_code == 401


def test_non_owner_session_cannot_use_legacy_unscoped_api(env):
    client, _, _ = env
    member = _login(client, "member-local", "memberpass1")
    assert member.get("/api/servers").status_code == 403
    assert member.get("/api/servers/srv-a/logs").status_code == 403


def test_default_login_remains_owner(env):
    client, _, _ = env
    c = TestClient(client.app)
    assert c.post("/api/auth/login", json={"password": "secret123"}).status_code == 200
    assert c.get("/api/v1/me").json()["user"]["id"] == "owner-local"
