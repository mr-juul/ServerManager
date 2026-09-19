from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ServerConfig
from .mod_adapters import get_mod_adapter


@dataclass
class ModImportResult:
    mod_id: str
    game: str
    name: str
    version: str
    source: str


class ModManager:
    def __init__(self, app_root: Path, logger: logging.Logger):
        self.app_root = app_root
        self.logger = logger
        self.mods_root = app_root / "mods"
        self.library_root = self.mods_root / "library"
        self.cache_root = self.mods_root / "cache"
        self.metadata_root = self.mods_root / "metadata"
        self.catalog_path = app_root / "config" / "mods.json"
        self._state = self._load_state()

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mods": {},
            "server_mods": {},
            "profiles": [],
        }

    def _load_state(self) -> dict[str, Any]:
        self.library_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.catalog_path.is_file():
            state = self._default_state()
            self._save_state(state)
            return state
        try:
            raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = self._default_state()
        raw.setdefault("schema_version", 1)
        raw.setdefault("mods", {})
        raw.setdefault("server_mods", {})
        raw.setdefault("profiles", [])
        return raw

    def _save_state(self, state: dict[str, Any] | None = None) -> None:
        current = state or self._state
        temp = self.catalog_path.with_suffix(".tmp")
        temp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        temp.replace(self.catalog_path)

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    def list_mods(self, game: str | None = None, query: str = "") -> list[dict[str, Any]]:
        text = query.strip().lower()
        result: list[dict[str, Any]] = []
        for mod in self._state["mods"].values():
            if game and mod.get("game") != game:
                continue
            if text and text not in str(mod.get("name", "")).lower() and text not in str(mod.get("id", "")).lower():
                continue
            result.append(mod)
        return sorted(result, key=lambda item: str(item.get("name", "")).lower())

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
        return slug.strip("-") or "mod"

    def _parse_name_version(self, filename: str) -> tuple[str, str]:
        stem = Path(filename).stem
        match = re.match(r"^(?P<name>.+?)[-_ ]v?(?P<version>\d+\.\d+(?:\.\d+)?(?:[-a-zA-Z0-9.]*)?)$", stem)
        if match:
            return match.group("name"), match.group("version")
        return stem, "unknown"

    def import_local_mod(self, archive_path: Path, game: str, source: str = "local") -> ModImportResult:
        if not archive_path.is_file():
            raise FileNotFoundError(f"Mod-fil ikke fundet: {archive_path}")
        suffix = archive_path.suffix.lower()
        if suffix not in {".zip", ".dll"}:
            raise ValueError("Kun .zip og .dll understøttes i første version")

        name, version = self._parse_name_version(archive_path.name)
        adapter = get_mod_adapter(game)
        detected: dict[str, Any] = {}
        if adapter:
            detected = adapter.detect_from_archive(archive_path)
            if detected.get("name") and detected["name"] != Path(archive_path.name).stem:
                name = str(detected["name"])
            if detected.get("version") and detected["version"] != "unknown":
                version = str(detected["version"])

        mod_id = f"{game}-{self._slug(name)}"
        version_dir = self.library_root / game / self._slug(name) / version
        version_dir.mkdir(parents=True, exist_ok=True)

        if suffix == ".zip":
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(version_dir)
        else:
            shutil.copy2(archive_path, version_dir / archive_path.name)

        entry = self._state["mods"].get(mod_id, {
            "id": mod_id,
            "game": game,
            "name": name,
            "author": "",
            "source": source,
            "description": "",
            "dependencies": detected.get("dependencies", []),
            "installed_versions": [],
            "latest_version": version,
            "status": "INSTALLED",
        })
        installed_versions = set(entry.get("installed_versions", []))
        installed_versions.add(version)
        entry["installed_versions"] = sorted(installed_versions)
        entry["latest_version"] = version
        entry["status"] = "INSTALLED"
        entry["source"] = source
        self._state["mods"][mod_id] = entry
        self._save_state()
        return ModImportResult(mod_id=mod_id, game=game, name=name, version=version, source=source)

    def servers_using_mod(self, mod_id: str) -> list[str]:
        users: list[str] = []
        for server_id, items in self._state["server_mods"].items():
            if any(str(item.get("mod_id")) == mod_id and bool(item.get("enabled", False)) for item in items):
                users.append(server_id)
        return sorted(users)

    def mods_for_server(self, server_id: str) -> list[dict[str, Any]]:
        return list(self._state["server_mods"].get(server_id, []))

    def enable_mod_for_server(self, server: ServerConfig, mod_id: str, version: str | None = None) -> None:
        mod = self._state["mods"].get(mod_id)
        if not mod:
            raise ValueError("Mod ikke fundet i library")
        version = version or str(mod.get("latest_version") or "")
        if not version:
            raise ValueError("Ingen version tilgængelig")

        slug_name = self._slug(str(mod.get("name", mod_id)))
        library_version_path = self.library_root / str(mod.get("game")) / slug_name / version
        if not library_version_path.exists():
            raise FileNotFoundError("Mod-version er ikke installeret i library")

        adapter = get_mod_adapter(server.game)
        if not adapter:
            raise ValueError("Ingen mod-adapter for dette spil")
        adapter.install_to_server(server, mod_id, version, library_version_path)

        items = list(self._state["server_mods"].get(server.id, []))
        updated = False
        for item in items:
            if item.get("mod_id") == mod_id:
                item["enabled"] = True
                item["installed"] = True
                item["version"] = version
                updated = True
                break
        if not updated:
            items.append({
                "mod_id": mod_id,
                "enabled": True,
                "installed": True,
                "version": version,
                "pinned": False,
            })
        self._state["server_mods"][server.id] = items
        self._save_state()

    def disable_mod_for_server(self, server_id: str, mod_id: str) -> None:
        items = list(self._state["server_mods"].get(server_id, []))
        for item in items:
            if item.get("mod_id") == mod_id:
                item["enabled"] = False
        self._state["server_mods"][server_id] = items
        self._save_state()

    def uninstall_mod_from_server(self, server: ServerConfig, mod_id: str) -> None:
        adapter = get_mod_adapter(server.game)
        if adapter:
            adapter.uninstall_from_server(server, mod_id)
        items = [item for item in self._state["server_mods"].get(server.id, []) if item.get("mod_id") != mod_id]
        self._state["server_mods"][server.id] = items
        self._save_state()

    def create_profile(self, game: str, name: str, mod_ids: list[str]) -> None:
        if not name.strip():
            raise ValueError("Profilnavn mangler")
        profiles = [profile for profile in self._state["profiles"] if not (profile.get("game") == game and profile.get("name") == name)]
        profiles.append({"game": game, "name": name, "mod_ids": sorted(set(mod_ids))})
        self._state["profiles"] = profiles
        self._save_state()

    def list_profiles(self, game: str | None = None) -> list[dict[str, Any]]:
        profiles = self._state["profiles"]
        if game:
            profiles = [profile for profile in profiles if profile.get("game") == game]
        return sorted(profiles, key=lambda item: (str(item.get("game", "")), str(item.get("name", "")).lower()))

    def apply_profile(self, server: ServerConfig, profile_name: str) -> dict[str, list[str]]:
        profile = next((item for item in self._state["profiles"] if item.get("game") == server.game and item.get("name") == profile_name), None)
        if not profile:
            raise ValueError("Profil ikke fundet")

        target_mods = set(profile.get("mod_ids", []))
        current_items = list(self._state["server_mods"].get(server.id, []))
        current_enabled = {str(item.get("mod_id")) for item in current_items if bool(item.get("enabled", False))}

        enable = sorted(target_mods - current_enabled)
        disable = sorted(current_enabled - target_mods)

        for mod_id in enable:
            self.enable_mod_for_server(server, mod_id)
        for mod_id in disable:
            self.disable_mod_for_server(server.id, mod_id)

        return {"enable": enable, "disable": disable}
