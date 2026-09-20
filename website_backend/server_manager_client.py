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

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }
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
                payload = json.loads(exc.read().decode("utf-8"))
                raise ServerManagerClientError(
                    error=str(payload.get("error", "server_manager_error")),
                    message=str(payload.get("message", "Request failed")),
                    status_code=exc.code,
                ) from exc
            except Exception:
                raise ServerManagerClientError("server_manager_error", f"HTTP {exc.code} from Server Manager", exc.code) from exc
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

    def get_servers(self) -> dict:
        return self._call("GET", "/api/v1/servers")

    def get_server(self, server_id: str) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}")

    def start_server(self, server_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/start", payload={})

    def stop_server(self, server_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/stop", payload={})

    def restart_server(self, server_id: str) -> dict:
        return self._call("POST", f"/api/v1/servers/{server_id}/restart", payload={})

    def get_logs(self, server_id: str, cursor: int = 0, limit: int = 200) -> dict:
        return self._call("GET", f"/api/v1/servers/{server_id}/logs?cursor={int(cursor)}&limit={int(limit)}")
