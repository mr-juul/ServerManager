from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Callable

import psutil

from .config import ServerConfig


class ServerStatus(str, Enum):
    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    ONLINE = "ONLINE"
    STOPPING = "STOPPING"
    CRASHED = "CRASHED"
    CRASH_LOOP = "CRASH LOOP"


class ServerProcess:
    def __init__(self, config: ServerConfig, log_path: Path,
                 on_status: Callable[[ServerStatus], None] | None = None,
                 on_output: Callable[[str], None] | None = None):
        self.config = config
        self.log_path = log_path
        self.on_status = on_status
        self.on_output = on_output
        self.status = ServerStatus.OFFLINE
        self.process: subprocess.Popen[str] | None = None
        self.started_at: float | None = None
        self._intentional_stop = False
        self._lock = threading.RLock()
        self._output = deque(maxlen=2000)
        self._tracked_processes: list[psutil.Process] = []
        self._monitor_thread: threading.Thread | None = None
        self._attached_root_pid: int | None = None

    @property
    def pid(self) -> int | None:
        for process in self._live_processes():
            return process.pid
        return None

    @property
    def uptime_seconds(self) -> int:
        return max(0, int(time.monotonic() - self.started_at)) if self.started_at else 0

    @property
    def recent_output(self) -> list[str]:
        return list(self._output)

    def _set_status(self, status: ServerStatus) -> None:
        with self._lock:
            self.status = status
        if self.on_status:
            self.on_status(status)

    def _emit_output(self, line: str) -> None:
        line = line.rstrip("\r\n")
        if not line:
            return
        self._output.append(line)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", errors="replace") as log_file:
            log_file.write(line + "\n")
        if self.on_output:
            self.on_output(line)

    def start(self) -> None:
        with self._lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError(f"{self.config.name} is already running")
            if self._attached_root_pid and self._is_attached_process_alive():
                raise RuntimeError(f"{self.config.name} is already running")
            script = Path(self.config.script)
            working_directory = Path(self.config.working_directory)
            if not script.is_file():
                raise FileNotFoundError(f"Script not found: {script}")
            if not working_directory.is_dir():
                raise FileNotFoundError(f"Working directory not found: {working_directory}")
            self._intentional_stop = False
            self._set_status(ServerStatus.STARTING)
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creation_flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.process = subprocess.Popen(
                ["cmd.exe", "/d", "/c", str(script)], cwd=str(working_directory),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", creationflags=creation_flags,
            )
            self.started_at = time.monotonic()
            self._attached_root_pid = None
            self._tracked_processes = []
            self._set_status(ServerStatus.ONLINE)
            threading.Thread(target=self._read_output, daemon=True, name=f"log-{self.config.id}").start()
            self._monitor_thread = threading.Thread(target=self._monitor, daemon=True, name=f"monitor-{self.config.id}")
            self._monitor_thread.start()

    def attach_existing(self, root_pid: int) -> None:
        with self._lock:
            root = psutil.Process(root_pid)
            if not root.is_running() or root.status() == psutil.STATUS_ZOMBIE:
                raise RuntimeError(f"Process {root_pid} is not running")
            self.process = None
            self._attached_root_pid = root_pid
            self._intentional_stop = False
            self.started_at = time.monotonic()
            self._tracked_processes = []
            self._remember_descendants()
            self._set_status(ServerStatus.ONLINE)
            self._monitor_thread = threading.Thread(target=self._monitor, daemon=True, name=f"monitor-{self.config.id}")
            self._monitor_thread.start()

    def _read_output(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            self._emit_output(line)

    def _monitor(self) -> None:
        process = self.process
        if process:
            while process.poll() is None:
                self._remember_descendants()
                time.sleep(0.25)
            return_code = process.returncode
        elif self._attached_root_pid:
            return_code = 0
            while self._is_attached_process_alive():
                self._remember_descendants()
                time.sleep(0.25)
        else:
            return
        self._remember_descendants()
        while self._live_processes():
            self._remember_descendants()
            time.sleep(0.25)
        if self._intentional_stop:
            self._set_status(ServerStatus.OFFLINE)
        elif return_code == 0:
            self._set_status(ServerStatus.OFFLINE)
        else:
            self._set_status(ServerStatus.CRASHED)

    def _remember_descendants(self) -> None:
        for process in self._tree():
            if process.pid not in {tracked.pid for tracked in self._tracked_processes}:
                self._tracked_processes.append(process)

    def _live_processes(self) -> list[psutil.Process]:
        live = []
        for process in self._tracked_processes:
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    live.append(process)
            except psutil.Error:
                continue
        return live

    def _tree(self) -> list[psutil.Process]:
        try:
            root_pid = self.process.pid if self.process else self._attached_root_pid
            if not root_pid:
                return []
            root = psutil.Process(root_pid)
            children = root.children(recursive=True)
            return ([root] if root.is_running() else []) + children
        except psutil.Error:
            return []

    def _is_attached_process_alive(self) -> bool:
        if not self._attached_root_pid:
            return False
        try:
            root = psutil.Process(self._attached_root_pid)
            return root.is_running() and root.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False

    def find_running_root_pid(self) -> int | None:
        script = str(Path(self.config.script).resolve(strict=False)).lower()
        working_directory = str(Path(self.config.working_directory).resolve(strict=False)).lower()
        for process in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                cmdline = [part.lower() for part in (process.info.get("cmdline") or [])]
                if not cmdline:
                    continue
                if not any(script in part for part in cmdline):
                    continue
                cwd = str(process.info.get("cwd") or "").lower()
                if working_directory and cwd and cwd != working_directory:
                    continue
                return int(process.info["pid"])
            except (psutil.Error, OSError, TypeError, ValueError):
                continue
        return None

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            attached_alive = self._is_attached_process_alive()
            if (not self.process or self.process.poll() is not None) and not attached_alive:
                self._set_status(ServerStatus.OFFLINE)
                return
            self._intentional_stop = True
            self._set_status(ServerStatus.STOPPING)
            self._remember_descendants()
            processes = list({process.pid: process for process in [*self._tree(), *self._tracked_processes]}.values())
            for process in reversed(processes):
                try:
                    process.terminate()
                except psutil.Error:
                    pass
        _, alive = psutil.wait_procs(processes, timeout=timeout)
        for process in alive:
            try:
                process.kill()
            except psutil.Error:
                pass
        with self._lock:
            self._attached_root_pid = None
            self._set_status(ServerStatus.OFFLINE)

    def restart(self) -> None:
        self.stop()
        self.start()
