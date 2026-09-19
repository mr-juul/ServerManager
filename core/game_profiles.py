from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "valheim": {
        "port": 2456,
        "public": True,
        "crossplay": True,
        "additional_arguments": "-batchmode -nographics",
        "installation_directory": "",
    }
}


class GameProfileStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, dict[str, Any]]:
        profiles = {key: value.copy() for key, value in DEFAULT_PROFILES.items()}
        if not self.path.is_file():
            return profiles
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return profiles
        for game_id, values in (raw.get("profiles") or {}).items():
            if isinstance(values, dict):
                profiles.setdefault(game_id, {}).update(values)
        return profiles

    def save(self, profiles: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if self.path.is_file():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing["profiles"] = profiles
        self.path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
