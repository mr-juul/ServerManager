from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .config import ServerConfig


class ModAdapter(ABC):
    game_id: str
    supports_load_order: bool = False
    mod_loader: str | None = None

    @abstractmethod
    def detect_from_archive(self, archive_path: Path) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def install_to_server(self, server: ServerConfig, mod_id: str, version: str, library_version_path: Path) -> Path:
        raise NotImplementedError

    @abstractmethod
    def uninstall_from_server(self, server: ServerConfig, mod_id: str) -> None:
        raise NotImplementedError


class BaseFilesystemAdapter(ModAdapter):
    """Generic adapter that keeps mod copies under servermanager_mods in working directory."""

    game_id = "generic"

    def detect_from_archive(self, archive_path: Path) -> dict[str, Any]:
        stem = archive_path.stem
        return {
            "name": stem,
            "version": "unknown",
            "dependencies": [],
            "loader": None,
        }

    def install_to_server(self, server: ServerConfig, mod_id: str, version: str, library_version_path: Path) -> Path:
        target_root = Path(server.working_directory) / "servermanager_mods" / mod_id / version
        if target_root.exists():
            shutil.rmtree(target_root)
        target_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(library_version_path, target_root)
        return target_root

    def uninstall_from_server(self, server: ServerConfig, mod_id: str) -> None:
        target_root = Path(server.working_directory) / "servermanager_mods" / mod_id
        if target_root.exists():
            shutil.rmtree(target_root)


class ValheimModAdapter(BaseFilesystemAdapter):
    game_id = "valheim"
    mod_loader = "BepInEx"

    def detect_from_archive(self, archive_path: Path) -> dict[str, Any]:
        base = super().detect_from_archive(archive_path)
        lower = archive_path.stem.lower()
        if "bepinex" in lower:
            base["loader"] = "BepInEx"
        return base


ADAPTERS: dict[str, ModAdapter] = {
    "valheim": ValheimModAdapter(),
    "generic": BaseFilesystemAdapter(),
}


def get_mod_adapter(game_id: str) -> ModAdapter | None:
    return ADAPTERS.get(game_id) or ADAPTERS.get("generic")
