from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from website_backend.server_manager_client import ServerManagerClient, ServerManagerClientError

SESSION_COOKIE = "sm_remote_session"
SESSION_TTL_SECONDS = 60 * 60 * 12


def _build_client() -> ServerManagerClient:
    base_url = os.environ.get("SM_API_BASE_URL", "http://127.0.0.1:8080")
    api_key = os.environ.get("SM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SM_API_KEY must be configured for website-backend")
    return ServerManagerClient(base_url=base_url, api_key=api_key)


app = FastAPI(title="Server Manager Website Backend", docs_url=None, redoc_url=None, openapi_url=None)
client = _build_client()
website_root = Path(__file__).resolve().parents[1] / "website"
sessions: dict[str, dict[str, object]] = {}

if website_root.is_dir():
    mounts = (
        ("/assets", "assets"),
        ("/css", "css"),
        ("/js", "js"),
    )
    for route, folder in mounts:
        directory = website_root / folder
        if directory.is_dir():
            app.mount(route, StaticFiles(directory=str(directory)), name=folder)


def _purge_sessions() -> None:
    now = time.time()
    expired = [token for token, session in sessions.items() if float(session["expires"]) <= now]
    for token in expired:
        sessions.pop(token, None)


def _session_user_id(request: Request) -> str | None:
    _purge_sessions()
    token = request.cookies.get(SESSION_COOKIE, "")
    session = sessions.get(token)
    return str(session["user_id"]) if session else None


def _set_session(response: Response, user_id: str) -> None:
    token = secrets.token_urlsafe(32)
    sessions[token] = {"user_id": user_id, "expires": time.time() + SESSION_TTL_SECONDS}
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")


def _clear_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE, "")
    sessions.pop(token, None)
    response.delete_cookie(SESSION_COOKIE)


async def _require_session(request: Request) -> ServerManagerClient:
    user_id = _session_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="unauthorized")
    return replace(client, user_id=user_id)


def _verify_password_against_server_manager(user_id: str, password: str) -> bool:
    # Validate against existing Server Manager auth without exposing secrets to frontend JS.
    endpoint = os.environ.get("SM_API_BASE_URL", "http://127.0.0.1:8080").rstrip("/") + "/api/auth/login"
    body = json.dumps({"user_id": user_id, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        method="POST",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            _ = response.read()
        return True
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    detail = str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": detail, "message": detail.replace("_", " ").capitalize()},
    )


@app.get("/")
async def index():
    index_file = website_root / "index.html"
    if not index_file.is_file():
        return JSONResponse(status_code=500, content={"success": False, "error": "website_missing", "message": "Website files are missing."})
    return FileResponse(index_file)


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    user_id = str((body or {}).get("user_id", "")).strip()
    password = str((body or {}).get("password", ""))
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id_required")
    if not password:
        raise HTTPException(status_code=400, detail="password_required")
    if not _verify_password_against_server_manager(user_id, password):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    response = JSONResponse({"success": True})
    _set_session(response, user_id)
    return response


@app.post("/api/logout")
async def logout(request: Request):
    response = JSONResponse({"success": True})
    _clear_session(request, response)
    return response


@app.get("/api/status")
async def status(_session=Depends(_require_session)):
    try:
        return _session.get_status()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/me")
async def me(_session=Depends(_require_session)):
    try:
        return _session.get_me()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/users")
async def users(_session=Depends(_require_session)):
    try:
        return _session.get_users()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/users")
async def create_user(request: Request, _session=Depends(_require_session)):
    body = await request.json()
    try:
        return _session.create_user(body or {})
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/invitations")
async def invitations(request: Request, _session=Depends(_require_session)):
    body = await request.json()
    try:
        return _session.create_invitation(body or {})
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/invitations")
async def list_invitations(_session=Depends(_require_session)):
    try:
        return _session.get_invitations()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.delete("/api/invitations/{invitation_id}")
async def delete_invitation(invitation_id: str, _session=Depends(_require_session)):
    try:
        return _session.delete_invitation(invitation_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers")
async def servers(_session=Depends(_require_session)):
    try:
        return _session.get_servers()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers")
async def create_server(request: Request, _session=Depends(_require_session)):
    body = await request.json()
    try:
        return _session.create_server(body or {})
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.delete("/api/servers/{server_id}")
async def delete_server(server_id: str, request: Request, _session=Depends(_require_session)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return _session.delete_server(server_id, body or {})
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/jobs/{job_id}")
async def job(job_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_job(job_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/jobs")
async def jobs(_session=Depends(_require_session)):
    try:
        return _session.get_jobs()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/tasks")
async def tasks(_session=Depends(_require_session)):
    try:
        return _session.get_tasks()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/tasks/{task_id}")
async def task(task_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_task(task_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}")
async def server(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_server(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/access")
async def server_access(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_server_access(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/access")
async def grant_server_access(server_id: str, request: Request, _session=Depends(_require_session)):
    body = await request.json()
    user_id = str((body or {}).get("user_id", "")).strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id_required")
    try:
        return _session.grant_server_access(server_id, user_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.delete("/api/servers/{server_id}/access/{user_id}")
async def revoke_server_access(server_id: str, user_id: str, _session=Depends(_require_session)):
    try:
        return _session.revoke_server_access(server_id, user_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/players")
async def players(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_players(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/settings")
async def server_settings(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_server_settings(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.patch("/api/servers/{server_id}/settings")
async def patch_server_settings(server_id: str, request: Request, _session=Depends(_require_session)):
    body = await request.json()
    try:
        return _session.patch_server_settings(server_id, body or {})
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/games")
async def games(_session=Depends(_require_session)):
    try:
        return _session.get_games()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/games/{game_id}")
async def game(game_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_game(game_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/games/{game_id}/create-schema")
async def create_schema(game_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_create_schema(game_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/games/{game_id}/worlds")
async def worlds(game_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_worlds(game_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/games/{game_id}/setup")
async def setup_game(game_id: str, _session=Depends(_require_session)):
    try:
        return _session.setup_game(game_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/mods")
async def mods(game: str = "", _session=Depends(_require_session)):
    try:
        return _session.get_mods(game=game)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/mods/{mod_id}")
async def mod(mod_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_mod(mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/mods")
async def server_mods(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_server_mods(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mods/{mod_id}/enable")
async def enable_mod(server_id: str, mod_id: str, _session=Depends(_require_session)):
    try:
        return _session.enable_mod(server_id, mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mods/{mod_id}/disable")
async def disable_mod(server_id: str, mod_id: str, _session=Depends(_require_session)):
    try:
        return _session.disable_mod(server_id, mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mods/{mod_id}/install")
async def install_mod(server_id: str, mod_id: str, _session=Depends(_require_session)):
    try:
        return _session.install_mod(server_id, mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mods/{mod_id}/uninstall")
async def uninstall_mod(server_id: str, mod_id: str, _session=Depends(_require_session)):
    try:
        return _session.uninstall_mod(server_id, mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/mod-profiles")
async def mod_profiles(game: str = "", _session=Depends(_require_session)):
    try:
        return _session.get_mod_profiles(game=game)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mod-profile")
async def apply_mod_profile(server_id: str, request: Request, _session=Depends(_require_session)):
    body = await request.json()
    profile = str((body or {}).get("profile") or "")
    try:
        return _session.apply_mod_profile(server_id, profile)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/backups")
async def backups(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.get_backups(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/backups")
async def create_backup(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.create_backup(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/backups/{backup_id}/restore")
async def restore_backup(server_id: str, backup_id: str, _session=Depends(_require_session)):
    try:
        return _session.restore_backup(server_id, backup_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/start")
async def start(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.start_server(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/stop")
async def stop(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.stop_server(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/restart")
async def restart(server_id: str, _session=Depends(_require_session)):
    try:
        return _session.restart_server(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/logs")
async def logs(server_id: str, cursor: int = 0, limit: int = 200, _session=Depends(_require_session)):
    try:
        return _session.get_logs(server_id, cursor=cursor, limit=limit)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})
