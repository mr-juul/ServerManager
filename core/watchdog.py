from __future__ import annotations

import argparse
import logging
import os
import subprocess
import time
from collections import deque
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Server Manager watchdog")
    parser.add_argument("--manager", required=True, help="Path to ServerManager executable")
    parser.add_argument("--startup-reason", default="manual", choices=["manual", "windows_startup"])
    parser.add_argument("--max-restarts", type=int, default=5)
    parser.add_argument("--window-minutes", type=int, default=5)
    parser.add_argument("--restart-delay-seconds", type=int, default=5)
    parser.add_argument("--cooldown-seconds", type=int, default=300)
    parser.add_argument("--once", action="store_true", help="Run manager once and exit")
    return parser


def _watchdog_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("server_manager_watchdog")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def run_watchdog(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    manager = Path(args.manager)
    if not manager.exists():
        raise FileNotFoundError(f"Manager executable not found: {manager}")

    # Keep logs in ProgramData when available; fallback to manager folder for portable runs.
    program_data = Path(os.environ.get("PROGRAMDATA") or "C:/ProgramData")
    root = program_data / "ServerManager"
    if not root.exists():
        root = manager.parent
    logger = _watchdog_logger(root / "logs" / "watchdog.log")
    logger.info("Watchdog startup")

    crash_times: deque[float] = deque()

    while True:
        reason = "recovery" if crash_times else args.startup_reason
        logger.info("Starting ServerManager (%s)", reason)
        process = subprocess.Popen([str(manager), f"--startup-reason={reason}"])
        exit_code = process.wait()
        logger.info("ServerManager exited with code %s", exit_code)

        if args.once:
            logger.info("Watchdog configured for one run; exiting")
            return exit_code

        now = time.monotonic()
        cutoff = now - max(1, args.window_minutes) * 60
        while crash_times and crash_times[0] < cutoff:
            crash_times.popleft()

        if exit_code == 0:
            crash_times.clear()
            return 0

        crash_times.append(now)
        logger.warning("Crash count in window: %s", len(crash_times))
        if len(crash_times) >= max(1, args.max_restarts):
            logger.error("Server Manager appears to be crashing repeatedly. Cooldown started.")
            time.sleep(max(1, args.cooldown_seconds))
            crash_times.clear()
            continue

        logger.info("Restarting in %ss", max(1, args.restart_delay_seconds))
        time.sleep(max(1, args.restart_delay_seconds))
