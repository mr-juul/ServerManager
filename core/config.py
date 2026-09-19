from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def default_data_root() -> Path:
    """Return the persistent data directory for the standalone app."""
    program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(program_data).expanduser() / "ServerManager"


class ConfigurationError(ValueError):
    """Raised when the JSON configuration cannot be used safely."""


@dataclass
class BackupConfig:
    enabled: bool = False
    source: str = ""
    destination: str = ""
    retention_days: int = 30


@dataclass
class ServerConfig:
    id: str
    name: str
    script: str
    working_directory: str
    game: str = "generic"
    world: str = ""
    password: str = ""
    port: int = 2456
    public: bool = True
    crossplay: bool = True
    additional_arguments: str = ""
    world_directory: str = ""
    executable_directory: str = ""
    auto_start: bool = False
    auto_restart: bool = False
    restart_delay: int = 10
    max_restarts: int = 5
    restart_window_minutes: int = 15
    backup: BackupConfig = field(default_factory=BackupConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ServerConfig":
        required = ("id", "name", "script", "working_directory")
        missing = [key for key in required if not str(raw.get(key, "")).strip()]
        if missing:
            raise ConfigurationError(f"Server is missing: {', '.join(missing)}")
        if not str(raw["script"]).lower().endswith(".bat"):
            raise ConfigurationError(f"{raw['id']}: script must be a .bat file")
        backup_raw = raw.get("backup") or {}
        try:
            return cls(
                id=str(raw["id"]), name=str(raw["name"]), script=str(raw["script"]),
                working_directory=str(raw["working_directory"]),
                game=str(raw.get("game", "valheim" if "world" in raw else "generic")),
                world=str(raw.get("world", "")),
                password=str(raw.get("password", "")),
                port=max(1, int(raw.get("port", 2456))),
                public=bool(raw.get("public", True)),
                crossplay=bool(raw.get("crossplay", True)),
                additional_arguments=str(raw.get("additional_arguments", "")),
                world_directory=str(raw.get("world_directory", backup_raw.get("source", ""))),
                executable_directory=str(raw.get("executable_directory", "")),
                auto_start=bool(raw.get("auto_start", False)),
                auto_restart=bool(raw.get("auto_restart", False)),
                restart_delay=max(1, int(raw.get("restart_delay", 10))),
                max_restarts=max(1, int(raw.get("max_restarts", 5))),
                restart_window_minutes=max(1, int(raw.get("restart_window_minutes", 15))),
                backup=BackupConfig(
                    enabled=bool(backup_raw.get("enabled", False)),
                    source=str(backup_raw.get("source", "")),
                    destination=str(backup_raw.get("destination", "")),
                    retention_days=max(1, int(backup_raw.get("retention_days", 30))),
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"{raw.get('id', 'unknown')}: invalid numeric setting") from exc

    def to_dict(self) -> dict[str, Any]:
        result = self.__dict__.copy()
        result["backup"] = self.backup.__dict__.copy()
        return result


@dataclass
class AppSettings:
    start_with_windows: bool = False
    minimize_to_tray: bool = True
    start_servers_automatically: bool = True
    log_retention_days: int = 30
    refresh_interval_seconds: int = 2


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path

    def _legacy_locations(self) -> list[Path]:
        default_path = (default_data_root() / "config" / "servers.json").resolve(strict=False)
        package_path = (Path(__file__).resolve().parents[1] / "config" / "servers.json").resolve(strict=False)
        if self.path.resolve(strict=False) != default_path:
            return []
        if not package_path.exists():
            return []
        return [package_path]

    def _ensure_default_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return

        for legacy_path in self._legacy_locations():
            backup_path = self.path.with_name(f"servers_backup_{int(__import__('time').time())}.json")
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
            self.path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
            return

        payload = {"schema_version": 2, "settings": AppSettings().__dict__, "servers": []}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self) -> tuple[AppSettings, list[ServerConfig]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._ensure_default_file()
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ConfigurationError(f"Configuration not found: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON at {self.path}: {exc.msg}") from exc

        if not isinstance(raw, dict) or not isinstance(raw.get("servers", []), list):
            raise ConfigurationError("Configuration must contain a servers array")
        settings_raw = raw.get("settings") or {}
        settings = AppSettings(
            start_with_windows=bool(settings_raw.get("start_with_windows", False)),
            minimize_to_tray=bool(settings_raw.get("minimize_to_tray", True)),
            start_servers_automatically=bool(settings_raw.get("start_servers_automatically", True)),
            log_retention_days=max(1, int(settings_raw.get("log_retention_days", 30))),
            refresh_interval_seconds=max(1, int(settings_raw.get("refresh_interval_seconds", 2))),
        )
        servers = [ServerConfig.from_dict(item) for item in raw["servers"]]
        ids = [server.id for server in servers]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("Server ids must be unique")
        return settings, servers

    def save(self, settings: AppSettings, servers: list[ServerConfig]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 2, "settings": settings.__dict__, "servers": [server.to_dict() for server in servers]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

