from __future__ import annotations

import html
import logging
import os
import re
import secrets
import socket
import threading
import time
import uuid
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

from .config import AppSettings, ServerConfig
from .access_control import (
    PERMISSION_CONTROL_SERVERS,
    PERMISSION_CREATE_SERVERS,
    PERMISSION_DELETE_SERVERS,
    PERMISSION_MANAGE_BACKUPS,
    PERMISSION_MANAGE_MODS,
    PERMISSION_MANAGE_USERS,
    PERMISSION_VIEW_SERVERS,
    PERMISSION_VIEW_USERS,
    PermissionManager,
    ROLE_OWNER,
    UserAccount,
    UserManager,
    UserStoreError,
)
from .game_detection import detect_game
from .games import game_choices, game_definition, game_public_payload
from .managed_server import generate_managed_server
from .setup_engine import GameSetupEngine
from .version import APP_VERSION
from .worlds import discover_world_names

SESSION_COOKIE = "sm_web_session"
SESSION_TTL_SECONDS = 60 * 60 * 24
DEFAULT_WEB_UI_ROOT = Path(__file__).resolve().parents[1] / "site" / "webcontrol"


@dataclass
class _Session:
    session_id: str
    csrf_token: str
    expires_at: float
    user_id: str = "owner-local"


@dataclass
class _AccessContext:
    session: _Session | None
    via_api_key: bool
    user: UserAccount


@dataclass
class _Job:
    job_id: str
    status: str
    operation: str
    created_at: float
    updated_at: float
    progress: list[str]
    result: dict[str, Any] | None = None
    error: str = ""
    metadata: dict[str, Any] | None = None
    owner_id: str = ""


class WebControlService:
    def __init__(
        self,
        settings: AppSettings,
        manager,
        logger: logging.Logger,
        on_settings_changed: Callable[[], None] | None = None,
        web_ui_root: Path | None = None,
        app_root: Path | None = None,
    ):
        self.settings = settings
        self.manager = manager
        self.logger = logger
        self.on_settings_changed = on_settings_changed
        self.web_ui_root = web_ui_root or DEFAULT_WEB_UI_ROOT
        self.app_root = app_root
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

        self._app = create_web_app(self.settings, self.manager, self.logger, self.on_settings_changed, self.web_ui_root, self.app_root)
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
    def __init__(self, settings: AppSettings, manager, logger: logging.Logger, app_root: Path | None = None):
        self.settings = settings
        self.manager = manager
        self.logger = logger
        self.app_root = app_root
        self.password_hasher = PasswordHasher()
        self.sessions: dict[str, _Session] = {}
        self.failed_logins: dict[str, dict[str, float]] = {}
        self.operation_locks: dict[str, threading.Lock] = {}
        self.jobs: dict[str, _Job] = {}
        self.restore_history: dict[str, list[dict[str, Any]]] = {}
        self.access_audit: list[dict[str, Any]] = []
        self.player_bans: dict[str, dict[str, dict[str, Any]]] = {}
        self.player_notes: dict[str, dict[str, dict[str, Any]]] = {}
        self.player_moderation_events: dict[str, list[dict[str, Any]]] = {}
        self.mod_manager = None
        if app_root:
            try:
                from .mod_manager import ModManager

                self.mod_manager = ModManager(app_root, logger)
            except Exception:
                self.logger.exception("Failed to initialize mod manager for web API")
        self.lock = threading.RLock()
        user_store = app_root / "config" / "users.json" if app_root else None
        self.user_manager = UserManager(storage_path=user_store)
        self.permission_manager = PermissionManager()
        self.invitations: list[dict[str, Any]] = []

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

    def _server_payload_v1(self, server_id: str) -> dict[str, Any]:
        config = self.manager.configs[server_id]
        process = self.manager.processes[server_id]
        return {
            "id": config.id,
            "name": config.name,
            "game": config.game,
            "status": self._server_state(process).lower(),
            "pid": process.pid,
            "uptime_seconds": process.uptime_seconds,
            "owner_id": config.owner_id,
        }

    def _api_key(self) -> str:
        return str(os.environ.get("SERVER_MANAGER_API_KEY", "")).strip()

    def _is_api_key_authorized(self, request: Request) -> bool:
        configured = self._api_key()
        if not configured:
            return False
        provided = request.headers.get("X-API-Key", "").strip()
        return bool(provided) and secrets.compare_digest(configured, provided)

    def _action_lock(self, server_id: str) -> threading.Lock:
        with self.lock:
            lock = self.operation_locks.get(server_id)
            if lock is None:
                lock = threading.Lock()
                self.operation_locks[server_id] = lock
            return lock

    def _get_server_or_404(self, server_id: str):
        if server_id not in self.manager.configs or server_id not in self.manager.processes:
            raise HTTPException(status_code=404, detail="server_not_found")
        return self.manager.configs[server_id], self.manager.processes[server_id]

    def _games_payload(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for definition in game_choices():
            status = self._public_game_state(definition.id)
            capabilities = definition.web_capabilities().copy()
            capabilities["create_server"] = self._can_web_create(definition.id)
            item = game_public_payload(definition)
            item["name"] = definition.display_name
            item["capabilities"] = capabilities
            item["status"] = status
            item["setup"] = {
                "supported": bool(definition.supported),
                "automatic": bool(definition.installation_capability == "FULL"),
                "requirements": [
                    "steam" if definition.steam_app_id else "",
                    "java" if definition.requires_java else "",
                ],
                "installer": "server_manager",
            }
            item["setup"]["requirements"] = [req for req in item["setup"]["requirements"] if req]
            payloads.append(item)
        return payloads

    def _public_game_state(self, game_id: str) -> str:
        definition = game_definition(game_id)
        if not definition.supported:
            return "UNSUPPORTED"
        if self._has_running_setup_job(game_id):
            return "INSTALLING"
        status = self._game_status(game_id)
        if status.state == "READY":
            return "READY"
        if status.state == "ERROR":
            return "ERROR"
        return "SETUP_REQUIRED"

    def _has_running_setup_job(self, game_id: str) -> bool:
        with self.lock:
            for job in self.jobs.values():
                metadata = job.metadata or {}
                if metadata.get("game_id") != game_id:
                    continue
                if job.operation != "setup_game":
                    continue
                if job.status in {"QUEUED", "RUNNING"}:
                    return True
        return False

    def _game_status(self, game_id: str):
        definition = game_definition(game_id)
        configured_paths: list[Path] = []
        for config in self.manager.configs.values():
            if config.game != game_id:
                continue
            location = str(config.executable_directory or "").strip()
            if location:
                configured_paths.append(Path(location))
        return detect_game(definition, configured_paths)

    def _can_web_create(self, game_id: str) -> bool:
        definition = game_definition(game_id)
        if not definition.supported:
            return False
        if not definition.server_search_names:
            return False

        # If we already know a valid installation path, allow create even when
        # environment detection is noisy on clean CI runners.
        if self._resolve_installation_path(game_id) is not None:
            return True

        status = self._game_status(game_id)
        if status.state != "READY":
            return False
        return self._resolve_installation_path(game_id) is not None

    def _create_schema_payload(self, game_id: str) -> dict[str, Any]:
        definition = game_definition(game_id)
        if definition.id != game_id:
            raise HTTPException(status_code=404, detail="game_not_found")

        if not self._can_web_create(game_id):
            return {
                "supported": False,
                "message": "Kræver opsætning i Server Manager appen. Brug Spil > Opsæt automatisk eller følg appens instruktioner.",
                "fields": [],
            }

        schema = definition.web_create_schema()
        if schema.get("supported"):
            return schema

        if not self._can_web_create(game_id):
            return schema

        fields: list[dict[str, Any]] = [
            {
                "id": "name",
                "type": "text",
                "label": "Server name",
                "required": True,
            },
            {
                "id": "password",
                "type": "password",
                "label": "Password",
                "required": False,
            },
        ]
        if definition.has_world:
            fields.extend(
                [
                    {
                        "id": "world_mode",
                        "type": "select",
                        "label": "World",
                        "required": True,
                        "options": [
                            {"id": "new", "label": "Create new world"},
                            {"id": "existing", "label": "Use existing world"},
                        ],
                        "default": "new",
                    },
                    {
                        "id": "world",
                        "type": "world-selector",
                        "label": "World",
                        "required": False,
                    },
                ]
            )

        return {
            "supported": True,
            "fields": fields,
            "editable_fields": definition.web_editable_fields(),
        }

    def _resolve_installation_path(self, game_id: str) -> Path | None:
        for config in self.manager.configs.values():
            if config.game != game_id:
                continue
            location = str(config.executable_directory or "").strip()
            if not location:
                continue
            candidate = Path(location)
            if candidate.is_dir():
                return candidate
        status = detect_game(game_definition(game_id))
        if status.installation_path and status.installation_path.is_dir():
            return status.installation_path
        return None

    def _create_server_from_payload(self, payload: dict[str, Any], owner_id: str = "owner-local") -> ServerConfig:
        game_id = str(payload.get("game", "")).strip()
        if not game_id:
            raise HTTPException(status_code=400, detail="game_required")

        definition = game_definition(game_id)
        if not self._can_web_create(game_id):
            raise HTTPException(status_code=400, detail="create_not_supported")

        name = str(payload.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name_required")

        setup = GameSetupEngine(definition).default_server_payload()
        installation = self._resolve_installation_path(game_id)
        if installation is None:
            raise HTTPException(status_code=409, detail="setup_required")

        world_mode = str(payload.get("world_mode", "new") or "new").strip().lower()
        world_value = str(payload.get("world", "")).strip()
        if definition.has_world:
            if world_mode == "existing" and not world_value:
                raise HTTPException(status_code=400, detail="world_required")
            if world_mode != "existing" and not world_value:
                world_value = name

        server_id = f"{game_id}-{uuid.uuid4().hex[:8]}"
        extra_args = str(setup.get("additional_arguments", ""))
        if game_id == "minecraft-java":
            mc_type = str(payload.get("minecraft_server_type") or "vanilla")
            mc_version = str(payload.get("minecraft_version") or "latest")
            extra_args = (extra_args + f" --server-type {mc_type} --mc-version {mc_version}").strip()

        config = ServerConfig(
            id=server_id,
            name=name,
            script="",
            working_directory="",
            game=game_id,
            world=world_value,
            password=str(payload.get("password") or ""),
            port=int(setup.get("port", 2456)),
            public=bool(setup.get("public", True)),
            crossplay=bool(setup.get("crossplay", True)),
            additional_arguments=extra_args,
            world_directory=str(setup.get("world_directory", "")),
            executable_directory=str(installation),
            auto_start=False,
            start_on_manager_recovery=True,
            auto_restart=True,
            restart_delay=int(setup.get("restart_delay", 10)),
            owner_id=owner_id,
        )

        root = self.app_root or Path(__file__).resolve().parents[1]
        try:
            config = generate_managed_server(config, root, str(installation))
        except FileNotFoundError:
            raise HTTPException(status_code=409, detail="setup_required")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"create_failed:{exc}")

        self.manager.configs[config.id] = config
        self.manager.processes[config.id] = self.manager._create_process(config)
        return config

    def _create_job(self, operation: str, metadata: dict[str, Any] | None = None, owner_id: str = "") -> _Job:
        now = time.time()
        job = _Job(
            job_id=secrets.token_urlsafe(12),
            status="QUEUED",
            operation=operation,
            created_at=now,
            updated_at=now,
            progress=[],
            metadata=metadata or {},
            owner_id=owner_id,
        )
        with self.lock:
            self.jobs[job.job_id] = job
        return job

    def _append_job_progress(self, job: _Job, message: str) -> None:
        with self.lock:
            job.progress.append(message)
            job.updated_at = time.time()

    def _finish_job(self, job: _Job, status: str, result: dict[str, Any] | None = None, error: str = "") -> None:
        with self.lock:
            job.status = status
            job.result = result
            job.error = error
            job.updated_at = time.time()

    def _job_payload(self, job: _Job) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "status": job.status,
            "operation": job.operation,
            "progress": list(job.progress),
            "result": job.result,
            "error": job.error,
            "metadata": job.metadata or {},
            "updated_at": job.updated_at,
        }

    def _jobs_payload(self, user: UserAccount | None = None) -> list[dict[str, Any]]:
        with self.lock:
            jobs = list(self.jobs.values())
        if user is not None and user.role != ROLE_OWNER:
            jobs = [job for job in jobs if job.owner_id == user.user_id]
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return [self._job_payload(job) for job in jobs]

    def _start_game_setup(self, game_id: str, owner_id: str = "") -> _Job:
        definition = game_definition(game_id)
        if definition.id != game_id:
            raise HTTPException(status_code=404, detail="game_not_found")
        if not definition.supported:
            raise HTTPException(status_code=400, detail="game_unsupported")
        if definition.installation_capability != "FULL":
            raise HTTPException(status_code=409, detail="setup_not_supported")
        if self._public_game_state(game_id) == "READY":
            raise HTTPException(status_code=409, detail="already_ready")
        if self._has_running_setup_job(game_id):
            raise HTTPException(status_code=409, detail="setup_in_progress")

        job = self._create_job("setup_game", metadata={"game_id": game_id}, owner_id=owner_id)

        def _worker() -> None:
            self._finish_job(job, "RUNNING")
            self._append_job_progress(job, "Checking system")
            self._append_job_progress(job, "Checking dependencies")
            self._append_job_progress(job, "Installing server files")
            self._append_job_progress(job, "Preparing files")
            self._append_job_progress(job, "Validating installation")
            state = self._public_game_state(game_id)
            if state == "READY":
                self._finish_job(job, "COMPLETED", result={"game_id": game_id, "status": "ready"})
                return
            self._finish_job(job, "FAILED", error="setup_requires_desktop_app")

        threading.Thread(target=_worker, daemon=True, name=f"job-setup-{job.job_id}").start()
        return job

    def _server_players_payload(self, server_id: str) -> dict[str, Any]:
        config, process = self._get_server_or_404(server_id)
        definition = game_definition(config.game)
        if not definition.web_players_supported:
            return {
                "supported": False,
                "players": [],
                "online": None,
                "max": None,
                "sessions": [],
                "recent_events": [],
                "event_count": 0,
                "source": "unsupported",
            }

        players: set[str] = set()
        sessions: dict[str, dict[str, Any]] = {}
        events: list[dict[str, Any]] = []
        if config.game == "valheim":
            joined_patterns = [
                re.compile(r"Got character .* from\s+([\w\-\. ]{2,32})\s*:\s*", re.IGNORECASE),
                re.compile(r"Player\s+([\w\-\. ]{2,32})\s+connected", re.IGNORECASE),
            ]
            left_patterns = [
                re.compile(r"Closing socket\s+([\w\-\. ]{2,32})", re.IGNORECASE),
                re.compile(r"Player\s+([\w\-\. ]{2,32})\s+disconnected", re.IGNORECASE),
            ]
        else:
            joined_patterns = [
                re.compile(r"player\s+([\w\-\. ]{2,32})\s+connected", re.IGNORECASE),
                re.compile(r"\[join\]\s+([\w\-\. ]{2,32})", re.IGNORECASE),
            ]
            left_patterns = [
                re.compile(r"player\s+([\w\-\. ]{2,32})\s+disconnected", re.IGNORECASE),
                re.compile(r"\[leave\]\s+([\w\-\. ]{2,32})", re.IGNORECASE),
            ]

        output = process.recent_output[-800:]
        for line_index, line in enumerate(output):
            text = str(line)
            matched_join = False
            for pattern in joined_patterns:
                match = pattern.search(text)
                if match:
                    player_name = match.group(1).strip()
                    if player_name:
                        players.add(player_name)
                        session = sessions.get(player_name, {
                            "player": player_name,
                            "joined_line": line_index,
                            "last_seen_line": line_index,
                        })
                        session["last_seen_line"] = line_index
                        session.setdefault("joined_line", line_index)
                        sessions[player_name] = session
                        events.append({"event": "joined", "player": player_name, "line": line_index})
                    matched_join = True
                    break
            if matched_join:
                continue

            for pattern in left_patterns:
                match = pattern.search(text)
                if match:
                    player_name = match.group(1).strip()
                    if player_name:
                        players.discard(player_name)
                        session = sessions.get(player_name, {
                            "player": player_name,
                            "joined_line": None,
                            "last_seen_line": line_index,
                        })
                        session["left_line"] = line_index
                        session["last_seen_line"] = line_index
                        sessions[player_name] = session
                        events.append({"event": "left", "player": player_name, "line": line_index})
                    break

        online = len(players)
        max_players = None
        count_patterns = [
            re.compile(r"(\d{1,3})\s*/\s*(\d{1,3})\s*players", re.IGNORECASE),
            re.compile(r"players\s*[:=]\s*(\d{1,3})\s*/\s*(\d{1,3})", re.IGNORECASE),
            re.compile(r"online\s*[:=]\s*(\d{1,3})\s*/\s*(\d{1,3})", re.IGNORECASE),
        ]
        for line in reversed(output):
            text = str(line)
            for pattern in count_patterns:
                match = pattern.search(text)
                if match:
                    try:
                        online = int(match.group(1))
                        max_players = int(match.group(2))
                    except ValueError:
                        pass
                    break
            if max_players is not None:
                break

        result = sorted(name for name in players if name)
        online_sessions = []
        for player_name in result:
            session = sessions.get(player_name, {"player": player_name, "joined_line": None, "last_seen_line": None})
            joined_line = session.get("joined_line")
            last_seen_line = session.get("last_seen_line")
            line_span = None
            if isinstance(joined_line, int) and isinstance(last_seen_line, int):
                line_span = max(0, last_seen_line - joined_line)
            online_sessions.append(
                {
                    "player": player_name,
                    "joined_line": joined_line,
                    "last_seen_line": last_seen_line,
                    "line_span": line_span,
                }
            )

        return {
            "supported": True,
            "players": result,
            "online": online,
            "max": max_players,
            "sessions": online_sessions,
            "recent_events": events[-20:],
            "event_count": len(events),
            "source": "recent_output",
        }

    def _normalize_player_name(self, value: str) -> str:
        player = str(value or "").strip()
        if not player:
            raise HTTPException(status_code=400, detail="player_required")
        if len(player) > 64:
            raise HTTPException(status_code=400, detail="player_name_too_long")
        return player

    def _record_player_moderation_event(self, server_id: str, action: str, actor: str, payload: dict[str, Any]) -> None:
        rows = self.player_moderation_events.setdefault(server_id, [])
        rows.insert(
            0,
            {
                "id": secrets.token_urlsafe(8),
                "action": action,
                "actor": actor,
                "at": time.time(),
                "payload": payload,
            },
        )
        del rows[100:]

    def _server_moderation_payload(self, server_id: str) -> dict[str, Any]:
        bans = self.player_bans.get(server_id, {})
        notes = self.player_notes.get(server_id, {})
        events = self.player_moderation_events.get(server_id, [])
        return {
            "server_id": server_id,
            "bans": [
                {
                    "player": player,
                    "reason": str(data.get("reason") or ""),
                    "by": str(data.get("by") or ""),
                    "at": float(data.get("at") or 0),
                }
                for player, data in sorted(bans.items(), key=lambda item: item[0].lower())
            ],
            "notes": [
                {
                    "player": player,
                    "note": str(data.get("note") or ""),
                    "by": str(data.get("by") or ""),
                    "updated_at": float(data.get("updated_at") or 0),
                }
                for player, data in sorted(notes.items(), key=lambda item: item[0].lower())
            ],
            "events": list(events[:30]),
        }

    def _moderation_command(self, server_id: str, action: str, player: str, reason: str = "") -> str:
        config, _ = self._get_server_or_404(server_id)
        game = str(config.game or "").strip().lower()
        safe_reason = reason.replace("\n", " ").replace("\r", " ").strip()
        if action == "kick":
            if game == "minecraft-java":
                return f"kick {player} {safe_reason}".strip()
            return f"kick {player}"
        if action == "ban":
            if game == "minecraft-java":
                return f"ban {player} {safe_reason}".strip()
            return f"ban {player}"
        if action == "unban":
            if game == "minecraft-java":
                return f"pardon {player}"
            return f"unban {player}"
        raise ValueError("invalid_moderation_action")

    def _dispatch_moderation_command(self, server_id: str, action: str, player: str, reason: str = "") -> dict[str, Any]:
        command = self._moderation_command(server_id, action, player, reason)
        process = self.manager.processes.get(server_id)
        if process is None:
            return {"sent": False, "command": command, "reason": "server_not_found"}
        state = self._server_state(process)
        if state not in {"RUNNING", "STARTING"}:
            return {"sent": False, "command": command, "reason": "server_not_running"}
        try:
            sent = self.manager.send_command(server_id, command)
        except Exception:
            sent = False
        return {
            "sent": bool(sent),
            "command": command,
            "reason": "ok" if sent else "stdin_unavailable",
        }

    def _server_backup_root(self, config: ServerConfig) -> Path | None:
        source = str(config.world_directory or config.backup.source or "").strip()
        if not source:
            return None
        path = Path(source)
        if not path.is_dir():
            return None
        return path

    def _list_backups(self, server_id: str) -> list[dict[str, Any]]:
        config, _ = self._get_server_or_404(server_id)
        root = self._server_backup_root(config)
        if root is None:
            return []
        active_mods = 0
        if self.mod_manager is not None:
            active_mods = sum(1 for item in self.mod_manager.mods_for_server(server_id) if bool(item.get("enabled", False)))
        items: list[dict[str, Any]] = []
        for entry in sorted(root.glob(f"{config.id}-world-*.tar.zst"), key=lambda p: p.stat().st_mtime, reverse=True):
            items.append(
                {
                    "id": entry.name,
                    "name": entry.stem,
                    "created_at": entry.stat().st_mtime,
                    "kind": "automatic",
                    "world": config.world or config.name,
                    "mods_active": active_mods,
                    "server_version": "current",
                }
            )
        return items

    def _list_restore_history(self, server_id: str) -> list[dict[str, Any]]:
        return list(self.restore_history.get(server_id, []))

    def _record_restore_event(self, server_id: str, event: dict[str, Any]) -> None:
        history = self.restore_history.setdefault(server_id, [])
        history.insert(0, event)
        del history[10:]

    def _create_backup(self, server_id: str) -> dict[str, Any]:
        from .valheim_backup import create_valheim_backup

        config, _ = self._get_server_or_404(server_id)
        definition = game_definition(config.game)
        if not definition.has_world:
            raise HTTPException(status_code=400, detail="backup_not_supported")
        root = self._server_backup_root(config)
        if root is None:
            raise HTTPException(status_code=400, detail="world_directory_missing")
        target = create_valheim_backup(str(root), config.id, max(1, int(config.backup.retention_days or 30)))
        return {
            "id": target.name,
            "name": target.stem,
            "created_at": target.stat().st_mtime,
            "kind": "manual",
            "world": config.world or config.name,
            "mods_active": 0 if self.mod_manager is None else sum(1 for item in self.mod_manager.mods_for_server(server_id) if bool(item.get("enabled", False))),
            "server_version": "current",
        }

    def _restore_archive_to_world(self, archive_path: Path, world_root: Path) -> None:
        import tarfile
        import tempfile

        import zstandard

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            with archive_path.open("rb") as source:
                dctx = zstandard.ZstdDecompressor()
                with dctx.stream_reader(source) as reader:
                    with tarfile.open(fileobj=reader, mode="r|") as archive:
                        archive.extractall(temp_root)

            extracted_dirs = [entry for entry in temp_root.iterdir() if entry.is_dir()]
            source_root = extracted_dirs[0] if extracted_dirs else temp_root
            world_root.mkdir(parents=True, exist_ok=True)
            for entry in world_root.iterdir():
                if entry.is_dir():
                    import shutil

                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            for entry in source_root.iterdir():
                target = world_root / entry.name
                if entry.is_dir():
                    import shutil

                    shutil.copytree(entry, target)
                else:
                    import shutil

                    shutil.copy2(entry, target)

    def _restore_backup(self, server_id: str, backup_id: str) -> None:
        from .server_process import ServerStatus
        from .valheim_backup import create_valheim_backup

        config, process = self._get_server_or_404(server_id)
        world_root = self._server_backup_root(config)
        if world_root is None:
            raise HTTPException(status_code=400, detail="world_directory_missing")
        if not re.fullmatch(rf"{re.escape(config.id)}-world-[A-Za-z0-9._-]+\.tar\.zst", backup_id or ""):
            raise HTTPException(status_code=400, detail="invalid_backup_id")
        backup_path = (world_root / backup_id).resolve()
        if backup_path.parent != world_root.resolve():
            raise HTTPException(status_code=400, detail="invalid_backup_id")
        if not backup_path.is_file():
            raise HTTPException(status_code=404, detail="backup_not_found")

        was_running = process.status in (ServerStatus.STARTING, ServerStatus.ONLINE, ServerStatus.STOPPING)
        if was_running:
            self.manager.stop(server_id)

        safety_archive = create_valheim_backup(str(world_root), config.id, max(1, int(config.backup.retention_days or 30)))
        try:
            self._restore_archive_to_world(backup_path, world_root)
        except Exception:
            try:
                self._restore_archive_to_world(safety_archive, world_root)
            except Exception:
                self.logger.exception("Rollback failed for restore on %s", server_id)
            raise

        if was_running:
            self.manager.start(server_id)

        event = {
            "backup_id": backup_id,
            "restored_at": time.time(),
            "safety_backup_id": safety_archive.name,
            "status": "completed",
            "error": "",
        }
        self._record_restore_event(server_id, event)

    def _delete_server(self, server_id: str, persist: Callable[[], None] | None) -> dict[str, Any]:
        """Stop, take a mandatory safety backup, then unregister the server. Files on disk are kept."""
        config, process = self._get_server_or_404(server_id)
        if self._server_state(process) in {"STARTING", "RUNNING", "STOPPING"}:
            self.manager.stop(server_id)
            if self._server_state(process) != "STOPPED":
                raise HTTPException(status_code=409, detail="stop_failed")

        # Any backup failure aborts here, before the server is touched.
        backup = self._create_backup(server_id)

        timers = getattr(self.manager, "_restart_timers", None)
        timer = timers.pop(server_id, None) if isinstance(timers, dict) else None
        if timer is not None:
            timer.cancel()
        self.manager.processes.pop(server_id, None)
        self.manager.configs.pop(server_id, None)
        if persist:
            try:
                persist()
            except Exception:
                self.manager.configs[server_id] = config
                self.manager.processes[server_id] = process
                self.logger.exception("Failed to persist server deletion for %s", server_id)
                raise HTTPException(status_code=500, detail="delete_failed")
        with self.lock:
            self.restore_history.pop(server_id, None)
        return backup

    def _list_server_mods(self, server_id: str) -> list[dict[str, Any]]:
        if self.mod_manager is None:
            return []
        config, _ = self._get_server_or_404(server_id)
        assigned = {item.get("mod_id"): item for item in self.mod_manager.mods_for_server(server_id)}
        rows: list[dict[str, Any]] = []
        for mod in self.mod_manager.list_mods(config.game):
            attached = assigned.get(mod.get("id"), {})
            rows.append(
                {
                    "id": mod.get("id"),
                    "name": mod.get("name"),
                    "game": mod.get("game"),
                    "version": attached.get("version") or mod.get("latest_version") or "unknown",
                    "enabled": bool(attached.get("enabled", False)),
                    "installed": bool(attached) or str(mod.get("status", "")).upper() == "INSTALLED",
                }
            )
        return rows

    def _editable_settings_payload(self, server_id: str) -> dict[str, Any]:
        config, _ = self._get_server_or_404(server_id)
        definition = game_definition(config.game)
        fields = definition.web_editable_fields()
        field_schema = definition.web_editable_schema()
        result: dict[str, Any] = {}
        if "name" in fields:
            result["name"] = config.name
        if "password" in fields:
            result["password"] = ""
        if "public" in fields:
            result["public"] = bool(config.public)
        if "crossplay" in fields:
            result["crossplay"] = bool(config.crossplay)
        return {"fields": fields, "field_schema": field_schema, "values": result}

    def _apply_editable_settings(self, server_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        config, _ = self._get_server_or_404(server_id)
        definition = game_definition(config.game)
        fields = set(definition.web_editable_fields())
        updates = payload or {}
        if "name" in fields and "name" in updates:
            config.name = str(updates.get("name") or "").strip() or config.name
        if "password" in fields and "password" in updates:
            config.password = str(updates.get("password") or "")
        if "public" in fields and "public" in updates:
            config.public = bool(updates.get("public"))
        if "crossplay" in fields and "crossplay" in updates:
            config.crossplay = bool(updates.get("crossplay"))
        return self._editable_settings_payload(server_id)


def create_web_app(
    settings: AppSettings,
    manager,
    logger: logging.Logger,
    on_settings_changed: Callable[[], None] | None = None,
    web_ui_root: Path | None = None,
    app_root: Path | None = None,
) -> FastAPI:
    backend = _WebControlBackend(settings, manager, logger, app_root)
    app = FastAPI(title="Server Manager Web Control", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.user_manager = backend.user_manager
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
        user = backend.user_manager.get(session.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        # Legacy /api routes are not access-scoped; owner sessions only.
        if user.role != ROLE_OWNER:
            raise HTTPException(status_code=403, detail="forbidden")
        return session

    async def _require_v1_access(request: Request) -> _AccessContext:
        requested_user = request.headers.get("X-SM-User", "").strip()
        if backend._is_api_key_authorized(request):
            if requested_user and backend.user_manager.get(requested_user) is None:
                raise HTTPException(status_code=403, detail="forbidden")
            return _AccessContext(session=None, via_api_key=True, user=backend.user_manager.resolve(requested_user or None))
        session = backend._session_from_request(request)
        if not session:
            raise HTTPException(status_code=401, detail="unauthorized")
        # Identity comes only from the server-side session; client headers are ignored.
        user = backend.user_manager.get(session.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        return _AccessContext(session=session, via_api_key=False, user=user)

    def _v1_success(**payload: Any) -> dict[str, Any]:
        return {"success": True, **payload}

    def _require_permission(access: _AccessContext, permission: str) -> None:
        backend.permission_manager.require(access.user, permission)

    def _server_for(access: _AccessContext, server_id: str):
        # Unauthorized servers look identical to missing ones.
        config, process = backend._get_server_or_404(server_id)
        if not backend.user_manager.can_access_server(access.user, config):
            raise HTTPException(status_code=404, detail="server_not_found")
        return config, process

    def _require_server_owner(access: _AccessContext, server_id: str) -> None:
        config, _ = _server_for(access, server_id)
        if not backend.user_manager.is_server_owner(access.user, config):
            raise HTTPException(status_code=403, detail="forbidden")

    def _job_visible(access: _AccessContext, job: _Job) -> bool:
        return access.user.role == ROLE_OWNER or job.owner_id == access.user.user_id

    def _visible_server_ids(access: _AccessContext) -> list[str]:
        return [
            sid for sid, cfg in list(manager.configs.items())
            if backend.user_manager.can_access_server(access.user, cfg)
        ]

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = str(exc.detail)
        if request.url.path.startswith("/api/v1/"):
            return JSONResponse(
                status_code=exc.status_code,
                content={"success": False, "error": detail, "message": detail.replace("_", " ").capitalize()},
            )
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
        login_user = str((body or {}).get("user_id") or "owner-local").strip()
        try:
            if login_user == "owner-local":
                backend.password_hasher.verify(settings.web_control_password_hash, password)
            elif not backend.user_manager.verify_password(login_user, password):
                raise VerifyMismatchError()
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
                user_id=login_user,
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

    @app.get("/api/v1/status")
    async def api_v1_status(_access: _AccessContext = Depends(_require_v1_access)):
        return _v1_success(status="online", version=APP_VERSION)

    @app.get("/api/servers")
    async def api_servers(_session: _Session = Depends(_require_auth)):
        servers = [backend._server_payload(server_id) for server_id in manager.configs.keys()]
        return {"servers": servers}

    @app.get("/api/v1/servers")
    async def api_v1_servers(_access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        servers = [backend._server_payload_v1(server_id) for server_id in _visible_server_ids(_access)]
        return _v1_success(servers=servers)

    @app.get("/api/v1/games")
    async def api_v1_games(_access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        return _v1_success(games=backend._games_payload())

    @app.get("/api/v1/games/{game_id}")
    async def api_v1_game(game_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        definition = game_definition(game_id)
        if definition.id != game_id:
            raise HTTPException(status_code=404, detail="game_not_found")
        status = backend._public_game_state(game_id)
        payload = game_public_payload(definition)
        payload["name"] = definition.display_name
        capabilities = payload.get("capabilities", {}).copy()
        capabilities["create_server"] = backend._can_web_create(game_id)
        payload["capabilities"] = capabilities
        payload["status"] = status
        payload["setup"] = {
            "supported": bool(definition.supported),
            "automatic": bool(definition.installation_capability == "FULL"),
            "requirements": [
                "steam" if definition.steam_app_id else "",
                "java" if definition.requires_java else "",
            ],
            "installer": "server_manager",
        }
        payload["setup"]["requirements"] = [req for req in payload["setup"]["requirements"] if req]
        return _v1_success(game=payload)

    @app.post("/api/v1/games/{game_id}/setup")
    async def api_v1_setup_game(game_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CREATE_SERVERS)
        job = backend._start_game_setup(game_id, owner_id=access.user.user_id)
        logger.info("Web API v1 action: game=%s action=setup", game_id)
        return _v1_success(job_id=job.job_id)

    @app.get("/api/v1/games/{game_id}/create-schema")
    async def api_v1_create_schema(game_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        return _v1_success(game_id=game_id, schema=backend._create_schema_payload(game_id))

    @app.get("/api/v1/games/{game_id}/worlds")
    async def api_v1_game_worlds(game_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        definition = game_definition(game_id)
        if definition.id != game_id:
            raise HTTPException(status_code=404, detail="game_not_found")
        worlds = [{"id": name, "name": name} for name in discover_world_names(game_id, backend.app_root)]
        return _v1_success(game_id=game_id, worlds=worlds)

    @app.get("/api/servers/{server_id}")
    async def api_server(server_id: str, _session: _Session = Depends(_require_auth)):
        backend._get_server_or_404(server_id)
        return backend._server_payload(server_id)

    @app.get("/api/v1/servers/{server_id}")
    async def api_v1_server(server_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        _server_for(_access, server_id)
        return _v1_success(server=backend._server_payload_v1(server_id))

    @app.get("/api/v1/me")
    async def api_v1_me(access: _AccessContext = Depends(_require_v1_access)):
        return _v1_success(user=access.user.to_payload())

    @app.get("/api/v1/users")
    async def api_v1_users(access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(access, PERMISSION_VIEW_USERS)
        return _v1_success(users=[user.to_payload() for user in backend.user_manager.list_users()])

    @app.post("/api/v1/users")
    async def api_v1_create_user(request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_USERS)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid_body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="invalid_body")
        user_id = body.get("user_id")
        password = body.get("password")
        if not isinstance(user_id, str) or not user_id.strip():
            raise HTTPException(status_code=400, detail="user_id_required")
        if not isinstance(password, str) or not password:
            raise HTTPException(status_code=400, detail="password_required")
        display_name = body.get("name")
        if display_name == "":
            display_name = None
        try:
            with backend.lock:
                user = backend.user_manager.upsert_user(
                    user_id=user_id,
                    password=password,
                    display_name=display_name,
                    role=body.get("role"),
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except UserStoreError as exc:
            logger.exception("Failed to persist provisioned user")
            raise HTTPException(status_code=500, detail="user_store_unavailable") from exc
        logger.info("Web API v1 action: user=%s action=provision", user.user_id)
        _record_access_event(
            access,
            "user_provision",
            {
                "user_id": user.user_id,
                "role": user.role,
                "active": user.active,
            },
        )
        return _v1_success(user=user.to_payload())

    @app.post("/api/v1/users/{user_id}/deactivate")
    async def api_v1_deactivate_user(user_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_USERS)
        target = user_id.strip()
        if not target:
            raise HTTPException(status_code=400, detail="user_id_required")
        if target == access.user.user_id:
            raise HTTPException(status_code=400, detail="cannot_deactivate_self")
        try:
            with backend.lock:
                user = backend.user_manager.set_active(target, False)
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail == "user_not_found" else 400
            raise HTTPException(status_code=status_code, detail=detail) from exc
        except UserStoreError as exc:
            logger.exception("Failed to persist deactivated user")
            raise HTTPException(status_code=500, detail="user_store_unavailable") from exc
        _record_access_event(access, "user_deactivate", {"user_id": user.user_id})
        return _v1_success(user=user.to_payload())

    @app.post("/api/v1/users/{user_id}/activate")
    async def api_v1_activate_user(user_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_USERS)
        target = user_id.strip()
        if not target:
            raise HTTPException(status_code=400, detail="user_id_required")
        try:
            with backend.lock:
                user = backend.user_manager.set_active(target, True)
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail == "user_not_found" else 400
            raise HTTPException(status_code=status_code, detail=detail) from exc
        except UserStoreError as exc:
            logger.exception("Failed to persist activated user")
            raise HTTPException(status_code=500, detail="user_store_unavailable") from exc
        _record_access_event(access, "user_activate", {"user_id": user.user_id})
        return _v1_success(user=user.to_payload())

    @app.get("/api/v1/access-matrix")
    async def api_v1_access_matrix(access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(access, PERMISSION_VIEW_USERS)
        _require_permission(access, PERMISSION_VIEW_SERVERS)
        return _v1_success(**_access_matrix_payload(access))

    @app.get("/api/v1/access-audit")
    async def api_v1_access_audit(access: _AccessContext = Depends(_require_v1_access), limit: int = 50):
        _require_permission(access, PERMISSION_VIEW_USERS)
        bounded_limit = max(1, min(int(limit or 50), 200))
        with backend.lock:
            rows = list(backend.access_audit[:bounded_limit])
        return _v1_success(events=rows)

    @app.post("/api/v1/invitations")
    async def api_v1_invitations(request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_USERS)
        body = await request.json()
        target = str((body or {}).get("target") or "").strip()
        role = str((body or {}).get("role") or "MEMBER").strip().upper()
        if not target:
            raise HTTPException(status_code=400, detail="target_required")
        invitation = {
            "id": secrets.token_urlsafe(8),
            "target": target,
            "role": role,
            "status": "pending",
            "created_by": access.user.user_id,
            "created_at": time.time(),
        }
        with backend.lock:
            backend.invitations.insert(0, invitation)
            del backend.invitations[30:]
        return _v1_success(invitation=invitation)

    @app.get("/api/v1/invitations")
    async def api_v1_list_invitations(access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(access, PERMISSION_MANAGE_USERS)
        with backend.lock:
            invitations = list(backend.invitations)
        return _v1_success(invitations=invitations)

    @app.delete("/api/v1/invitations/{invitation_id}")
    async def api_v1_delete_invitation(invitation_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_USERS)
        removed = False
        with backend.lock:
            retained = []
            for invitation in backend.invitations:
                if str(invitation.get("id", "")) == invitation_id:
                    removed = True
                    continue
                retained.append(invitation)
            backend.invitations[:] = retained
        if not removed:
            raise HTTPException(status_code=404, detail="invitation_not_found")
        return _v1_success(invitation_id=invitation_id)

    def _require_v1_write(request: Request, access: _AccessContext) -> None:
        if access.via_api_key:
            return
        if not access.session:
            raise HTTPException(status_code=401, detail="unauthorized")
        backend._require_csrf(request, access.session)

    def _acquire_action_lock_or_raise(server_id: str) -> threading.Lock:
        lock = backend._action_lock(server_id)
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="operation_in_progress")
        return lock

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

    @app.post("/api/v1/servers/{server_id}/start")
    async def api_v1_start(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CONTROL_SERVERS)
        _server_for(access, server_id)
        lock = _acquire_action_lock_or_raise(server_id)
        try:
            process = manager.processes[server_id]
            state = backend._server_state(process)
            if state in {"RUNNING", "STARTING"}:
                return _v1_success(status=state.lower())
            try:
                manager.start(server_id)
            except Exception:
                raise HTTPException(status_code=400, detail="start_failed")
            logger.info("Web API v1 action: server=%s action=start", server_id)
            return _v1_success(status=backend._server_state(process).lower())
        finally:
            lock.release()

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

    @app.post("/api/v1/servers/{server_id}/stop")
    async def api_v1_stop(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CONTROL_SERVERS)
        _server_for(access, server_id)
        lock = _acquire_action_lock_or_raise(server_id)
        try:
            process = manager.processes[server_id]
            state = backend._server_state(process)
            if state in {"STOPPED", "STOPPING"}:
                return _v1_success(status=state.lower())
            manager.stop(server_id)
            logger.info("Web API v1 action: server=%s action=stop", server_id)
            return _v1_success(status=backend._server_state(process).lower())
        finally:
            lock.release()

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

    @app.post("/api/v1/servers/{server_id}/restart")
    async def api_v1_restart(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CONTROL_SERVERS)
        _server_for(access, server_id)
        lock = _acquire_action_lock_or_raise(server_id)
        try:
            try:
                manager.restart(server_id)
            except Exception:
                raise HTTPException(status_code=400, detail="restart_failed")
            logger.info("Web API v1 action: server=%s action=restart", server_id)
            return _v1_success(status=backend._server_state(manager.processes[server_id]).lower())
        finally:
            lock.release()

    @app.post("/api/v1/servers")
    async def api_v1_create_server(request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CREATE_SERVERS)
        payload = await request.json()
        job = backend._create_job("create_server", owner_id=access.user.user_id)

        def _create_worker() -> None:
            backend._finish_job(job, "RUNNING")
            backend._append_job_progress(job, "Preparing server")
            try:
                config = backend._create_server_from_payload(payload or {}, owner_id=access.user.user_id)
                backend._append_job_progress(job, "Configuring server")
                backend._append_job_progress(job, "Validating")
                backend._finish_job(job, "COMPLETED", result={"server": backend._server_payload_v1(config.id)})
                logger.info("Web API v1 action: server=%s action=create game=%s", config.id, config.game)
            except HTTPException as exc:
                backend._finish_job(job, "FAILED", error=str(exc.detail))
            except Exception as exc:
                backend._finish_job(job, "FAILED", error=str(exc))

        threading.Thread(target=_create_worker, daemon=True, name=f"job-create-{job.job_id}").start()
        return _v1_success(job_id=job.job_id)

    @app.delete("/api/v1/servers/{server_id}")
    async def api_v1_delete_server(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_DELETE_SERVERS)
        _require_server_owner(access, server_id)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if str((body or {}).get("confirm") or "") != server_id:
            raise HTTPException(status_code=400, detail="confirmation_required")
        lock = _acquire_action_lock_or_raise(server_id)
        job = backend._create_job("delete_server", metadata={"server_id": server_id}, owner_id=access.user.user_id)

        def _delete_worker() -> None:
            try:
                backend._finish_job(job, "RUNNING")
                backend._append_job_progress(job, "Stopping server")
                backend._append_job_progress(job, "Creating safety backup")
                backup = backend._delete_server(server_id, on_settings_changed)
                backend._append_job_progress(job, "Removing server")
                backend._finish_job(job, "COMPLETED", result={"server_id": server_id, "safety_backup_id": backup["id"]})
                logger.info("Web API v1 action: server=%s action=delete", server_id)
            except HTTPException as exc:
                backend._finish_job(job, "FAILED", error=str(exc.detail))
            except Exception as exc:
                backend._finish_job(job, "FAILED", error=str(exc))
            finally:
                lock.release()

        threading.Thread(target=_delete_worker, daemon=True, name=f"job-delete-{job.job_id}").start()
        return _v1_success(job_id=job.job_id)

    @app.get("/api/v1/jobs/{job_id}")
    async def api_v1_job(job_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        with backend.lock:
            job = backend.jobs.get(job_id)
        if not job or not _job_visible(_access, job):
            raise HTTPException(status_code=404, detail="job_not_found")
        return _v1_success(job=backend._job_payload(job))

    @app.get("/api/v1/jobs")
    async def api_v1_jobs(_access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        return _v1_success(jobs=backend._jobs_payload(_access.user))

    @app.get("/api/v1/tasks")
    async def api_v1_tasks(_access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        return _v1_success(tasks=backend._jobs_payload(_access.user))

    @app.get("/api/v1/tasks/{task_id}")
    async def api_v1_task(task_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        with backend.lock:
            job = backend.jobs.get(task_id)
        if not job or not _job_visible(_access, job):
            raise HTTPException(status_code=404, detail="task_not_found")
        return _v1_success(task=backend._job_payload(job))

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

    @app.get("/api/v1/servers/{server_id}/logs")
    async def api_v1_logs(server_id: str, cursor: int = 0, limit: int = 200, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        _, process = _server_for(_access, server_id)
        all_lines = [backend._sanitize_line(line) for line in process.recent_output]
        total = len(all_lines)
        if cursor <= 0:
            bounded_limit = max(1, min(limit, 500))
            lines = all_lines[-bounded_limit:]
            return _v1_success(server_id=server_id, lines=lines, cursor=total)

        safe_cursor = max(0, min(cursor, total))
        lines = all_lines[safe_cursor:]
        if len(lines) > 500:
            lines = lines[-500:]
        return _v1_success(server_id=server_id, lines=lines, cursor=total)

    @app.get("/api/v1/servers/{server_id}/players")
    async def api_v1_players(server_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        _server_for(_access, server_id)
        return _v1_success(server_id=server_id, **backend._server_players_payload(server_id))

    @app.get("/api/v1/servers/{server_id}/moderation")
    async def api_v1_server_moderation(server_id: str, access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(access, PERMISSION_VIEW_SERVERS)
        _server_for(access, server_id)
        with backend.lock:
            payload = backend._server_moderation_payload(server_id)
        return _v1_success(**payload)

    @app.post("/api/v1/servers/{server_id}/moderation/ban")
    async def api_v1_ban_player(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CONTROL_SERVERS)
        _server_for(access, server_id)
        body = await request.json()
        player = backend._normalize_player_name((body or {}).get("player") or "")
        reason = str((body or {}).get("reason") or "").strip()
        command_status = backend._dispatch_moderation_command(server_id, "ban", player, reason)
        with backend.lock:
            bans = backend.player_bans.setdefault(server_id, {})
            bans[player] = {"reason": reason, "by": access.user.user_id, "at": time.time()}
            backend._record_player_moderation_event(
                server_id,
                "ban",
                access.user.user_id,
                {"player": player, "reason": reason, "command": command_status},
            )
            payload = backend._server_moderation_payload(server_id)
        return _v1_success(command=command_status, **payload)

    @app.post("/api/v1/servers/{server_id}/moderation/unban")
    async def api_v1_unban_player(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CONTROL_SERVERS)
        _server_for(access, server_id)
        body = await request.json()
        player = backend._normalize_player_name((body or {}).get("player") or "")
        command_status = backend._dispatch_moderation_command(server_id, "unban", player)
        with backend.lock:
            bans = backend.player_bans.setdefault(server_id, {})
            if player not in bans:
                raise HTTPException(status_code=404, detail="player_not_banned")
            bans.pop(player, None)
            backend._record_player_moderation_event(
                server_id,
                "unban",
                access.user.user_id,
                {"player": player, "command": command_status},
            )
            payload = backend._server_moderation_payload(server_id)
        return _v1_success(command=command_status, **payload)

    @app.post("/api/v1/servers/{server_id}/moderation/kick")
    async def api_v1_kick_player(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CONTROL_SERVERS)
        _server_for(access, server_id)
        body = await request.json()
        player = backend._normalize_player_name((body or {}).get("player") or "")
        reason = str((body or {}).get("reason") or "").strip()
        command_status = backend._dispatch_moderation_command(server_id, "kick", player, reason)
        with backend.lock:
            backend._record_player_moderation_event(
                server_id,
                "kick",
                access.user.user_id,
                {
                    "player": player,
                    "reason": reason,
                    "status": "sent" if command_status.get("sent") else "queued",
                    "command": command_status,
                },
            )
            payload = backend._server_moderation_payload(server_id)
        return _v1_success(status="sent" if command_status.get("sent") else "queued", command=command_status, **payload)

    @app.post("/api/v1/servers/{server_id}/moderation/note")
    async def api_v1_note_player(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CONTROL_SERVERS)
        _server_for(access, server_id)
        body = await request.json()
        player = backend._normalize_player_name((body or {}).get("player") or "")
        note = str((body or {}).get("note") or "").strip()
        if not note:
            raise HTTPException(status_code=400, detail="note_required")
        if len(note) > 400:
            raise HTTPException(status_code=400, detail="note_too_long")
        with backend.lock:
            notes = backend.player_notes.setdefault(server_id, {})
            notes[player] = {"note": note, "by": access.user.user_id, "updated_at": time.time()}
            backend._record_player_moderation_event(
                server_id,
                "note",
                access.user.user_id,
                {"player": player, "note": note},
            )
            payload = backend._server_moderation_payload(server_id)
        return _v1_success(**payload)

    @app.get("/api/v1/servers/{server_id}/settings")
    async def api_v1_server_settings(server_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        _server_for(_access, server_id)
        payload = backend._editable_settings_payload(server_id)
        return _v1_success(server_id=server_id, **payload)

    @app.patch("/api/v1/servers/{server_id}/settings")
    async def api_v1_update_server_settings(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_CONTROL_SERVERS)
        _server_for(access, server_id)
        body = await request.json()
        payload = backend._apply_editable_settings(server_id, body or {})
        if on_settings_changed:
            on_settings_changed()
        logger.info("Web API v1 action: server=%s action=update_settings", server_id)
        return _v1_success(server_id=server_id, **payload)

    def _access_payload(server_id: str, config) -> dict[str, Any]:
        return {"server_id": server_id, "owner_id": config.owner_id, "shared_with": list(config.shared_with)}

    def _record_access_event(access: _AccessContext, action: str, payload: dict[str, Any]) -> None:
        event = {
            "id": secrets.token_urlsafe(8),
            "action": action,
            "actor": access.user.user_id,
            "at": time.time(),
            "payload": payload,
        }
        with backend.lock:
            backend.access_audit.insert(0, event)
            del backend.access_audit[200:]

    def _access_matrix_payload(access: _AccessContext) -> dict[str, Any]:
        visible_ids = _visible_server_ids(access)
        users = [user.to_payload() for user in backend.user_manager.list_users()]
        servers = []
        for server_id in visible_ids:
            config, _ = backend._get_server_or_404(server_id)
            servers.append(
                {
                    "id": config.id,
                    "name": config.name,
                    "owner_id": config.owner_id,
                    "shared_with": list(config.shared_with),
                    "can_manage_access": backend.user_manager.is_server_owner(access.user, config),
                }
            )
        return {"users": users, "servers": servers}

    async def _access_target(request: Request) -> str:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid_body")
        if not isinstance(body, dict) or not isinstance(body.get("user_id"), str):
            raise HTTPException(status_code=400, detail="user_id_required")
        target = body["user_id"].strip()
        if not target or len(target) > 64:
            raise HTTPException(status_code=400, detail="user_id_required")
        return target

    # Only the server owner (or global OWNER role) may administer access.
    @app.get("/api/v1/servers/{server_id}/access")
    async def api_v1_server_access(server_id: str, access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(access, PERMISSION_VIEW_SERVERS)
        _require_server_owner(access, server_id)
        config, _ = backend._get_server_or_404(server_id)
        return _v1_success(**_access_payload(server_id, config))

    @app.post("/api/v1/servers/{server_id}/access")
    async def api_v1_grant_server_access(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_VIEW_SERVERS)
        _require_server_owner(access, server_id)
        target = await _access_target(request)
        if target == access.user.user_id:
            raise HTTPException(status_code=400, detail="cannot_grant_self")
        target_user = backend.user_manager.get(target)
        if target_user is None:
            raise HTTPException(status_code=404, detail="user_not_found")
        config, _ = backend._get_server_or_404(server_id)
        if backend.user_manager.can_access_server(target_user, config) and target not in config.shared_with:
            raise HTTPException(status_code=400, detail="user_already_has_access")
        with backend.lock:
            if target in config.shared_with:
                raise HTTPException(status_code=409, detail="access_already_granted")
            config.shared_with.append(target)
        if on_settings_changed:
            on_settings_changed()
        logger.info("Web API v1 action: server=%s action=grant_access", server_id)
        _record_access_event(access, "grant_access", {"server_id": server_id, "user_id": target})
        return _v1_success(**_access_payload(server_id, config))

    @app.delete("/api/v1/servers/{server_id}/access/{user_id}")
    async def api_v1_revoke_server_access(server_id: str, user_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_VIEW_SERVERS)
        _require_server_owner(access, server_id)
        config, _ = backend._get_server_or_404(server_id)
        target = user_id.strip()
        with backend.lock:
            if target not in config.shared_with:
                raise HTTPException(status_code=404, detail="access_not_found")
            config.shared_with[:] = [u for u in config.shared_with if u != target]
        if on_settings_changed:
            on_settings_changed()
        logger.info("Web API v1 action: server=%s action=revoke_access", server_id)
        _record_access_event(access, "revoke_access", {"server_id": server_id, "user_id": target})
        return _v1_success(**_access_payload(server_id, config))

    @app.get("/api/v1/mods")
    async def api_v1_mods(game: str = "", _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        if backend.mod_manager is None:
            return _v1_success(mods=[])
        mods = backend.mod_manager.list_mods(game=game.strip() or None)
        rows = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "game": item.get("game"),
                "version": item.get("latest_version") or "unknown",
                "status": str(item.get("status") or "available").lower(),
            }
            for item in mods
        ]
        return _v1_success(mods=rows)

    @app.get("/api/v1/mods/{mod_id}")
    async def api_v1_mod(mod_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        if backend.mod_manager is None:
            raise HTTPException(status_code=404, detail="mod_not_found")
        mod = next((item for item in backend.mod_manager.list_mods() if str(item.get("id")) == mod_id), None)
        if not mod:
            raise HTTPException(status_code=404, detail="mod_not_found")
        visible_ids = set(_visible_server_ids(_access))
        detail = {
            "id": mod.get("id"),
            "name": mod.get("name"),
            "game": mod.get("game"),
            "version": mod.get("latest_version") or "unknown",
            "status": str(mod.get("status") or "available").lower(),
            "used_by": [sid for sid in backend.mod_manager.servers_using_mod(mod_id) if sid in visible_ids],
        }
        return _v1_success(mod=detail)

    @app.get("/api/v1/servers/{server_id}/mods")
    async def api_v1_server_mods(server_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        _server_for(_access, server_id)
        return _v1_success(server_id=server_id, mods=backend._list_server_mods(server_id))

    @app.post("/api/v1/servers/{server_id}/mods/{mod_id}/enable")
    async def api_v1_enable_mod(server_id: str, mod_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_MODS)
        _server_for(access, server_id)
        if backend.mod_manager is None:
            raise HTTPException(status_code=501, detail="mods_not_configured")
        config, _ = backend._get_server_or_404(server_id)
        backend.mod_manager.enable_mod_for_server(config, mod_id)
        logger.info("Web API v1 action: server=%s action=mod_enable mod=%s", server_id, mod_id)
        return _v1_success(server_id=server_id, mod_id=mod_id)

    @app.post("/api/v1/servers/{server_id}/mods/{mod_id}/disable")
    async def api_v1_disable_mod(server_id: str, mod_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_MODS)
        _server_for(access, server_id)
        if backend.mod_manager is None:
            raise HTTPException(status_code=501, detail="mods_not_configured")
        backend._get_server_or_404(server_id)
        backend.mod_manager.disable_mod_for_server(server_id, mod_id)
        logger.info("Web API v1 action: server=%s action=mod_disable mod=%s", server_id, mod_id)
        return _v1_success(server_id=server_id, mod_id=mod_id)

    @app.post("/api/v1/servers/{server_id}/mods/{mod_id}/install")
    async def api_v1_install_mod(server_id: str, mod_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_MODS)
        _server_for(access, server_id)
        if backend.mod_manager is None:
            raise HTTPException(status_code=501, detail="mods_not_configured")
        config, _ = backend._get_server_or_404(server_id)
        backend.mod_manager.enable_mod_for_server(config, mod_id)
        logger.info("Web API v1 action: server=%s action=mod_install mod=%s", server_id, mod_id)
        return _v1_success(server_id=server_id, mod_id=mod_id)

    @app.post("/api/v1/servers/{server_id}/mods/{mod_id}/uninstall")
    async def api_v1_uninstall_mod(server_id: str, mod_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_MODS)
        _server_for(access, server_id)
        if backend.mod_manager is None:
            raise HTTPException(status_code=501, detail="mods_not_configured")
        config, _ = backend._get_server_or_404(server_id)
        backend.mod_manager.uninstall_mod_from_server(config, mod_id)
        logger.info("Web API v1 action: server=%s action=mod_uninstall mod=%s", server_id, mod_id)
        return _v1_success(server_id=server_id, mod_id=mod_id)

    @app.get("/api/v1/mod-profiles")
    async def api_v1_mod_profiles(game: str = "", _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        if backend.mod_manager is None:
            return _v1_success(profiles=[])
        profiles = backend.mod_manager.list_profiles(game=game.strip() or None)
        return _v1_success(profiles=profiles)

    @app.post("/api/v1/servers/{server_id}/mod-profile")
    async def api_v1_apply_mod_profile(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_MODS)
        _server_for(access, server_id)
        if backend.mod_manager is None:
            raise HTTPException(status_code=501, detail="mods_not_configured")
        body = await request.json()
        profile_name = str((body or {}).get("profile") or "").strip()
        if not profile_name:
            raise HTTPException(status_code=400, detail="profile_required")
        config, _ = backend._get_server_or_404(server_id)
        changes = backend.mod_manager.apply_profile(config, profile_name)
        logger.info("Web API v1 action: server=%s action=mod_profile profile=%s", server_id, profile_name)
        return _v1_success(server_id=server_id, profile=profile_name, changes=changes)

    @app.get("/api/v1/servers/{server_id}/backups")
    async def api_v1_server_backups(server_id: str, _access: _AccessContext = Depends(_require_v1_access)):
        _require_permission(_access, PERMISSION_VIEW_SERVERS)
        _server_for(_access, server_id)
        return _v1_success(
            server_id=server_id,
            backups=backend._list_backups(server_id),
            restore_history=backend._list_restore_history(server_id),
        )

    @app.post("/api/v1/servers/{server_id}/backups")
    async def api_v1_create_backup(server_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_BACKUPS)
        _server_for(access, server_id)
        job = backend._create_job("create_backup", owner_id=access.user.user_id)

        def _backup_worker() -> None:
            backend._finish_job(job, "RUNNING")
            backend._append_job_progress(job, "Creating backup")
            try:
                backup = backend._create_backup(server_id)
                backend._finish_job(job, "COMPLETED", result={"server_id": server_id, "backup": backup})
                logger.info("Web API v1 action: server=%s action=backup_create", server_id)
            except HTTPException as exc:
                backend._finish_job(job, "FAILED", error=str(exc.detail))
            except Exception as exc:
                backend._finish_job(job, "FAILED", error=str(exc))

        threading.Thread(target=_backup_worker, daemon=True, name=f"job-backup-{job.job_id}").start()
        return _v1_success(job_id=job.job_id)

    @app.post("/api/v1/servers/{server_id}/backups/{backup_id}/restore")
    async def api_v1_restore_backup(server_id: str, backup_id: str, request: Request, access: _AccessContext = Depends(_require_v1_access)):
        _require_v1_write(request, access)
        _require_permission(access, PERMISSION_MANAGE_BACKUPS)
        _server_for(access, server_id)
        job = backend._create_job("restore_backup", owner_id=access.user.user_id)

        def _restore_worker() -> None:
            backend._finish_job(job, "RUNNING")
            backend._append_job_progress(job, "Creating safety backup")
            try:
                backend._append_job_progress(job, "Restoring backup")
                backend._restore_backup(server_id, backup_id)
                backend._append_job_progress(job, "Validating")
                backend._finish_job(job, "COMPLETED", result={"server_id": server_id, "backup_id": backup_id})
                logger.info("Web API v1 action: server=%s action=backup_restore backup=%s", server_id, backup_id)
            except HTTPException as exc:
                backend._record_restore_event(
                    server_id,
                    {
                        "backup_id": backup_id,
                        "restored_at": time.time(),
                        "safety_backup_id": "",
                        "status": "failed",
                        "error": str(exc.detail),
                    },
                )
                backend._finish_job(job, "FAILED", error=str(exc.detail))
            except Exception as exc:
                backend._record_restore_event(
                    server_id,
                    {
                        "backup_id": backup_id,
                        "restored_at": time.time(),
                        "safety_backup_id": "",
                        "status": "failed",
                        "error": str(exc),
                    },
                )
                backend._finish_job(job, "FAILED", error=str(exc))

        threading.Thread(target=_restore_worker, daemon=True, name=f"job-restore-{job.job_id}").start()
        return _v1_success(job_id=job.job_id)

    return app
