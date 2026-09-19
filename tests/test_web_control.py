from __future__ import annotations

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
        cfg = ServerConfig(id="valheim-kirken", name="Kirken", script="a.bat", working_directory="C:/", game="valheim")
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
