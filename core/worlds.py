from __future__ import annotations

import json
import os
from pathlib import Path


def _candidate_world_roots(app_root: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    local_low = os.environ.get("LOCALAPPDATA")
    if local_low:
        # LOCALAPPDATA points to AppData\Local; Valheim saves are under AppData\LocalLow.
        base = Path(local_low).parent / "LocalLow" / "IronGate" / "Valheim"
        roots.extend([base / "worlds_local", base / "worlds"])

    if app_root:
        roots.extend([
            app_root / "servers",
            app_root / "worlds",
            app_root / "backups",
        ])

    return [root for root in roots if root.is_dir()]


def discover_valheim_worlds(app_root: Path | None = None) -> list[str]:
    names: set[str] = set()
    for root in _candidate_world_roots(app_root):
        for pattern in ("*.fwl", "*.db", "*.db.old"):
            for path in root.rglob(pattern):
                stem = path.stem
                if stem.endswith(".db"):
                    stem = stem[:-3]
                if stem and stem not in {"steam_autocloud"}:
                    names.add(stem)
    return sorted(names, key=str.lower)


def _discover_saved_worlds(app_root: Path | None, game_id: str) -> set[str]:
    if app_root is None:
        return set()
    config_path = app_root / "config" / "servers.json"
    if not config_path.is_file():
        return set()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    worlds: set[str] = set()
    for raw in data.get("servers") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("game", "")).strip() != game_id:
            continue
        world = str(raw.get("world", "")).strip()
        if world:
            worlds.add(world)
    return worlds


def discover_world_names(game_id: str, app_root: Path | None = None) -> list[str]:
    names = _discover_saved_worlds(app_root, game_id)
    if game_id == "valheim":
        names.update(discover_valheim_worlds(app_root))
    return sorted(names, key=str.lower)
