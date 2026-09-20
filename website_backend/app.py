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


@app.get("/api/servers/{server_id}")
async def server(server_id: str, _session=Depends(_require_session)):
    try:
        return client.get_server(server_id)
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
