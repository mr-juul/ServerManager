from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 3


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
    start_on_manager_recovery: bool = False
    auto_restart: bool = False
    restart_delay: int = 10
    max_restarts: int = 5
    restart_window_minutes: int = 15
    backup: BackupConfig = field(default_factory=BackupConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ServerConfig":
        required = ("id", "script", "working_directory")
        missing = [key for key in required if not str(raw.get(key, "")).strip()]
        if missing:
            raise ConfigurationError(f"Server is missing: {', '.join(missing)}")
        display_name = str(raw.get("name") or raw.get("display_name") or "").strip()
        if not display_name:
            raise ConfigurationError(f"{raw.get('id', 'unknown')}: missing name")
        if not str(raw["script"]).lower().endswith(".bat"):
            raise ConfigurationError(f"{raw['id']}: script must be a .bat file")
        backup_raw = raw.get("backup") or {}
        try:
            return cls(
                id=str(raw["id"]), name=display_name, script=str(raw["script"]),
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
                start_on_manager_recovery=bool(raw.get("start_on_manager_recovery", raw.get("auto_start", False))),
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
    automatic_update_checks: bool = True
    update_channel: str = "stable"
    update_check_frequency: str = "startup"
    watchdog_max_restarts: int = 5
    watchdog_window_minutes: int = 5
    watchdog_restart_delay_seconds: int = 5


def ensure_runtime_layout(root: Path) -> None:
    required = [
        root / "config",
        root / "servers",
        root / "backups",
        root / "logs",
        root / "logs" / "server-manager",
        root / "logs" / "servers",
        root / "cache",
        root / "updates",
        root / "updates" / "downloads",
        root / "updates" / "backups",
    ]
    for directory in required:
        directory.mkdir(parents=True, exist_ok=True)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self.root = path.parent.parent if path.parent.name == "config" else path.parent

    @property
    def servers_path(self) -> Path:
        return self.root / "config" / "servers.json"

    @property
    def settings_path(self) -> Path:
        return self.root / "config" / "settings.json"

    @property
    def games_path(self) -> Path:
        return self.root / "config" / "games.json"

    def _legacy_locations(self) -> list[Path]:
        default_path = (default_data_root() / "config" / "servers.json").resolve(strict=False)
        package_path = (Path(__file__).resolve().parents[1] / "config" / "servers.json").resolve(strict=False)
        if self.servers_path.resolve(strict=False) != default_path:
            return []
        if not package_path.exists():
            return []
        return [package_path]

    def _atomic_write_json(self, target: Path, payload: dict[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp.replace(target)

    def _backup_file(self, source: Path) -> Path:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = self.root / "updates" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{source.stem}-{timestamp}{source.suffix}"
        shutil.copy2(source, backup)
        return backup

    def _create_default_servers(self) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "servers": []}
        self._atomic_write_json(self.servers_path, payload)

    def _create_default_settings(self) -> None:
        payload = {"schema_version": SCHEMA_VERSION, **AppSettings().__dict__}
        self._atomic_write_json(self.settings_path, payload)

    def _ensure_games_file(self) -> None:
        if self.games_path.exists():
            return
        package_games = (Path(__file__).resolve().parents[1] / "config" / "games.json").resolve(strict=False)
        if package_games.exists():
            self.games_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package_games, self.games_path)
            return
        self._atomic_write_json(self.games_path, {"schema_version": SCHEMA_VERSION, "profiles": {}})

    def _migrate_legacy_servers_format(self) -> None:
        if not self.servers_path.exists():
            return
        try:
            raw = json.loads(self.servers_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON at {self.servers_path}: {exc.msg}") from exc

        if not isinstance(raw, dict):
            raise ConfigurationError("servers.json must be a JSON object")

        settings = raw.get("settings")
        changed = False
        if isinstance(settings, dict) and not self.settings_path.exists():
            self._backup_file(self.servers_path)
            settings_payload = {"schema_version": SCHEMA_VERSION, **self._normalize_settings(settings).__dict__}
            self._atomic_write_json(self.settings_path, settings_payload)
            changed = True

        schema = int(raw.get("schema_version", 1))
        if "servers" not in raw:
            raise ConfigurationError("Configuration must contain a servers array")
        if schema < SCHEMA_VERSION or "settings" in raw:
            self._backup_file(self.servers_path)
            migrated = {"schema_version": SCHEMA_VERSION, "servers": raw.get("servers") or []}
            self._atomic_write_json(self.servers_path, migrated)
            changed = True

        if changed:
            return

    def _initialize_defaults(self) -> None:
        ensure_runtime_layout(self.root)

        if not self.servers_path.exists():
            for legacy_path in self._legacy_locations():
                self.servers_path.parent.mkdir(parents=True, exist_ok=True)
                self.servers_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
                self._backup_file(self.servers_path)
                break
            if not self.servers_path.exists():
                self._create_default_servers()

        self._migrate_legacy_servers_format()

        if not self.settings_path.exists():
            self._create_default_settings()

        self._ensure_games_file()

    def _normalize_settings(self, settings_raw: dict[str, Any]) -> AppSettings:
        return AppSettings(
            start_with_windows=bool(settings_raw.get("start_with_windows", False)),
            minimize_to_tray=bool(settings_raw.get("minimize_to_tray", True)),
            start_servers_automatically=bool(settings_raw.get("start_servers_automatically", True)),
            log_retention_days=max(1, int(settings_raw.get("log_retention_days", 30))),
            refresh_interval_seconds=max(1, int(settings_raw.get("refresh_interval_seconds", 2))),
            automatic_update_checks=bool(settings_raw.get("automatic_update_checks", True)),
            update_channel=str(settings_raw.get("update_channel", "stable")),
            update_check_frequency=str(settings_raw.get("update_check_frequency", "startup")),
            watchdog_max_restarts=max(1, int(settings_raw.get("watchdog_max_restarts", 5))),
            watchdog_window_minutes=max(1, int(settings_raw.get("watchdog_window_minutes", 5))),
            watchdog_restart_delay_seconds=max(1, int(settings_raw.get("watchdog_restart_delay_seconds", 5))),
        )

    def load(self) -> tuple[AppSettings, list[ServerConfig]]:
        self._initialize_defaults()
        try:
            servers_raw = json.loads(self.servers_path.read_text(encoding="utf-8"))
            settings_raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Configuration not found: {exc.filename}") from exc
        except json.JSONDecodeError as exc:
            invalid_path = self.servers_path if self.servers_path.exists() else self.settings_path
            raise ConfigurationError(f"Invalid JSON at {invalid_path}: {exc.msg}") from exc

        if not isinstance(servers_raw, dict) or not isinstance(servers_raw.get("servers", []), list):
            raise ConfigurationError("Configuration must contain a servers array")

        settings = self._normalize_settings(settings_raw)
        servers = [ServerConfig.from_dict(item) for item in servers_raw["servers"]]
        ids = [server.id for server in servers]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("Server ids must be unique")
        return settings, servers

    def save(self, settings: AppSettings, servers: list[ServerConfig]) -> None:
        self._initialize_defaults()
        self._atomic_write_json(self.settings_path, {"schema_version": SCHEMA_VERSION, **settings.__dict__})
        self._atomic_write_json(self.servers_path, {"schema_version": SCHEMA_VERSION, "servers": [server.to_dict() for server in servers]})

