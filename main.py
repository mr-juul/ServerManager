from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from core.config import ConfigStore, ConfigurationError, default_data_root
from core.logging_setup import configure_logging
from ui.main_window import MainWindow, apply_theme


def application_root() -> Path:
    """Use the persistent ProgramData directory for user-managed state."""
    return default_data_root()


ROOT = application_root()


def installation_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def parse_startup_reason(argv: list[str]) -> str:
    for argument in argv[1:]:
        if argument.startswith("--startup-reason="):
            reason = argument.split("=", 1)[1].strip().lower()
            if reason in {"normal", "windows_startup", "manual", "recovery"}:
                return reason
    return "normal"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Server Manager")
    apply_theme(app)
    logger = configure_logging(ROOT / "logs")
    startup_reason = parse_startup_reason(sys.argv)
    try:
        settings, servers = ConfigStore(ROOT / "config" / "servers.json").load()
    except ConfigurationError as exc:
        logger.exception("Configuration error")
        QMessageBox.critical(None, "Configuration error", str(exc))
        return 1
    window = MainWindow(settings, servers, ROOT / "logs", logger, ROOT, startup_reason, installation_root())
    window.show()
    if startup_reason == "recovery":
        window.manager.auto_start_recovery()
    elif settings.start_servers_automatically:
        window.manager.auto_start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
