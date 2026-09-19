from __future__ import annotations

import subprocess
from pathlib import Path

TASK_NAME = "ServerManagerWatchdog"


class StartupIntegrationError(RuntimeError):
    """Raised when Windows startup integration fails."""


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        ["schtasks.exe", *args],
        capture_output=True,
        text=True,
        check=False,
        creationflags=creation_flags,
    )


def startup_command(app_root: Path) -> str:
    watchdog = app_root / "ServerManagerWatchdog.exe"
    manager = app_root / "ServerManager.exe"
    if watchdog.exists():
        return f'"{watchdog}" --manager "{manager}" --startup-reason windows_startup'
    return f'"{manager}" --startup-reason windows_startup'


def is_enabled() -> bool:
    result = _run_schtasks(["/Query", "/TN", TASK_NAME])
    return result.returncode == 0


def enable(app_root: Path) -> None:
    command = startup_command(app_root)
    result = _run_schtasks(
        [
            "/Create",
            "/F",
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/TN",
            TASK_NAME,
            "/TR",
            command,
        ]
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise StartupIntegrationError(f"Could not enable startup task: {detail}")


def disable() -> None:
    result = _run_schtasks(["/Delete", "/F", "/TN", TASK_NAME])
    if result.returncode != 0 and "cannot find the file specified" not in (result.stderr or "").lower():
        detail = (result.stderr or result.stdout).strip()
        raise StartupIntegrationError(f"Could not disable startup task: {detail}")
