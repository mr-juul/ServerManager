from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.request
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
sessions: dict[str, float] = {}

if website_root.is_dir():
    app.mount("/assets", StaticFiles(directory=str(website_root / "assets")), name="assets")
    app.mount("/css", StaticFiles(directory=str(website_root / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(website_root / "js")), name="js")


def _purge_sessions() -> None:
    now = time.time()
    expired = [token for token, expires in sessions.items() if expires <= now]
    for token in expired:
        sessions.pop(token, None)


def _session_ok(request: Request) -> bool:
    _purge_sessions()
    token = request.cookies.get(SESSION_COOKIE, "")
    return bool(token and token in sessions)


def _set_session(response: Response) -> None:
    token = secrets.token_urlsafe(32)
    sessions[token] = time.time() + SESSION_TTL_SECONDS
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")


def _clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


async def _require_session(request: Request) -> None:
    if not _session_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")


def _verify_password_against_server_manager(password: str) -> bool:
    # Validate against existing Server Manager auth without exposing secrets to frontend JS.
    endpoint = os.environ.get("SM_API_BASE_URL", "http://127.0.0.1:8080").rstrip("/") + "/api/auth/login"
    body = json.dumps({"password": password}).encode("utf-8")
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
    password = str((body or {}).get("password", ""))
    if not password:
        raise HTTPException(status_code=400, detail="password_required")
    if not _verify_password_against_server_manager(password):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    response = JSONResponse({"success": True})
    _set_session(response)
    return response


@app.post("/api/logout")
async def logout(_request: Request):
    response = JSONResponse({"success": True})
    _clear_session(response)
    return response


@app.get("/api/status")
async def status(_session=Depends(_require_session)):
    try:
        return client.get_status()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers")
async def servers(_session=Depends(_require_session)):
    try:
        return client.get_servers()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers")
async def create_server(request: Request, _session=Depends(_require_session)):
    body = await request.json()
    try:
        return client.create_server(body or {})
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/jobs/{job_id}")
async def job(job_id: str, _session=Depends(_require_session)):
    try:
        return client.get_job(job_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}")
async def server(server_id: str, _session=Depends(_require_session)):
    try:
        return client.get_server(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/players")
async def players(server_id: str, _session=Depends(_require_session)):
    try:
        return client.get_players(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/settings")
async def server_settings(server_id: str, _session=Depends(_require_session)):
    try:
        return client.get_server_settings(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.patch("/api/servers/{server_id}/settings")
async def patch_server_settings(server_id: str, request: Request, _session=Depends(_require_session)):
    body = await request.json()
    try:
        return client.patch_server_settings(server_id, body or {})
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/games")
async def games(_session=Depends(_require_session)):
    try:
        return client.get_games()
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/games/{game_id}")
async def game(game_id: str, _session=Depends(_require_session)):
    try:
        return client.get_game(game_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/games/{game_id}/create-schema")
async def create_schema(game_id: str, _session=Depends(_require_session)):
    try:
        return client.get_create_schema(game_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/games/{game_id}/worlds")
async def worlds(game_id: str, _session=Depends(_require_session)):
    try:
        return client.get_worlds(game_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/mods")
async def mods(game: str = "", _session=Depends(_require_session)):
    try:
        return client.get_mods(game=game)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/mods/{mod_id}")
async def mod(mod_id: str, _session=Depends(_require_session)):
    try:
        return client.get_mod(mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/mods")
async def server_mods(server_id: str, _session=Depends(_require_session)):
    try:
        return client.get_server_mods(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mods/{mod_id}/enable")
async def enable_mod(server_id: str, mod_id: str, _session=Depends(_require_session)):
    try:
        return client.enable_mod(server_id, mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mods/{mod_id}/disable")
async def disable_mod(server_id: str, mod_id: str, _session=Depends(_require_session)):
    try:
        return client.disable_mod(server_id, mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mods/{mod_id}/install")
async def install_mod(server_id: str, mod_id: str, _session=Depends(_require_session)):
    try:
        return client.install_mod(server_id, mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mods/{mod_id}/uninstall")
async def uninstall_mod(server_id: str, mod_id: str, _session=Depends(_require_session)):
    try:
        return client.uninstall_mod(server_id, mod_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/mod-profiles")
async def mod_profiles(game: str = "", _session=Depends(_require_session)):
    try:
        return client.get_mod_profiles(game=game)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/mod-profile")
async def apply_mod_profile(server_id: str, request: Request, _session=Depends(_require_session)):
    body = await request.json()
    profile = str((body or {}).get("profile") or "")
    try:
        return client.apply_mod_profile(server_id, profile)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/backups")
async def backups(server_id: str, _session=Depends(_require_session)):
    try:
        return client.get_backups(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/backups")
async def create_backup(server_id: str, _session=Depends(_require_session)):
    try:
        return client.create_backup(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/backups/{backup_id}/restore")
async def restore_backup(server_id: str, backup_id: str, _session=Depends(_require_session)):
    try:
        return client.restore_backup(server_id, backup_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/start")
async def start(server_id: str, _session=Depends(_require_session)):
    try:
        return client.start_server(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/stop")
async def stop(server_id: str, _session=Depends(_require_session)):
    try:
        return client.stop_server(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.post("/api/servers/{server_id}/restart")
async def restart(server_id: str, _session=Depends(_require_session)):
    try:
        return client.restart_server(server_id)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})


@app.get("/api/servers/{server_id}/logs")
async def logs(server_id: str, cursor: int = 0, limit: int = 200, _session=Depends(_require_session)):
    try:
        return client.get_logs(server_id, cursor=cursor, limit=limit)
    except ServerManagerClientError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": exc.error, "message": exc.message})
