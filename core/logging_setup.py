from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def configure_logging(log_root: Path) -> logging.Logger:
    log_root.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("server_manager")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(log_root / "application.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def server_log_path(log_root: Path, server_id: str) -> Path:
    directory = log_root / server_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{datetime.now():%Y-%m-%d}.log"
