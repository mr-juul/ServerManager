from __future__ import annotations

import json

import pytest

from core.config import ServerConfig
from tests.test_web_control import FakeProcess, _client

KEY = {"X-API-Key": "abc123"}


def _as(user: str) -> dict[str, str]:
    return {**KEY, "X-SM-User": user}


def _add(manager, sid: str, owner: str, shared: list[str] | None = None) -> None:
    manager.configs[sid] = ServerConfig(
        id=sid, name=sid, script="a.bat", working_directory="C:/", game="valheim",
        executable_directory="C:/", owner_id=owner, shared_with=list(shared or []),
    )
    manager.processes[sid] = FakeProcess()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, manager = _client()
    # valheim-kirken (A): legacy, owned by owner-local
    _add(manager, "srv-b", "admin-local")
    _add(manager, "srv-c", "admin-local", ["member-local"])
    _add(manager, "srv-e", "member-local", ["admin-local"])
    return client, manager


def _ids(client, user):
    response = client.get("/api/v1/servers", headers=_as(user))
    assert response.status_code == 200
    return {s["id"] for s in response.json()["servers"]}


def test_legacy_config_defaults_to_owner_local():
    cfg = ServerConfig.from_dict({"id": "x", "script": "a.bat", "working_directory": "C:/", "name": "x"})
    assert cfg.owner_id == "owner-local"
    assert cfg.shared_with == []
    data = cfg.to_dict()
    assert data["owner_id"] == "owner-local"
    assert ServerConfig.from_dict(data).owner_id == "owner-local"


def test_owner_sees_all_servers(env):
    client, _ = env
    assert _ids(client, "owner-local") == {"valheim-kirken", "srv-b", "srv-c", "srv-e"}


def test_users_only_see_owned_or_shared(env):
    client, _ = env
    assert _ids(client, "admin-local") == {"srv-b", "srv-c", "srv-e"}
    assert _ids(client, "member-local") == {"srv-c", "srv-e"}


def test_unknown_user_header_is_rejected(env):
    client, _ = env
    response = client.get("/api/v1/servers", headers=_as("nobody"))
    assert response.status_code == 403


READ_ENDPOINTS = [
    "/api/v1/servers/{sid}",
    "/api/v1/servers/{sid}/logs",
    "/api/v1/servers/{sid}/players",
    "/api/v1/servers/{sid}/settings",
    "/api/v1/servers/{sid}/mods",
    "/api/v1/servers/{sid}/backups",
]

WRITE_ENDPOINTS = [
    ("post", "/api/v1/servers/{sid}/start", {}),
    ("post", "/api/v1/servers/{sid}/stop", {}),
    ("post", "/api/v1/servers/{sid}/restart", {}),
    ("patch", "/api/v1/servers/{sid}/settings", {"name": "hacked"}),
    ("post", "/api/v1/servers/{sid}/mods/m1/enable", {}),
    ("post", "/api/v1/servers/{sid}/mods/m1/disable", {}),
    ("post", "/api/v1/servers/{sid}/mods/m1/install", {}),
    ("post", "/api/v1/servers/{sid}/mods/m1/uninstall", {}),
    ("post", "/api/v1/servers/{sid}/mod-profile", {"profile": "p"}),
    ("post", "/api/v1/servers/{sid}/backups", {}),
    ("post", "/api/v1/servers/{sid}/backups/b1/restore", {}),
    ("delete", "/api/v1/servers/{sid}", {"confirm": "valheim-kirken"}),
]


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_read_denied_without_access(env, path):
    client, _ = env
    for user in ("admin-local", "member-local"):
        response = client.get(path.format(sid="valheim-kirken"), headers=_as(user))
        assert response.status_code == 404, (user, path)
        assert response.json()["error"] == "server_not_found"


@pytest.mark.parametrize("method,path,body", WRITE_ENDPOINTS)
def test_write_denied_without_access(env, method, path, body):
    client, manager = env
    before = (manager.started, manager.stopped, manager.restarted, manager.configs["valheim-kirken"].name)
    for user in ("admin-local", "member-local"):
        response = client.request(method, path.format(sid="valheim-kirken"), json=body, headers=_as(user))
        # Members lack manage/delete permissions (403); admins lack server access (404).
        assert response.status_code in {403, 404}, (user, path, response.status_code)
        if user == "admin-local":
            assert response.status_code == 404, path
    after = (manager.started, manager.stopped, manager.restarted, manager.configs["valheim-kirken"].name)
    assert before == after
    assert "valheim-kirken" in manager.configs


def test_access_to_one_server_does_not_grant_another(env):
    client, manager = env
    assert client.post("/api/v1/servers/srv-c/start", json={}, headers=_as("member-local")).status_code == 200
    started = manager.started
    assert client.post("/api/v1/servers/srv-b/start", json={}, headers=_as("member-local")).status_code == 404
    assert client.get("/api/v1/servers/srv-b/logs", headers=_as("member-local")).status_code == 404
    assert manager.started == started


def test_shared_user_can_view_and_control(env):
    client, _ = env
    assert client.get("/api/v1/servers/srv-c/logs", headers=_as("member-local")).status_code == 200
    assert client.post("/api/v1/servers/srv-c/stop", json={}, headers=_as("member-local")).status_code == 200
    assert client.post("/api/v1/servers/srv-c/restart", json={}, headers=_as("member-local")).status_code == 200


def test_shared_user_cannot_delete_even_with_permission(env):
    client, manager = env
    # admin-local has delete permission and explicit access to srv-e, but does not own it.
    response = client.request("DELETE", "/api/v1/servers/srv-e", json={"confirm": "srv-e"}, headers=_as("admin-local"))
    assert response.status_code == 403
    assert "srv-e" in manager.configs


def test_owner_can_delete_own_server(env):
    client, manager = env
    response = client.request("DELETE", "/api/v1/servers/srv-b", json={"confirm": "srv-b"}, headers=_as("admin-local"))
    assert response.status_code == 200
    assert response.json()["job_id"]


def test_jobs_are_scoped_to_creator(env):
    client, _ = env
    created = client.post("/api/v1/servers/srv-b/backups", json={}, headers=_as("admin-local"))
    assert created.status_code == 200
    job_id = created.json()["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}", headers=_as("admin-local")).status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}", headers=_as("member-local")).status_code == 404
    member_jobs = client.get("/api/v1/jobs", headers=_as("member-local")).json()["jobs"]
    assert all(job["job_id"] != job_id for job in member_jobs)
    assert client.get(f"/api/v1/jobs/{job_id}", headers=_as("owner-local")).status_code == 200


def test_session_user_keeps_full_local_access(env):
    client, _ = env
    client2, _ = _client()
    csrf = client2.post("/api/auth/login", json={"password": "secret123"}).json()["csrf_token"]
    response = client2.post("/api/v1/servers/valheim-kirken/start", json={}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200


def _wait(client, job_id, headers):
    import time

    job = {}
    for _ in range(100):
        job = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["job"]
        if job["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.05)
    return job


def test_tasks_are_scoped_to_creator(env):
    client, _ = env
    job_id = client.post("/api/v1/servers/srv-b/backups", json={}, headers=_as("admin-local")).json()["job_id"]
    assert client.get(f"/api/v1/tasks/{job_id}", headers=_as("admin-local")).status_code == 200
    assert client.get(f"/api/v1/tasks/{job_id}", headers=_as("owner-local")).status_code == 200
    denied = client.get(f"/api/v1/tasks/{job_id}", headers=_as("member-local"))
    assert denied.status_code == 404
    assert denied.json()["error"] == "task_not_found"
    member_tasks = client.get("/api/v1/tasks", headers=_as("member-local")).json()["tasks"]
    assert all(task["job_id"] != job_id for task in member_tasks)


def test_member_cannot_manage_backups_or_mods_on_shared_server(env):
    client, _ = env
    member = _as("member-local")
    assert client.get("/api/v1/servers/srv-c/backups", headers=member).status_code == 200
    assert client.post("/api/v1/servers/srv-c/backups", json={}, headers=member).status_code == 403
    assert client.post("/api/v1/servers/srv-c/backups/x/restore", json={}, headers=member).status_code == 403
    assert client.post("/api/v1/servers/srv-c/mods/m1/enable", json={}, headers=member).status_code == 403


def test_admin_with_access_can_manage_backups_and_mods(env):
    client, _ = env
    admin = _as("admin-local")
    assert client.post("/api/v1/servers/srv-b/backups", json={}, headers=admin).status_code == 200
    assert client.post("/api/v1/servers/srv-b/backups/x/restore", json={}, headers=admin).status_code == 200
    assert client.post("/api/v1/servers/srv-b/mods/m1/enable", json={}, headers=admin).status_code not in {403, 404}
    # No access to the owner's server despite the role permission.
    assert client.post("/api/v1/servers/valheim-kirken/backups", json={}, headers=admin).status_code == 404
    assert client.post("/api/v1/servers/valheim-kirken/mods/m1/enable", json={}, headers=admin).status_code == 404


def test_settings_patch_requires_server_access(env):
    client, manager = env
    assert client.patch("/api/v1/servers/srv-b/settings", json={"name": "x"}, headers=_as("member-local")).status_code == 404
    assert client.patch("/api/v1/servers/srv-b/settings", json={"name": "x"}, headers=_as("admin-local")).status_code != 404
    assert client.patch("/api/v1/servers/srv-c/settings", json={"name": "x"}, headers=_as("member-local")).status_code != 404
    assert manager.configs["srv-c"].owner_id == "admin-local"


def test_inactive_user_is_rejected(env, monkeypatch):
    from core.access_control import UserManager

    original = UserManager.__init__

    def patched(self, storage_path=None):
        original(self, storage_path=storage_path)
        self._users["member-local"].active = False

    monkeypatch.setattr(UserManager, "__init__", patched)
    client, _ = _client()
    response = client.get("/api/v1/servers", headers=_as("member-local"))
    assert response.status_code == 403


@pytest.fixture
def restore_env(env, tmp_path):
    client, manager = env
    world = tmp_path / "w"
    world.mkdir()
    (world / "keep.db").write_text("data")
    (tmp_path / "outside-world-1.tar.zst").write_bytes(b"x")
    (world / "srv-c-world-1.tar.zst").write_bytes(b"x")
    (world / "keep.zip").write_bytes(b"x")
    manager.configs["srv-b"].world_directory = str(world)
    return client, manager, world


@pytest.mark.parametrize(
    "backup_id",
    [
        "..%2Foutside-world-1.tar.zst",
        "..%5Coutside-world-1.tar.zst",
        "srv-b-world-..%2F..%2Fx.tar.zst",
        "srv-b-world-1.tar.zst%00.txt",
        "srv-c-world-1.tar.zst",
        "srv-bb-world-1.tar.zst",
        "srv-b-world-1.zip",
        "keep.zip",
        "keep.db",
        "srv-b-world-.tar.zst",
    ],
)
def test_restore_rejects_invalid_backup_ids(restore_env, backup_id):
    client, _, world = restore_env
    admin = _as("admin-local")
    response = client.post(f"/api/v1/servers/srv-b/backups/{backup_id}/restore", json={}, headers=admin)
    if response.status_code != 200:
        assert response.status_code in {400, 404}
        return
    job = _wait(client, response.json()["job_id"], admin)
    assert job["status"] == "FAILED"
    assert job["error"] == "invalid_backup_id"
    assert (world / "keep.db").read_text() == "data"
    assert not list(world.glob("srv-b-world-*.tar.zst"))



# --- grant / revoke access ---

def _grant(client, user, sid, target):
    return client.post(f"/api/v1/servers/{sid}/access", headers=_as(user), json={"user_id": target})


def _revoke(client, user, sid, target):
    return client.delete(f"/api/v1/servers/{sid}/access/{target}", headers=_as(user))


def test_owner_can_grant_and_revoke_with_immediate_enforcement(env):
    client, manager = env
    assert client.get("/api/v1/servers/srv-b", headers=_as("member-local")).status_code == 404
    r = _grant(client, "admin-local", "srv-b", "member-local")
    assert r.status_code == 200 and r.json()["shared_with"] == ["member-local"]
    assert manager.configs["srv-b"].shared_with == ["member-local"]
    assert client.get("/api/v1/servers/srv-b", headers=_as("member-local")).status_code == 200
    r = _revoke(client, "admin-local", "srv-b", "member-local")
    assert r.status_code == 200 and r.json()["shared_with"] == []
    assert client.get("/api/v1/servers/srv-b", headers=_as("member-local")).status_code == 404
    assert client.post("/api/v1/servers/srv-b/start", headers=_as("member-local")).status_code == 404


def test_global_owner_can_administer_any_server(env):
    client, _ = env
    assert _grant(client, "owner-local", "srv-b", "member-local").status_code == 200
    assert client.get("/api/v1/servers/srv-b/access", headers=_as("owner-local")).json()["shared_with"] == ["member-local"]


def test_shared_user_cannot_administer_access(env):
    client, _ = env
    assert _grant(client, "member-local", "srv-c", "admin-local").status_code == 403
    assert _revoke(client, "member-local", "srv-c", "member-local").status_code == 403
    assert client.get("/api/v1/servers/srv-c/access", headers=_as("member-local")).status_code == 403


def test_non_participant_cannot_administer_and_gets_404(env):
    client, _ = env
    assert _grant(client, "member-local", "srv-b", "member-local").status_code == 404
    assert _revoke(client, "member-local", "srv-b", "admin-local").status_code == 404


def test_cannot_grant_self(env):
    client, manager = env
    assert _grant(client, "admin-local", "srv-b", "admin-local").status_code == 400
    assert _grant(client, "member-local", "srv-b", "member-local").status_code == 404
    assert manager.configs["srv-b"].shared_with == []


def test_grant_validation(env):
    client, _ = env
    assert _grant(client, "admin-local", "srv-b", "nobody").status_code == 404
    assert _grant(client, "admin-local", "srv-b", "   ").status_code == 400
    r = client.post("/api/v1/servers/srv-b/access", headers=_as("admin-local"), json={"user_id": 5})
    assert r.status_code == 400 and r.json()["success"] is False
    r = client.post("/api/v1/servers/srv-b/access", headers=_as("admin-local"), content=b"nope")
    assert r.status_code == 400
    assert _grant(client, "admin-local", "srv-b", "owner-local").status_code == 400
    assert _grant(client, "admin-local", "nope-srv", "member-local").status_code == 404


def test_duplicate_grant_and_missing_revoke(env):
    client, _ = env
    assert _grant(client, "admin-local", "srv-c", "member-local").status_code == 409
    assert _revoke(client, "admin-local", "srv-c", "owner-local").status_code == 404


def test_grant_does_not_leak_to_other_servers(env):
    client, _ = env
    _grant(client, "admin-local", "srv-b", "member-local")
    assert _ids(client, "member-local") == {"srv-b", "srv-c", "srv-e"}
    assert client.get("/api/v1/servers/valheim-kirken", headers=_as("member-local")).status_code == 404


def test_owner_can_provision_user_grant_access_and_user_can_log_in(env):
    client, _ = env
    created = client.post(
        "/api/v1/users",
        headers=_as("owner-local"),
        json={"user_id": "new-member", "name": "", "password": "secure-pass-123"},
    )
    assert created.status_code == 200
    assert created.json()["user"] == {
        "id": "new-member",
        "name": "new-member",
        "role": "MEMBER",
        "permissions": ["control_servers", "view_servers"],
        "active": True,
    }
    assert _grant(client, "owner-local", "srv-b", "new-member").status_code == 200

    login = client.post(
        "/api/auth/login",
        json={"user_id": "new-member", "password": "secure-pass-123"},
    )
    assert login.status_code == 200
    servers = client.get("/api/v1/servers")
    assert servers.status_code == 200
    assert {server["id"] for server in servers.json()["servers"]} == {"srv-b"}


def test_user_provisioning_requires_manage_users_and_valid_input(env):
    client, _ = env
    payload = {"user_id": "new-member", "password": "secure-pass-123"}
    assert client.post("/api/v1/users", headers=_as("admin-local"), json=payload).status_code == 403
    assert client.post("/api/v1/users", headers=_as("member-local"), json=payload).status_code == 403

    assert client.post("/api/v1/users", headers=_as("owner-local"), json={"user_id": "x", "password": "short"}).status_code == 400
    invalid_role = client.post(
        "/api/v1/users",
        headers=_as("owner-local"),
        json={"user_id": "new-member", "password": "secure-pass-123", "role": "OWNER"},
    )
    assert invalid_role.status_code == 400
    assert invalid_role.json()["error"] == "invalid_role"


def test_provisioned_user_and_password_hash_survive_app_restart(tmp_path, monkeypatch):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from core.config import AppSettings
    from core.web_control import create_web_app
    from tests.test_web_control import FakeManager

    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    manager = FakeManager()
    settings = AppSettings(
        web_control_enabled=True,
        web_control_password_hash=PasswordHasher().hash("owner-password"),
    )

    class Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    def create_client():
        app = create_web_app(settings, manager, Logger(), app_root=tmp_path)
        return TestClient(app)

    first_client = create_client()
    password = "secure-pass-123"
    created = first_client.post(
        "/api/v1/users",
        headers=_as("owner-local"),
        json={"user_id": "persistent-user", "password": password},
    )
    assert created.status_code == 200
    stored = (tmp_path / "config" / "users.json").read_text(encoding="utf-8")
    assert password not in stored
    saved_hash = json.loads(stored)["users"][0]["password_hash"]
    assert PasswordHasher().verify(saved_hash, password)

    restarted_client = create_client()
    login = restarted_client.post("/api/auth/login", json={"user_id": "persistent-user", "password": password})
    assert login.status_code == 200


def test_grant_requires_auth_and_persists(monkeypatch):
    from core.config import AppSettings
    from core.web_control import create_web_app
    from fastapi.testclient import TestClient
    from tests.test_web_control import FakeManager
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    calls = []
    manager = FakeManager()
    _add(manager, "srv-b", "admin-local")

    class L:
        def __getattr__(self, _n):
            return lambda *a, **k: None

    client = TestClient(create_web_app(AppSettings(web_control_enabled=True), manager, L(), lambda: calls.append(1)))
    assert client.post("/api/v1/servers/srv-b/access", json={"user_id": "member-local"}).status_code == 401
    assert calls == []
    _grant(client, "admin-local", "srv-b", "member-local")
    _revoke(client, "admin-local", "srv-b", "member-local")
    assert len(calls) == 2
