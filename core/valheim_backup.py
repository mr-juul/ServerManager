from __future__ import annotations

import tarfile
from datetime import datetime, timedelta
from pathlib import Path

import zstandard


def create_valheim_backup(world_directory: str, server_id: str, retention_days: int = 30) -> Path:
    source = Path(world_directory)
    if not source.is_dir():
        raise FileNotFoundError(f"World-mappe ikke fundet: {source}")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = source / f"{server_id}-world-{timestamp}.tar.zst"
    temporary = source.parent / f".{target.name}.tmp"
    compressor = zstandard.ZstdCompressor(level=3)
    try:
        with temporary.open("wb") as compressed:
            with compressor.stream_writer(compressed) as writer:
                with tarfile.open(fileobj=writer, mode="w|", dereference=False) as archive:
                    archive.add(source, arcname=source.name, recursive=True)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    cutoff = datetime.now() - timedelta(days=retention_days)
    for archive in source.glob(f"{server_id}-world-*.tar.zst"):
        if datetime.fromtimestamp(archive.stat().st_mtime) < cutoff:
            archive.unlink()
    return target
