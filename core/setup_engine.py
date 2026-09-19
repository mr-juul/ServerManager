from __future__ import annotations

import os
import webbrowser
from dataclasses import dataclass, field

from .game_detection import _steam_libraries
from .games import GameDefinition


@dataclass
class SetupIssue:
    label: str
    state: str
    detail: str = ""


@dataclass
class SetupResult:
    definition: GameDefinition
    ready: bool
    issues: list[SetupIssue] = field(default_factory=list)

    @property
    def fixable(self) -> bool:
        return any(issue.state != "ok" for issue in self.issues)

    def fix_message(self) -> str:
        if self.ready:
            return "Everything is ready."
        missing = [issue for issue in self.issues if issue.state != "ok"]
        if not missing:
            return "Everything is ready."
        first = missing[0]
        if first.label == "Steam":
            return "Steam is required. Install Steam and open it to log in before continuing."
        if first.label == "Java":
            return "Java is not installed. Install Java and ensure it is available on PATH."
        if first.label == "Dedicated server":
            return f"The dedicated server for {self.definition.display_name} is not installed yet. Install it from Steam and retry."
        return f"{first.label} needs attention before this game can be used."


class GameSetupEngine:
    def __init__(self, definition: GameDefinition):
        self.definition = definition

    def evaluate(self) -> SetupResult:
        issues: list[SetupIssue] = []
        libraries = _steam_libraries()
        if libraries:
            issues.append(SetupIssue("Steam", "ok", "Steam libraries detected: " + ", ".join(str(path) for path in libraries[:3])))
        else:
            issues.append(SetupIssue("Steam", "warning", "Steam not detected. Install Steam or add a library folder."))

        if self.definition.requires_java:
            import shutil
            java = shutil.which("java")
            if java:
                issues.append(SetupIssue("Java", "ok", java))
            else:
                issues.append(SetupIssue("Java", "warning", "Java is required for this game."))
        else:
            issues.append(SetupIssue("Prerequisites", "ok", "No extra dependencies required."))

        if self.definition.steam_app_id:
            if libraries:
                issues.append(SetupIssue("Dedicated server", "warning", f"Steam App ID {self.definition.steam_app_id} has not been installed yet."))
            else:
                issues.append(SetupIssue("Dedicated server", "warning", "Steam is required before dedicated server installation."))
        else:
            issues.append(SetupIssue("Dedicated server", "ok", "No dedicated server install required."))

        ready = all(issue.state == "ok" for issue in issues)
        return SetupResult(self.definition, ready, issues)

    def default_server_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": f"{self.definition.id}-server",
            "name": self.definition.display_name,
            "game": self.definition.id,
            "script": "",
            "working_directory": "",
            "world": "",
            "password": "",
            "port": 2456,
            "public": True,
            "crossplay": True,
            "additional_arguments": "",
            "world_directory": "",
            "executable_directory": "",
            "auto_start": False,
            "start_on_manager_recovery": False,
            "auto_restart": False,
            "restart_delay": 10,
            "max_restarts": 5,
            "restart_window_minutes": 15,
        }
        if self.definition.id == "valheim":
            payload.update({
                "name": "Valheim",
                "world": "ValheimWorld",
                "port": 2456,
                "public": True,
                "crossplay": True,
                "additional_arguments": "-batchmode -nographics",
            })
        return payload

    def fix(self) -> dict[str, str]:
        if self.definition.requires_java:
            return {"action": "java_download", "target": "https://www.oracle.com/java/technologies/downloads/"}
        if self.definition.steam_app_id:
            return {"action": "steam_install", "target": f"steam://install/{self.definition.steam_app_id}"}
        return {"action": "manual", "target": ""}

    def run_fix(self) -> bool:
        fix = self.fix()
        action = fix.get("action", "manual")
        target = fix.get("target", "")
        if action == "java_download":
            webbrowser.open(target)
            return True
        if action == "steam_install":
            if os.name == "nt":
                try:
                    os.startfile(target)
                    return True
                except OSError:
                    pass
            webbrowser.open(target)
            return True
        return False
