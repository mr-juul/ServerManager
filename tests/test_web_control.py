from __future__ import annotations

from pathlib import Path

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from core.config import AppSettings, ServerConfig
from core.server_process import ServerStatus
from core.web_control import create_web_app


class FakeProcess:
    def __init__(self, status: ServerStatus = ServerStatus.OFFLINE):
        self.status = status
        self.pid = None
        self.uptime_seconds = 0
        self.recent_output = [
            "Server booting",
            "-password hunter2",
            "token abc123",
        ]


class FakeManager:
    def __init__(self):
        cfg = ServerConfig(id="valheim-kirken", name="Kirken", script="a.bat", working_directory="C:/", game="valheim", executable_directory="C:/")
        self.configs = {cfg.id: cfg}
        self.processes = {cfg.id: FakeProcess()}
        self.started = 0
        self.stopped = 0
        self.restarted = 0

    def start(self, server_id: str):
        self.started += 1
        self.processes[server_id].status = ServerStatus.ONLINE

    def stop(self, server_id: str):
        self.stopped += 1
        self.processes[server_id].status = ServerStatus.OFFLINE

    def restart(self, server_id: str):
        self.restarted += 1
        self.processes[server_id].status = ServerStatus.ONLINE

    def _create_process(self, _config: ServerConfig):
        return FakeProcess()


def _client(configured: bool = True) -> tuple[TestClient, FakeManager]:
    settings = AppSettings(web_control_enabled=True, web_control_port=8080, web_control_bind_address="127.0.0.1")
    if configured:
        settings.web_control_password_hash = PasswordHasher().hash("secret123")
    manager = FakeManager()

    class DummyLogger:
        def info(self, *_args, **_kwargs):
            return None

        def warning(self, *_args, **_kwargs):
            return None

        def exception(self, *_args, **_kwargs):
            return None

    app = create_web_app(settings, manager, DummyLogger())
    return TestClient(app), manager


def _login(client: TestClient, password: str = "secret123") -> str:
    response = client.post("/api/auth/login", json={"password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_login_success_and_authenticated_access():
    client, _ = _client()
    csrf = _login(client)
    assert csrf
    response = client.get("/api/servers")
    assert response.status_code == 200
    assert response.json()["servers"][0]["id"] == "valheim-kirken"


def test_login_failure_then_rate_limited():
    client, _ = _client()
    for _ in range(3):
        response = client.post("/api/auth/login", json={"password": "bad"})
        assert response.status_code in {401, 429}
    response = client.post("/api/auth/login", json={"password": "bad"})
    assert response.status_code == 429


def test_unauthenticated_api_is_blocked():
    client, _ = _client()
    response = client.get("/api/servers")
    assert response.status_code == 401


def test_csrf_required_for_server_actions():
    client, _ = _client()
    _login(client)
    response = client.post("/api/servers/valheim-kirken/start", json={})
    assert response.status_code == 403


def test_start_stop_restart_actions_use_manager():
    client, manager = _client()
    csrf = _login(client)
    start = client.post("/api/servers/valheim-kirken/start", json={}, headers={"X-CSRF-Token": csrf})
    stop = client.post("/api/servers/valheim-kirken/stop", json={}, headers={"X-CSRF-Token": csrf})
    restart = client.post("/api/servers/valheim-kirken/restart", json={}, headers={"X-CSRF-Token": csrf})
    assert start.status_code == 200
    assert stop.status_code == 200
    assert restart.status_code == 200
    assert manager.started == 1
    assert manager.stopped == 1
    assert manager.restarted == 1


def test_server_not_found_is_rejected():
    client, _ = _client()
    csrf = _login(client)
    response = client.post("/api/servers/missing/start", json={}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 404
    assert response.json()["error"] == "server_not_found"


def test_logs_are_sanitized():
    client, _ = _client()
    _login(client)
    response = client.get("/api/servers/valheim-kirken/logs")
    assert response.status_code == 200
    lines = response.json()["lines"]
    assert "hunter2" not in "\n".join(lines)
    assert "abc123" not in "\n".join(lines)


def test_logout_invalidates_session():
    client, _ = _client()
    csrf = _login(client)
    before = client.get("/api/servers")
    assert before.status_code == 200

    logout = client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200

    after = client.get("/api/servers")
    assert after.status_code == 401


def test_first_time_setup_flow():
    client, _ = _client(configured=False)
    status = client.get("/api/auth/status")
    assert status.status_code == 200
    assert status.json()["configured"] is False

    setup = client.post("/api/auth/setup", json={"password": "newsecret1"})
    assert setup.status_code == 200

    login = client.post("/api/auth/login", json={"password": "newsecret1"})
    assert login.status_code == 200


def test_login_page_has_security_headers_and_csp():
    client, _ = _client()
    response = client.get("/login")
    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_static_web_assets_are_served():
    client, _ = _client()
    css = client.get("/web-static/web_control.css")
    js = client.get("/web-static/web_control.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "--bg" in css.text
    assert "bootstrap()" in js.text


def test_v1_servers_with_session_auth():
    client, _ = _client()
    _login(client)
    response = client.get("/api/v1/servers")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["servers"][0]["id"] == "valheim-kirken"
    assert "status" in payload["servers"][0]


def test_v1_start_accepts_api_key_without_csrf(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, manager = _client()
    response = client.post("/api/v1/servers/valheim-kirken/start", json={}, headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["status"] in {"running", "starting"}
    assert manager.started == 1


def test_v1_unknown_server_returns_structured_error(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    response = client.get("/api/v1/servers/missing", headers={"X-API-Key": "abc123"})
    assert response.status_code == 404
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"] == "server_not_found"


def test_v1_games_catalog(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    response = client.get("/api/v1/games", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert any(game["id"] == "valheim" for game in payload["games"])


def test_v1_create_schema_for_valheim(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    response = client.get("/api/v1/games/valheim/create-schema", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["schema"]["supported"] is False
    assert "appen" in str(payload["schema"].get("message", "")).lower()


def test_v1_create_server_from_web(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    install = tmp_path / "valheim"
    install.mkdir()
    (install / "valheim_server.exe").write_text("", encoding="utf-8")

    client, manager = _client()
    manager.configs["valheim-kirken"].executable_directory = str(install)
    app = create_web_app(AppSettings(web_control_enabled=True, web_control_port=8080, web_control_bind_address="127.0.0.1"), manager, type("L", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None, "exception": lambda *a, **k: None})(), app_root=tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/v1/servers",
        json={"game": "valheim", "name": "New Kirken", "password": "secret123", "world_mode": "new"},
        headers={"X-API-Key": "abc123"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["job_id"]

    job_response = client.get(f"/api/v1/jobs/{payload['job_id']}", headers={"X-API-Key": "abc123"})
    assert job_response.status_code == 200
    job_payload = job_response.json()["job"]
    assert job_payload["status"] in {"QUEUED", "RUNNING", "COMPLETED"}

    for _ in range(30):
        job_response = client.get(f"/api/v1/jobs/{payload['job_id']}", headers={"X-API-Key": "abc123"})
        assert job_response.status_code == 200
        job_payload = job_response.json()["job"]
        if job_payload["status"] == "COMPLETED":
            break
    assert job_payload["status"] == "COMPLETED"
    assert job_payload["result"]["server"]["game"] == "valheim"
    assert len(manager.configs) >= 2


def test_v1_players_endpoint(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, manager = _client()
    manager.processes["valheim-kirken"].recent_output.extend([
        "2 / 10 players",
        "Player Oscar connected",
        "Player Player2 connected",
    ])
    response = client.get("/api/v1/servers/valheim-kirken/players", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["supported"] is True
    assert payload["online"] >= 0
    assert payload["max"] in {None, 10}


def test_v1_patch_settings(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, manager = _client()
    response = client.patch(
        "/api/v1/servers/valheim-kirken/settings",
        json={"name": "Kirken New", "public": False, "crossplay": False},
        headers={"X-API-Key": "abc123"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert manager.configs["valheim-kirken"].name == "Kirken New"
    assert manager.configs["valheim-kirken"].public is False
    assert manager.configs["valheim-kirken"].crossplay is False


def test_v1_settings_returns_field_schema(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    response = client.get("/api/v1/servers/valheim-kirken/settings", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "field_schema" in payload
    assert any(field["id"] == "name" for field in payload["field_schema"])


def test_v1_backups_includes_restore_history(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    response = client.get("/api/v1/servers/valheim-kirken/backups", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "restore_history" in payload


def test_v1_restore_job_failure_creates_history_event(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    response = client.post(
        "/api/v1/servers/valheim-kirken/backups/not-found.tar.zst/restore",
        json={},
        headers={"X-API-Key": "abc123"},
    )
    assert response.status_code == 200
    job_id = response.json().get("job_id")
    assert job_id

    for _ in range(30):
        job_response = client.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": "abc123"})
        assert job_response.status_code == 200
        job = job_response.json()["job"]
        if job["status"] in {"COMPLETED", "FAILED"}:
            break
    assert job["status"] == "FAILED"

    backups_response = client.get("/api/v1/servers/valheim-kirken/backups", headers={"X-API-Key": "abc123"})
    assert backups_response.status_code == 200
    history = backups_response.json().get("restore_history", [])
    assert history
    assert history[0].get("status") in {"failed", "completed"}


def _wait_job(client, job_id, headers):
    import time

    job = {}
    for _ in range(100):
        job = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["job"]
        if job["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.05)
    return job


def _delete_client(monkeypatch, tmp_path, status=ServerStatus.OFFLINE):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, manager = _client()
    world = tmp_path / "worlds"
    world.mkdir()
    (world / "w.db").write_text("data")
    manager.configs["valheim-kirken"].world_directory = str(world)
    manager.processes["valheim-kirken"].status = status
    return client, manager, world


def test_v1_delete_creates_safety_backup_then_removes(monkeypatch, tmp_path):
    client, manager, world = _delete_client(monkeypatch, tmp_path)
    headers = {"X-API-Key": "abc123"}
    response = client.request("DELETE", "/api/v1/servers/valheim-kirken", json={"confirm": "valheim-kirken"}, headers=headers)
    assert response.status_code == 200
    job = _wait_job(client, response.json()["job_id"], headers)
    assert job["status"] == "COMPLETED"
    assert "valheim-kirken" not in manager.configs
    assert "valheim-kirken" not in manager.processes
    assert list(world.glob("valheim-kirken-world-*.tar.zst"))
    assert (world / "w.db").exists()
    assert job["result"]["safety_backup_id"]


def test_v1_delete_stops_running_server_first(monkeypatch, tmp_path):
    client, manager, _ = _delete_client(monkeypatch, tmp_path, ServerStatus.ONLINE)
    headers = {"X-API-Key": "abc123"}
    response = client.request("DELETE", "/api/v1/servers/valheim-kirken", json={"confirm": "valheim-kirken"}, headers=headers)
    job = _wait_job(client, response.json()["job_id"], headers)
    assert job["status"] == "COMPLETED"
    assert manager.stopped == 1


def test_v1_delete_aborts_without_backup(monkeypatch, tmp_path):
    client, manager, world = _delete_client(monkeypatch, tmp_path)
    manager.configs["valheim-kirken"].world_directory = str(tmp_path / "missing")
    headers = {"X-API-Key": "abc123"}
    response = client.request("DELETE", "/api/v1/servers/valheim-kirken", json={"confirm": "valheim-kirken"}, headers=headers)
    job = _wait_job(client, response.json()["job_id"], headers)
    assert job["status"] == "FAILED"
    assert "valheim-kirken" in manager.configs
    assert "valheim-kirken" in manager.processes


def test_v1_delete_requires_confirmation(monkeypatch, tmp_path):
    client, manager, _ = _delete_client(monkeypatch, tmp_path)
    response = client.request("DELETE", "/api/v1/servers/valheim-kirken", json={}, headers={"X-API-Key": "abc123"})
    assert response.status_code == 400
    assert response.json()["error"] == "confirmation_required"
    assert "valheim-kirken" in manager.configs


def test_v1_delete_forbidden_for_member(monkeypatch, tmp_path):
    client, manager, _ = _delete_client(monkeypatch, tmp_path)
    response = client.request(
        "DELETE",
        "/api/v1/servers/valheim-kirken",
        json={"confirm": "valheim-kirken"},
        headers={"X-API-Key": "abc123", "X-SM-User": "member-local"},
    )
    assert response.status_code == 403
    assert "valheim-kirken" in manager.configs


def test_v1_delete_requires_auth_and_csrf(monkeypatch, tmp_path):
    client, manager, _ = _delete_client(monkeypatch, tmp_path)
    assert client.request("DELETE", "/api/v1/servers/valheim-kirken", json={"confirm": "valheim-kirken"}).status_code == 401
    _login(client)
    response = client.request("DELETE", "/api/v1/servers/valheim-kirken", json={"confirm": "valheim-kirken"})
    assert response.status_code == 403
    assert "valheim-kirken" in manager.configs


def test_v1_delete_unknown_server(monkeypatch, tmp_path):
    client, _, _ = _delete_client(monkeypatch, tmp_path)
    response = client.request("DELETE", "/api/v1/servers/nope", json={"confirm": "nope"}, headers={"X-API-Key": "abc123"})
    assert response.status_code == 404


def test_v1_games_enable_create_for_ready_non_static_game(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    install = tmp_path / "pz"
    install.mkdir()
    (install / "StartServer64.bat").write_text("@echo off", encoding="utf-8")

    client, manager = _client()
    manager.configs["pz-one"] = ServerConfig(
        id="pz-one",
        name="PZ",
        script="C:/pz.bat",
        working_directory="C:/",
        game="project-zomboid",
        executable_directory=str(install),
    )

    response = client.get("/api/v1/games", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    pz = next(item for item in payload["games"] if item["id"] == "project-zomboid")
    assert pz["capabilities"]["create_server"] is True

    schema_response = client.get("/api/v1/games/project-zomboid/create-schema", headers={"X-API-Key": "abc123"})
    assert schema_response.status_code == 200
    schema_payload = schema_response.json()
    assert schema_payload["success"] is True
    assert schema_payload["schema"]["supported"] is True


def test_v1_games_disable_create_for_ready_game_without_search_names(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    install = tmp_path / "cs2"
    install.mkdir()

    class _Status:
        state = "READY"

        def __init__(self):
            self.installation_path = install

    def _detect_ready(*_args, **_kwargs):
        return _Status()

    monkeypatch.setattr("core.web_control.detect_game", _detect_ready)

    response = client.get("/api/v1/games", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    cs2 = next(item for item in payload["games"] if item["id"] == "counter-strike-2")
    assert cs2["capabilities"]["create_server"] is False

    schema_response = client.get("/api/v1/games/counter-strike-2/create-schema", headers={"X-API-Key": "abc123"})
    assert schema_response.status_code == 200
    schema_payload = schema_response.json()
    assert schema_payload["success"] is True
    assert schema_payload["schema"]["supported"] is False


def test_v1_games_disable_create_when_ready_but_installation_missing(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()

    class _Status:
        state = "READY"
        installation_path = None

    def _detect_ready_without_install(*_args, **_kwargs):
        return _Status()

    monkeypatch.setattr("core.web_control.detect_game", _detect_ready_without_install)

    response = client.get("/api/v1/games", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    mc = next(item for item in payload["games"] if item["id"] == "minecraft-java")
    assert mc["capabilities"]["create_server"] is False


def test_v1_me_endpoint(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    response = client.get("/api/v1/me", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["user"]["id"] == "owner-local"


def test_v1_users_forbidden_for_member(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    response = client.get("/api/v1/users", headers={"X-API-Key": "abc123", "X-SM-User": "member-local"})
    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"] == "forbidden"


def test_v1_create_server_forbidden_for_member(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()
    response = client.post(
        "/api/v1/servers",
        json={"game": "valheim", "name": "Member Create", "world_mode": "new"},
        headers={"X-API-Key": "abc123", "X-SM-User": "member-local"},
    )
    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"] == "forbidden"


def test_v1_jobs_and_tasks_alias(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()

    create_response = client.post(
        "/api/v1/servers",
        json={"game": "valheim", "name": "Alias Test", "world_mode": "new"},
        headers={"X-API-Key": "abc123"},
    )
    assert create_response.status_code == 200

    jobs_response = client.get("/api/v1/jobs", headers={"X-API-Key": "abc123"})
    assert jobs_response.status_code == 200
    jobs_payload = jobs_response.json()
    assert jobs_payload["success"] is True
    assert isinstance(jobs_payload.get("jobs"), list)

    tasks_response = client.get("/api/v1/tasks", headers={"X-API-Key": "abc123"})
    assert tasks_response.status_code == 200
    tasks_payload = tasks_response.json()
    assert tasks_payload["success"] is True
    assert isinstance(tasks_payload.get("tasks"), list)


def test_v1_setup_game_returns_job_and_fails_cleanly_for_manual_titles(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()

    response = client.post("/api/v1/games/minecraft-java/setup", json={}, headers={"X-API-Key": "abc123"})
    assert response.status_code == 409
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"] == "setup_not_supported"


def test_v1_server_details_include_runtime_fields(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()

    response = client.get("/api/v1/servers/valheim-kirken", headers={"X-API-Key": "abc123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    server = payload["server"]
    assert "pid" in server
    assert "uptime_seconds" in server
    assert "owner_id" in server


def test_v1_invitations_list_and_delete(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()

    create = client.post(
        "/api/v1/invitations",
        json={"target": "bob", "role": "MEMBER"},
        headers={"X-API-Key": "abc123"},
    )
    assert create.status_code == 200
    invitation_id = create.json()["invitation"]["id"]

    listing = client.get("/api/v1/invitations", headers={"X-API-Key": "abc123"})
    assert listing.status_code == 200
    listing_payload = listing.json()
    assert listing_payload["success"] is True
    assert any(item["id"] == invitation_id for item in listing_payload.get("invitations", []))

    delete = client.delete(f"/api/v1/invitations/{invitation_id}", headers={"X-API-Key": "abc123"})
    assert delete.status_code == 200
    delete_payload = delete.json()
    assert delete_payload["success"] is True
    assert delete_payload["invitation_id"] == invitation_id


def test_v1_invitations_forbidden_for_member(monkeypatch):
    monkeypatch.setenv("SERVER_MANAGER_API_KEY", "abc123")
    client, _ = _client()

    response = client.get("/api/v1/invitations", headers={"X-API-Key": "abc123", "X-SM-User": "member-local"})
    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"] == "forbidden"
