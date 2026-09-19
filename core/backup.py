from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .config import BackupConfig


def create_backup(config: BackupConfig) -> Path:
    source = Path(config.source)
    destination = Path(config.destination)
    if not source.is_dir():
        raise FileNotFoundError(f"Backup source not found: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{source.name}-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copytree(source, target)
    cleanup_backups(config)
    return target


def cleanup_backups(config: BackupConfig) -> None:
    destination = Path(config.destination)
    if not destination.is_dir():
        return
    cutoff = datetime.now() - timedelta(days=config.retention_days)
    for entry in destination.iterdir():
        if entry.is_dir() and datetime.fromtimestamp(entry.stat().st_mtime) < cutoff:
            shutil.rmtree(entry)
