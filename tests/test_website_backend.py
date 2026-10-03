from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("SM_API_KEY", "website-backend-test-key")

from fastapi.testclient import TestClient

from website_backend import app as portal
from website_backend.server_manager_client import ServerManagerClient


@pytest.fixture
def portal_client(monkeypatch):
    portal.sessions.clear()
    calls = []

    def verify(user_id: str, password: str) -> bool:
        return user_id == "alice" and password == "correct-password"

    def call(self, method: str, path: str, payload: dict | None = None) -> dict:
        calls.append((self.user_id, method, path, payload))
        if path == "/api/v1/servers":
            return {"success": True, "servers": [{"id": "owned"}]}
        if path == "/api/v1/servers/owned":
            return {"success": True, "server": {"id": "owned", "name": "Owned", "status": "running", "pid": 42, "uptime_seconds": 120, "owner_id": "alice"}}
        if path == "/api/v1/invitations":
            if method == "GET":
                return {"success": True, "invitations": [{"id": "inv-1", "target": "bob", "role": "MEMBER", "status": "pending"}]}
            if method == "POST":
                return {"success": True, "invitation": {"id": "inv-2", "target": "charlie", "role": "ADMIN", "status": "pending"}}
        if path.endswith("/access"):
            return {"success": True, "server_id": "owned", "owner_id": "alice", "shared_with": ["bob"]}
        if path == "/api/v1/invitations/inv-1" and method == "DELETE":
            return {"success": True, "invitation_id": "inv-1"}
        return {"success": True}

    monkeypatch.setattr(portal, "_verify_password_against_server_manager", verify)
    monkeypatch.setattr(ServerManagerClient, "_call", call)
    return TestClient(portal.app), calls


def test_login_requires_user_id_and_password(portal_client):
    client, _ = portal_client
    assert client.post("/api/login", json={"password": "correct-password"}).status_code == 400
    assert client.post("/api/login", json={"user_id": "alice"}).status_code == 400


def test_session_identity_scopes_proxy_calls_and_ignores_browser_identity(portal_client):
    client, calls = portal_client
    response = client.post("/api/login", json={"user_id": "alice", "password": "correct-password"})
    assert response.status_code == 200
    assert next(iter(portal.sessions.values()))["user_id"] == "alice"

    servers = client.get("/api/servers", headers={"X-SM-User": "attacker"})
    assert servers.status_code == 200
    assert servers.json()["servers"] == [{"id": "owned"}]
    assert calls == [("alice", "GET", "/api/v1/servers", None)]


def test_access_proxy_routes_return_payloads_and_use_session_identity(portal_client):
    client, calls = portal_client
    client.post("/api/login", json={"user_id": "alice", "password": "correct-password"})

    listing = client.get("/api/servers/owned/access")
    granted = client.post("/api/servers/owned/access", json={"user_id": "charlie"})
    revoked = client.delete("/api/servers/owned/access/bob")

    assert listing.json()["shared_with"] == ["bob"]
    assert granted.json()["owner_id"] == "alice"
    assert revoked.json()["success"] is True
    assert calls == [
        ("alice", "GET", "/api/v1/servers/owned/access", None),
        ("alice", "POST", "/api/v1/servers/owned/access", {"user_id": "charlie"}),
        ("alice", "DELETE", "/api/v1/servers/owned/access/bob", None),
    ]


def test_user_provisioning_proxy_forwards_session_identity(portal_client):
    client, calls = portal_client
    client.post("/api/login", json={"user_id": "alice", "password": "correct-password"})

    response = client.post(
        "/api/users",
        json={"user_id": "new-member", "name": "New Member", "password": "secure-pass-123"},
    )

    assert response.status_code == 200
    assert calls == [(
        "alice",
        "POST",
        "/api/v1/users",
        {"user_id": "new-member", "name": "New Member", "password": "secure-pass-123"},
    )]


def test_game_setup_proxy_forwards_session_identity(portal_client):
    client, calls = portal_client
    client.post("/api/login", json={"user_id": "alice", "password": "correct-password"})

    response = client.post("/api/games/valheim/setup", json={})

    assert response.status_code == 200
    assert calls == [(
        "alice",
        "POST",
        "/api/v1/games/valheim/setup",
        {},
    )]


def test_server_details_proxy_forwards_session_identity(portal_client):
    client, calls = portal_client
    client.post("/api/login", json={"user_id": "alice", "password": "correct-password"})

    response = client.get("/api/servers/owned")

    assert response.status_code == 200
    assert response.json()["server"]["pid"] == 42
    assert calls == [(
        "alice",
        "GET",
        "/api/v1/servers/owned",
        None,
    )]


def test_invitations_proxy_routes_use_session_identity(portal_client):
    client, calls = portal_client
    client.post("/api/login", json={"user_id": "alice", "password": "correct-password"})

    listing = client.get("/api/invitations")
    created = client.post("/api/invitations", json={"target": "charlie", "role": "ADMIN"})
    deleted = client.delete("/api/invitations/inv-1")

    assert listing.status_code == 200
    assert listing.json()["invitations"][0]["id"] == "inv-1"
    assert created.status_code == 200
    assert created.json()["invitation"]["target"] == "charlie"
    assert deleted.status_code == 200
    assert calls == [
        ("alice", "GET", "/api/v1/invitations", None),
        ("alice", "POST", "/api/v1/invitations", {"target": "charlie", "role": "ADMIN"}),
        ("alice", "DELETE", "/api/v1/invitations/inv-1", None),
    ]


def test_players_proxy_route_uses_session_identity(portal_client):
    client, calls = portal_client
    client.post("/api/login", json={"user_id": "alice", "password": "correct-password"})

    response = client.get("/api/servers/owned/players")

    assert response.status_code == 200
    assert calls == [
        ("alice", "GET", "/api/v1/servers/owned/players", None),
    ]


def test_mods_proxy_routes_use_session_identity(portal_client):
    client, calls = portal_client
    client.post("/api/login", json={"user_id": "alice", "password": "correct-password"})

    listing = client.get("/api/mods", params={"game": "valheim"})
    server_mods = client.get("/api/servers/owned/mods")
    enable = client.post("/api/servers/owned/mods/mod-1/enable")

    assert listing.status_code == 200
    assert server_mods.status_code == 200
    assert enable.status_code == 200
    assert calls == [
        ("alice", "GET", "/api/v1/mods?game=valheim", None),
        ("alice", "GET", "/api/v1/servers/owned/mods", None),
        ("alice", "POST", "/api/v1/servers/owned/mods/mod-1/enable", {}),
    ]


def test_backups_proxy_routes_use_session_identity(portal_client):
    client, calls = portal_client
    client.post("/api/login", json={"user_id": "alice", "password": "correct-password"})

    listing = client.get("/api/servers/owned/backups")
    create = client.post("/api/servers/owned/backups")
    restore = client.post("/api/servers/owned/backups/backup-id/restore")

    assert listing.status_code == 200
    assert create.status_code == 200
    assert restore.status_code == 200
    assert calls == [
        ("alice", "GET", "/api/v1/servers/owned/backups", None),
        ("alice", "POST", "/api/v1/servers/owned/backups", {}),
        ("alice", "POST", "/api/v1/servers/owned/backups/backup-id/restore", {}),
    ]


def test_server_manager_client_preserves_structured_http_errors(monkeypatch):
    from website_backend.server_manager_client import ServerManagerClientError

    def raise_forbidden(_request, timeout):
        raise portal.urllib.error.HTTPError(
            "http://manager.test/api/v1/users",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"success": false, "error": "forbidden", "message": "Access denied"}'),
        )

    import io

    monkeypatch.setattr(portal.urllib.request, "urlopen", raise_forbidden)
    with pytest.raises(ServerManagerClientError) as error:
        ServerManagerClient("http://manager.test", "api-key").get_users()

    assert error.value.error == "forbidden"
    assert error.value.message == "Access denied"
    assert error.value.status_code == 403


def test_proxy_routes_block_unauthorized_requests(portal_client):
    client, calls = portal_client
    assert client.get("/api/servers").status_code == 401
    assert client.get("/api/servers/owned").status_code == 401
    assert client.post("/api/games/valheim/setup", json={}).status_code == 401
    assert client.get("/api/invitations").status_code == 401
    assert client.post("/api/invitations", json={"target": "charlie"}).status_code == 401
    assert client.delete("/api/invitations/inv-1").status_code == 401
    assert client.get("/api/servers/owned/access").status_code == 401
    assert client.post("/api/servers/owned/access", json={"user_id": "bob"}).status_code == 401
    assert client.delete("/api/servers/owned/access/bob").status_code == 401
    assert calls == []


def test_upstream_login_and_proxy_requests_include_required_identity(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"success": true}'

    def open_request(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(portal.urllib.request, "urlopen", open_request)
    monkeypatch.setenv("SM_API_BASE_URL", "http://manager.test")

    assert portal._verify_password_against_server_manager("alice", "secret") is True
    login_request = requests[-1][0]
    assert login_request.full_url == "http://manager.test/api/auth/login"
    assert json.loads(login_request.data) == {"user_id": "alice", "password": "secret"}

    ServerManagerClient("http://manager.test", "api-key", user_id="alice").get_servers()
    proxy_request = requests[-1][0]
    assert dict((key.lower(), value) for key, value in proxy_request.header_items())["x-sm-user"] == "alice"