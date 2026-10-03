from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


class ServerManagerClientError(RuntimeError):
    def __init__(self, error: str, message: str, status_code: int = 502):
        super().__init__(message)
        self.error = error
        self.message = message
        self.status_code = status_code


@dataclass
class ServerManagerClient:
    base_url: str
    api_key: str
    timeout: int = 15
    user_id: str | None = None

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }
        if self.user_id:
            headers["X-SM-User"] = self.user_id
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}{path}",
            method=method,
            data=data,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
            parsed = json.loads(body or "{}")
        except urllib.error.HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                error_payload = {}
            if not isinstance(error_payload, dict):
                error_payload = {}
            raise ServerManagerClientError(
                error=str(error_payload.get("error", "server_manager_error")),
                message=str(error_payload.get("message", f"HTTP {exc.code} from Server Manager")),
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise ServerManagerClientError("server_manager_offline", "Server Manager is offline.", 503) from exc
        except json.JSONDecodeError as exc:
            raise ServerManagerClientError("invalid_response", "Server Manager returned invalid JSON.", 502) from exc

        if not bool(parsed.get("success", False)):
            raise ServerManagerClientError(
                error=str(parsed.get("error", "server_manager_error")),
                message=str(parsed.get("message", "Request failed")),
                status_code=400,
            )
        return parsed

    def get_status(self) -> dict:
        return self._call("GET", "/api/v1/status")

    def get_me(self) -> dict:
        return self._call("GET", "/api/v1/me")

    def get_users(self) -> dict:
        return self._call("GET", "/api/v1/users")

    def create_user(self, payload: dict) -> dict:
        return self._call("POST", "/api/v1/users", payload=payload)

    def activate_user(self, user_id: str) -> dict:
        return self._call("POST", f"/api/v1/users/{user_id}/activate", payload={})

    def deactivate_user(self, user_id: str) -> dict:
        return self._call("POST", f"/api/v1/users/{user_id}/deactivate", payload={})

    def get_access_matrix(self) -> dict:
        return self._call("GET", "/api/v1/access-matrix")

    def get_access_audit(self, limit: int = 50) -> dict:
        return self._call("GET", f"/api/v1/access-audit?limit={max(1, int(limit))}")

    def create_invitation(self, payload: dict) -> dict:
        return self._call("POST", "/api/v1/invitations", payload=payload)

    def get_invitations(self) -> dict:
        return self._call("GET", "/api/v1/invitations")

    def delete_invitation(self, invitation_id: str) -> dict:
        return self._call("DELETE", f"/api/v1/invitations/{invitation_id}")

    def get_servers(self) -> dict:
        return self._call("GET", "/api/v1/servers")

    def get_server(self, server_id: str) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}")

    def get_server_access(self, server_id: str) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}/access")

    def grant_server_access(self, server_id: str, user_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/access", payload={"user_id": user_id})

    def revoke_server_access(self, server_id: str, user_id: str) -> dict:
        return self._call("DELETE", f"/api/v1/servers/{server_id}/access/{user_id}")

    def create_server(self, payload: dict) -> dict:
        return self._call("POST", "/api/v1/servers", payload=payload)

    def get_job(self, job_id: str) -> dict:
        return self._call("GET", f"/api/v1/jobs/{job_id}")

    def delete_server(self, server_id: str, payload: dict) -> dict:
        return self._call("DELETE", f"/api/v1/servers/{server_id}", payload=payload)

    def get_jobs(self) -> dict:
        return self._call("GET", "/api/v1/jobs")

    def get_tasks(self) -> dict:
        return self._call("GET", "/api/v1/tasks")

    def get_task(self, task_id: str) -> dict:
        return self._call("GET", f"/api/v1/tasks/{task_id}")

    def start_server(self, server_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/start", payload={})

    def stop_server(self, server_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/stop", payload={})

    def restart_server(self, server_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/restart", payload={})

    def get_logs(self, server_id: str, cursor: int = 0, limit: int = 200) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}/logs?cursor={int(cursor)}&limit={int(limit)}")

    def get_players(self, server_id: str) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}/players")

    def get_moderation(self, server_id: str) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}/moderation")

    def kick_player(self, server_id: str, payload: dict) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/moderation/kick", payload=payload)

    def ban_player(self, server_id: str, payload: dict) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/moderation/ban", payload=payload)

    def unban_player(self, server_id: str, payload: dict) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/moderation/unban", payload=payload)

    def note_player(self, server_id: str, payload: dict) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/moderation/note", payload=payload)

    def get_server_settings(self, server_id: str) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}/settings")

    def patch_server_settings(self, server_id: str, payload: dict) -> dict:
        return self._call("PATCH", f"/api/v1/servers/{server_id}/settings", payload=payload)

    def get_games(self) -> dict:
        return self._call("GET", "/api/v1/games")

    def get_game(self, game_id: str) -> dict:
        return self._call("GET", f"/api/v1/games/{game_id}")

    def get_create_schema(self, game_id: str) -> dict:
        return self._call("GET", f"/api/v1/games/{game_id}/create-schema")

    def setup_game(self, game_id: str) -> dict:
        return self._call("POST", f"/api/v1/games/{game_id}/setup", payload={})

    def get_worlds(self, game_id: str) -> dict:
        return self._call("GET", f"/api/v1/games/{game_id}/worlds")

    def get_mods(self, game: str = "") -> dict:
        suffix = f"?game={game}" if game else ""
        return self._call("GET", f"/api/v1/mods{suffix}")

    def get_mod(self, mod_id: str) -> dict:
        return self._call("GET", f"/api/v1/mods/{mod_id}")

    def get_server_mods(self, server_id: str) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}/mods")

    def enable_mod(self, server_id: str, mod_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/mods/{mod_id}/enable", payload={})

    def disable_mod(self, server_id: str, mod_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/mods/{mod_id}/disable", payload={})

    def install_mod(self, server_id: str, mod_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/mods/{mod_id}/install", payload={})

    def uninstall_mod(self, server_id: str, mod_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/mods/{mod_id}/uninstall", payload={})

    def get_mod_profiles(self, game: str = "") -> dict:
        suffix = f"?game={game}" if game else ""
        return self._call("GET", f"/api/v1/mod-profiles{suffix}")

    def apply_mod_profile(self, server_id: str, profile: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/mod-profile", payload={"profile": profile})

    def get_backups(self, server_id: str) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}/backups")

    def create_backup(self, server_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/backups", payload={})

    def restore_backup(self, server_id: str, backup_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/backups/{backup_id}/restore", payload={})
