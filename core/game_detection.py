from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .games import GameDefinition


@dataclass
class CheckResult:
    label: str
    state: str
    detail: str = ""


@dataclass
class GameStatus:
    definition: GameDefinition
    state: str
    checks: list[CheckResult] = field(default_factory=list)
    installation_path: Path | None = None
    checked_at: datetime = field(default_factory=datetime.now)


def _steam_roots() -> list[Path]:
    roots = [Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Steam", Path(os.environ.get("PROGRAMFILES", "")) / "Steam"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Steam")
    return list(dict.fromkeys(path for path in roots if str(path) and path.is_dir()))


def _steam_libraries() -> list[Path]:
    libraries: list[Path] = []
    for root in _steam_roots():
        if root.is_dir():
            libraries.append(root)
        manifest = root / "steamapps" / "libraryfolders.vdf"
        if manifest.is_file():
            text = manifest.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if '"path"' not in line:
                    continue
                import re
                match = re.search(r'"path"\s+"([^"]+)"', line)
                if not match:
                    continue
                value = match.group(1).replace('\\\\', '\\')
                path = Path(value)
                if path.is_dir():
                    libraries.append(path)
    return list(dict.fromkeys(libraries))


def detect_game(definition: GameDefinition, configured_paths: list[Path] | None = None) -> GameStatus:
    checks: list[CheckResult] = [CheckResult("Support", "ok" if definition.supported else "error", "Ready" if definition.supported else "Unavailable")]
    candidates = list(configured_paths or [])
    if definition.requires_java:
        java = shutil.which("java")
        checks.append(CheckResult("Java", "ok" if java else "warning", "Compatible runtime detected" if java else "No compatible runtime"))
    if definition.steam_app_id:
        steam = _steam_libraries()
        checks.append(CheckResult("Steam", "ok" if steam else "warning", "Available" if steam else "Not available"))
        for library in steam:
            candidates.extend([library / "steamapps" / "common", library / "steamapps" / "common" / definition.display_name])
    found = None
    for candidate in candidates:
        if not candidate.is_dir(): continue
        if not definition.server_search_names or any((candidate / name).exists() for name in definition.server_search_names):
            found = candidate; break
    if definition.server_search_names:
        checks.append(CheckResult("Dedicated server", "ok" if found else "warning", "Installed" if found else "Needs setup"))
    state = "READY" if all(check.state == "ok" for check in checks if check.label != "Support") else "WARNING"
    if not definition.supported: state = "ERROR"
    return GameStatus(definition, state, checks, found)
