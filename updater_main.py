from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import psutil

from core.update_service import apply_update_archive, validate_release_zip


def wait_for_pid(pid: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            process = psutil.Process(pid)
            if not process.is_running():
                return
        except psutil.Error:
            return
        time.sleep(0.5)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Server Manager updater")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--pid", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    archive = Path(args.archive)
    app_root = Path(args.app_root)

    wait_for_pid(args.pid)
    validate_release_zip(archive)
    apply_update_archive(archive, app_root, app_root / "updates" / "backups")

    manager = app_root / "ServerManager.exe"
    if manager.exists():
        subprocess.Popen([str(manager), "--startup-reason=manual"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
