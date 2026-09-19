from __future__ import annotations

import html
import logging
import secrets
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .config import AppSettings
from .version import APP_VERSION

SESSION_COOKIE = "sm_web_session"
SESSION_TTL_SECONDS = 60 * 60 * 24
DEFAULT_WEB_UI_ROOT = Path(__file__).resolve().parents[1] / "site" / "webcontrol"


@dataclass
class _Session:
    session_id: str
    csrf_token: str
    expires_at: float


class WebControlService:
    def __init__(
        self,
        settings: AppSettings,
        manager,
        logger: logging.Logger,
        on_settings_changed: Callable[[], None] | None = None,
        web_ui_root: Path | None = None,
    ):
        self.settings = settings
        self.manager = manager
        self.logger = logger
        self.on_settings_changed = on_settings_changed
        self.web_ui_root = web_ui_root or DEFAULT_WEB_UI_ROOT
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._app: FastAPI | None = None
        self._scheme = "http"
        self.last_error = ""

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def local_ip(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    def local_url(self) -> str:
        host = self.local_ip() if self.settings.web_control_bind_address == "0.0.0.0" else self.settings.web_control_bind_address
        return f"{self._scheme}://{host}:{self.settings.web_control_port}"

    def diagnostics(self) -> dict[str, str]:
        cert = self.settings.web_control_https_cert.strip()
        key = self.settings.web_control_https_key.strip()
        cert_state = "not_configured"
        if cert and key:
            cert_path = Path(cert)
            key_path = Path(key)
            if cert_path.is_file() and key_path.is_file():
                cert_state = "ready"
            else:
                cert_state = "missing_files"
        elif cert or key:
            cert_state = "partial"

        bind = self.settings.web_control_bind_address
        exposure = "lan" if bind == "0.0.0.0" else "local_only"
        return {
            "enabled": "yes" if self.settings.web_control_enabled else "no",
            "running": "yes" if self.is_running else "no",
            "bind": bind,
            "port": str(self.settings.web_control_port),
            "https": cert_state,
            "exposure": exposure,
            "url": self.local_url() if self.is_running else "-",
            "last_error": self.last_error or "-",
        }

    def start(self) -> None:
        if not self.settings.web_control_enabled:
            self.stop()
            return
        if self.is_running:
            return

        self.last_error = ""
        cert = self.settings.web_control_https_cert.strip()
        key = self.settings.web_control_https_key.strip()

        config_kwargs: dict[str, Any] = {
            "host": self.settings.web_control_bind_address,
            "port": int(self.settings.web_control_port),
            "log_level": "warning",
            "access_log": False,
            "lifespan": "off",
            # PyInstaller onefile builds can miss uvicorn's default logging formatter wiring.
            # We rely on the app logger instead of uvicorn's dictConfig.
            "log_config": None,
        }

        if cert or key:
            if not cert or not key:
                raise RuntimeError("HTTPS kræver både certificate og private key")
            cert_path = Path(cert)
            key_path = Path(key)
            if not cert_path.is_file() or not key_path.is_file():
                raise RuntimeError("HTTPS certificate/key fil ikke fundet")
            config_kwargs["ssl_certfile"] = cert
            config_kwargs["ssl_keyfile"] = key
            self._scheme = "https"
        else:
            self._scheme = "http"

        self._app = create_web_app(self.settings, self.manager, self.logger, self.on_settings_changed, self.web_ui_root)
        config = uvicorn.Config(app=self._app, **config_kwargs)
        self._server = uvicorn.Server(config)

        def _run() -> None:
            try:
                self._server.run()
            except Exception as exc:
                self.last_error = str(exc)
                self.logger.exception("Web Control crashed")
            finally:
                self._running = False

        self._thread = threading.Thread(target=_run, daemon=True, name="web-control")
        self._thread.start()
        self._running = True

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._server = None
        self._thread = None
        self._running = False

    def restart(self, settings: AppSettings | None = None) -> None:
        if settings is not None:
            self.settings = settings
        self.stop()
        self.start()


class _WebControlBackend:
    def __init__(self, settings: AppSettings, manager, logger: logging.Logger):
        self.settings = settings
        self.manager = manager
        self.logger = logger
        self.password_hasher = PasswordHasher()
        self.sessions: dict[str, _Session] = {}
        self.failed_logins: dict[str, dict[str, float]] = {}
        self.lock = threading.RLock()

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
        return request.client.host if request.client else "unknown"

    def _purge_expired_sessions(self) -> None:
        now = time.time()
        expired = [sid for sid, session in self.sessions.items() if session.expires_at <= now]
        for sid in expired:
            self.sessions.pop(sid, None)

    def _session_from_request(self, request: Request) -> _Session | None:
        with self.lock:
            self._purge_expired_sessions()
            session_id = request.cookies.get(SESSION_COOKIE)
            if not session_id:
                return None
            session = self.sessions.get(session_id)
            if not session:
                return None
            if session.expires_at <= time.time():
                self.sessions.pop(session_id, None)
                return None
            return session

    def _require_csrf(self, request: Request, session: _Session) -> None:
        token = request.headers.get("X-CSRF-Token", "")
        if not token or token != session.csrf_token:
            raise HTTPException(status_code=403, detail="csrf_invalid")

    def _set_session_cookie(self, response: Response, session_id: str, secure: bool) -> None:
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session_id,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=secure,
        )

    def _clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(SESSION_COOKIE)

    def _sanitize_line(self, line: str) -> str:
        lowered = line.lower()
        if "password" in lowered or "token" in lowered or "secret" in lowered:
            return "[redacted sensitive log line]"
        if "-password" in lowered:
            parts = line.split()
            masked: list[str] = []
            skip_next = False
            for item in parts:
                if skip_next:
                    masked.append("******")
                    skip_next = False
                    continue
                masked.append(item)
                if item.lower() == "-password":
                    skip_next = True
            return " ".join(masked)
        return line

    def _server_state(self, process) -> str:
        raw = getattr(process.status, "value", str(process.status))
        mapping = {
            "ONLINE": "RUNNING",
            "OFFLINE": "STOPPED",
            "CRASH LOOP": "CRASHED",
        }
        return mapping.get(raw, raw if raw in {"STARTING", "RUNNING", "STOPPING", "STOPPED", "CRASHED", "UNKNOWN"} else "UNKNOWN")

    def _server_payload(self, server_id: str) -> dict[str, Any]:
        config = self.manager.configs[server_id]
        process = self.manager.processes[server_id]
        return {
            "id": config.id,
            "name": config.name,
            "game": config.game,
            "state": self._server_state(process),
            "pid": process.pid,
            "uptime_seconds": process.uptime_seconds,
            "players": None,
        }

    def _get_server_or_404(self, server_id: str):
        if server_id not in self.manager.configs or server_id not in self.manager.processes:
            raise HTTPException(status_code=404, detail="server_not_found")
        return self.manager.configs[server_id], self.manager.processes[server_id]


def create_web_app(
    settings: AppSettings,
    manager,
    logger: logging.Logger,
    on_settings_changed: Callable[[], None] | None = None,
    web_ui_root: Path | None = None,
) -> FastAPI:
    backend = _WebControlBackend(settings, manager, logger)
    app = FastAPI(title="Server Manager Web Control", docs_url=None, redoc_url=None, openapi_url=None)
    ui_root = web_ui_root or DEFAULT_WEB_UI_ROOT

    if ui_root.is_dir():
        app.mount("/web-static", StaticFiles(directory=str(ui_root)), name="web-static")

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    async def _require_auth(request: Request) -> _Session:
        session = backend._session_from_request(request)
        if not session:
            raise HTTPException(status_code=401, detail="unauthorized")
        return session

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = str(exc.detail)
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=exc.status_code, content={"error": detail})
        if exc.status_code == 401:
            return HTMLResponse("Unauthorized", status_code=401)
        return HTMLResponse(html.escape(detail), status_code=exc.status_code)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/")
    async def web_ui():
        index_file = ui_root / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse("Web UI missing", status_code=500)

    @app.get("/login")
    async def login_ui():
        index_file = ui_root / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse("Web UI missing", status_code=500)

    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        configured = bool(settings.web_control_password_hash.strip())
        session = backend._session_from_request(request)
        return {
            "configured": configured,
            "authenticated": bool(session),
            "csrf_token": session.csrf_token if session else "",
        }

    @app.post("/api/auth/setup")
    async def auth_setup(request: Request):
        if settings.web_control_password_hash.strip():
            raise HTTPException(status_code=409, detail="already_configured")
        body = await request.json()
        password = str((body or {}).get("password", ""))
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="password_too_short")
        settings.web_control_password_hash = backend.password_hasher.hash(password)
        if on_settings_changed:
            try:
                on_settings_changed()
            except Exception:
                logger.exception("Failed to persist web-control password hash")
        logger.info("Web control password configured")
        return {"ok": True}

    @app.post("/api/auth/login")
    async def auth_login(request: Request):
        if not settings.web_control_password_hash.strip():
            raise HTTPException(status_code=400, detail="not_configured")

        ip = backend._client_ip(request)
        now = time.time()
        entry = backend.failed_logins.get(ip, {"fails": 0.0, "next_allowed": 0.0})
        if now < float(entry.get("next_allowed", 0.0)):
            raise HTTPException(status_code=429, detail="too_many_attempts")

        body = await request.json()
        password = str((body or {}).get("password", ""))
        try:
            backend.password_hasher.verify(settings.web_control_password_hash, password)
        except VerifyMismatchError:
            fails = int(entry.get("fails", 0)) + 1
            backoff = min(300, 2 ** min(8, fails))
            backend.failed_logins[ip] = {"fails": float(fails), "next_allowed": now + backoff}
            logger.warning("Failed web login attempt")
            raise HTTPException(status_code=401, detail="invalid_credentials")

        backend.failed_logins[ip] = {"fails": 0.0, "next_allowed": 0.0}
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        with backend.lock:
            backend.sessions[session_id] = _Session(
                session_id=session_id,
                csrf_token=csrf_token,
                expires_at=now + SESSION_TTL_SECONDS,
            )
        logger.info("Web user authenticated")
        response = JSONResponse({"ok": True, "csrf_token": csrf_token})
        backend._set_session_cookie(response, session_id, secure=request.url.scheme == "https")
        return response

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request, session: _Session = Depends(_require_auth)):
        backend._require_csrf(request, session)
        with backend.lock:
            backend.sessions.pop(session.session_id, None)
        response = JSONResponse({"ok": True})
        backend._clear_session_cookie(response)
        return response

    @app.get("/api/status")
    async def api_status(_session: _Session = Depends(_require_auth)):
        return {"server_manager": {"version": APP_VERSION, "status": "running"}}

    @app.get("/api/servers")
    async def api_servers(_session: _Session = Depends(_require_auth)):
        servers = [backend._server_payload(server_id) for server_id in manager.configs.keys()]
        return {"servers": servers}

    @app.get("/api/servers/{server_id}")
    async def api_server(server_id: str, _session: _Session = Depends(_require_auth)):
        backend._get_server_or_404(server_id)
        return backend._server_payload(server_id)

    @app.post("/api/servers/{server_id}/start")
    async def api_start(server_id: str, request: Request, session: _Session = Depends(_require_auth)):
        backend._require_csrf(request, session)
        backend._get_server_or_404(server_id)
        process = manager.processes[server_id]
        state = backend._server_state(process)
        if state in {"RUNNING", "STARTING"}:
            return {"ok": True, "state": state}
        try:
            manager.start(server_id)
        except Exception:
            raise HTTPException(status_code=400, detail="start_failed")
        logger.info("Web action: server=%s action=start", server_id)
        return {"ok": True, "state": backend._server_state(process)}

    @app.post("/api/servers/{server_id}/stop")
    async def api_stop(server_id: str, request: Request, session: _Session = Depends(_require_auth)):
        backend._require_csrf(request, session)
        backend._get_server_or_404(server_id)
        process = manager.processes[server_id]
        state = backend._server_state(process)
        if state == "STOPPED":
            return {"ok": True, "state": state}
        manager.stop(server_id)
        logger.info("Web action: server=%s action=stop", server_id)
        return {"ok": True, "state": backend._server_state(process)}

    @app.post("/api/servers/{server_id}/restart")
    async def api_restart(server_id: str, request: Request, session: _Session = Depends(_require_auth)):
        backend._require_csrf(request, session)
        backend._get_server_or_404(server_id)
        try:
            manager.restart(server_id)
        except Exception:
            raise HTTPException(status_code=400, detail="restart_failed")
        logger.info("Web action: server=%s action=restart", server_id)
        return {"ok": True, "state": backend._server_state(manager.processes[server_id])}

    @app.get("/api/servers/{server_id}/logs")
    async def api_logs(server_id: str, cursor: int = 0, limit: int = 200, _session: _Session = Depends(_require_auth)):
        _, process = backend._get_server_or_404(server_id)
        all_lines = [backend._sanitize_line(line) for line in process.recent_output]
        total = len(all_lines)
        if cursor <= 0:
            bounded_limit = max(1, min(limit, 500))
            lines = all_lines[-bounded_limit:]
            return {"server_id": server_id, "lines": lines, "cursor": total}

        safe_cursor = max(0, min(cursor, total))
        lines = all_lines[safe_cursor:]
        if len(lines) > 500:
            lines = lines[-500:]
        return {"server_id": server_id, "lines": lines, "cursor": total}

    return app
