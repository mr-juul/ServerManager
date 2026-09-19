from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable

from .config import ServerConfig
from .logging_setup import server_log_path
from .server_process import ServerProcess, ServerStatus


class ServerManager:
    def __init__(self, configs: list[ServerConfig], log_root: Path,
                 on_change: Callable[[str, ServerStatus], None] | None = None,
                 on_output: Callable[[str, str], None] | None = None,
                 logger: logging.Logger | None = None):
        self.configs = {config.id: config for config in configs}
        self.log_root = log_root
        self.on_change = on_change
        self.on_output = on_output
        self.logger = logger or logging.getLogger("server_manager")
        self.processes: dict[str, ServerProcess] = {}
        self.crash_times: dict[str, deque[float]] = defaultdict(deque)
        self._restart_timers: dict[str, threading.Timer] = {}
        self._lock = threading.RLock()
        for config in configs:
            self.processes[config.id] = self._create_process(config)

    def _create_process(self, config: ServerConfig) -> ServerProcess:
        return ServerProcess(
            config, server_log_path(self.log_root, config.id),
            on_status=lambda status, server_id=config.id: self._status_changed(server_id, status),
            on_output=lambda line, server_id=config.id: self.on_output and self.on_output(server_id, line),
        )

    def _status_changed(self, server_id: str, status: ServerStatus) -> None:
        self.logger.info("%s status: %s", server_id, status.value)
        if self.on_change:
            self.on_change(server_id, status)
        if status == ServerStatus.CRASHED:
            self._handle_crash(server_id)

    def _handle_crash(self, server_id: str) -> None:
        config = self.configs[server_id]
        now = time.monotonic()
        crashes = self.crash_times[server_id]
        cutoff = now - config.restart_window_minutes * 60
        while crashes and crashes[0] < cutoff:
            crashes.popleft()
        crashes.append(now)
        self.logger.error("Server crashed: %s", config.name)
        if not config.auto_restart:
            return
        if len(crashes) > config.max_restarts:
            self.processes[server_id]._set_status(ServerStatus.CRASH_LOOP)
            self.logger.error("Crash loop protection engaged: %s", config.name)
            return
        delay = config.restart_delay * min(4, len(crashes))
        self.logger.warning("Automatic restart scheduled for %s in %ss", config.name, delay)
        timer = threading.Timer(delay, self._restart_after_crash, args=(server_id,))
        timer.daemon = True
        self._restart_timers[server_id] = timer
        timer.start()

    def _restart_after_crash(self, server_id: str) -> None:
        try:
            self.start(server_id, automatic=True)
        except Exception:
            self.logger.exception("Automatic restart failed for %s", server_id)

    def start(self, server_id: str, automatic: bool = False) -> None:
        process = self.processes[server_id]
        try:
            process.start()
            self.logger.info("%s started%s", process.config.name, " automatically" if automatic else "")
        except Exception:
            self.logger.exception("Error starting %s", process.config.name)
            process._set_status(ServerStatus.CRASHED)
            raise

    def stop(self, server_id: str) -> None:
        self.processes[server_id].stop()
        self.logger.info("%s stopped", self.configs[server_id].name)

    def restart(self, server_id: str) -> None:
        self.processes[server_id].restart()
        self.logger.info("%s restarted", self.configs[server_id].name)

    def start_all(self) -> None:
        for server_id in self.processes:
            try:
                self.start(server_id)
            except Exception:
                continue

    def stop_all(self) -> None:
        for server_id in self.processes:
            self.stop(server_id)

    def restart_all(self) -> None:
        for server_id in self.processes:
            try:
                self.restart(server_id)
            except Exception:
                continue

    def auto_start(self) -> None:
        for server_id, config in self.configs.items():
            if config.auto_start:
                try:
                    self.start(server_id, automatic=True)
                except Exception:
                    continue

    def auto_start_recovery(self) -> None:
        for server_id, config in self.configs.items():
            if config.start_on_manager_recovery:
                try:
                    self.start(server_id, automatic=True)
                except Exception:
                    continue

    def shutdown(self) -> None:
        for timer in self._restart_timers.values():
            timer.cancel()
        self.stop_all()
