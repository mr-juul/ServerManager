from __future__ import annotations

import time

import psutil


def snapshot() -> dict[str, float | int]:
    disk_path = psutil.disk_partitions()[0].mountpoint if psutil.disk_partitions() else "/"
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage(disk_path).percent,
        "uptime_seconds": int(time.time() - psutil.boot_time()),
    }
