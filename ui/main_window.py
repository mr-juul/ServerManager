from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox, QStackedWidget,
    QStyle, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from core.backup import create_backup
from core.config import AppSettings, ConfigStore, ServerConfig
from core.game_detection import GameStatus, detect_game
from core.game_profiles import GameProfileStore
from core.games import GameDefinition, game_choices, game_definition
from core.manager import ServerManager
from core.managed_server import generate_managed_server, resolve_server_executable
from core.mod_manager import ModManager
from core.valheim_backup import create_valheim_backup
from core.server_process import ServerStatus
from core.setup_engine import GameSetupEngine
from core.startup_windows import StartupIntegrationError, disable as disable_startup, enable as enable_startup, is_enabled as startup_enabled
from core.system_monitor import snapshot
from core.update_service import UpdateError, download_release_asset, fetch_latest_release, is_newer, launch_updater, validate_release_zip
from core.version import APP_VERSION, DEFAULT_RELEASE_REPO
from core.web_control import WebControlService
from core.worlds import discover_world_names


class EventBridge(QObject):
    status = Signal(str, str)
    output = Signal(str, str)
    update_available = Signal(object)
    update_none = Signal()
    update_error = Signal(str)


class DetectionWorker(QObject):
    finished = Signal(object)
    def __init__(self, definitions, servers):
        super().__init__(); self.definitions = definitions; self.servers = servers
    def run(self):
        result = {}
        for definition in self.definitions:
            paths = [Path(server.working_directory) for server in self.servers if server.game == definition.id]
            try: result[definition.id] = detect_game(definition, paths)
            except Exception as exc: result[definition.id] = GameStatus(definition, "ERROR", detail_checks(exc))
        self.finished.emit(result)


def detail_checks(exc):
    from core.game_detection import CheckResult
    return [CheckResult("Detection", "error", str(exc))]


class ServerDialog(QDialog):
    def __init__(self, server: ServerConfig | None = None, app_root: Path | None = None, game_profiles=None, parent=None):
        super().__init__(parent); self.server_id = server.id if server else None; self.setWindowTitle("Rediger server" if server else "Ny server"); self.resize(600, 540)
        self.app_root = app_root or Path.cwd()
        self.game_profiles = game_profiles or {}
        valheim_profile = self.game_profiles.get("valheim", {})
        self.game = QComboBox(); [self.game.addItem(f"{item.icon}  {item.display_name}", item.id) for item in game_choices()]
        self.name = QLineEdit(server.name if server else ""); self.world = QLineEdit(server.world if server else ""); self.password = QLineEdit(server.password if server else ""); self.password.setEchoMode(QLineEdit.Password)
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(server.port if server else int(valheim_profile.get("port", 2456)))
        self.public = QCheckBox(); self.public.setChecked(server.public if server else bool(valheim_profile.get("public", True))); self.crossplay = QCheckBox(); self.crossplay.setChecked(server.crossplay if server else bool(valheim_profile.get("crossplay", True)))
        self.script = QLineEdit(server.script if server else ""); self.directory = QLineEdit(server.working_directory if server else ""); self.arguments = QLineEdit(server.additional_arguments if server else str(valheim_profile.get("additional_arguments", "")))
        self.world_directory = QLineEdit(server.world_directory if server else "")
        self.managed = QCheckBox("Opret start-script og arbejdsmappe automatisk"); self.managed.setChecked(False)
        self.executable = QLineEdit(server.executable_directory if server else str(valheim_profile.get("installation_directory", ""))); executable_browse = QPushButton("Gennemse")
        executable_browse.clicked.connect(self._browse_installation)
        self.auto_start = QCheckBox("Start automatisk med Server Manager"); self.auto_start.setChecked(server.auto_start if server else False); self.start_on_recovery = QCheckBox("Start efter Manager recovery"); self.start_on_recovery.setChecked(server.start_on_manager_recovery if server else False); self.auto_restart = QCheckBox("Genstart automatisk ved crash"); self.auto_restart.setChecked(server.auto_restart if server else False)
        self.delay = QSpinBox(); self.delay.setRange(1, 3600); self.delay.setValue(server.restart_delay if server else 10)
        if server: self.game.setCurrentIndex(max(0, self.game.findData(server.game)))
        self.game.currentIndexChanged.connect(self._update_fields); self.script.editingFinished.connect(self._populate_directory)
        form = QFormLayout(self); form.addRow("Spil", self.game); form.addRow("Servernavn", self.name); form.addRow("World", self.world); form.addRow("Password", self.password); form.addRow("Port", self.port); form.addRow("Public", self.public); form.addRow("Crossplay", self.crossplay)
        form.addRow("Ekstra argumenter", self.arguments)
        form.addRow(self.managed)
        executable_row = QHBoxLayout(); executable_row.addWidget(self.executable); executable_row.addWidget(executable_browse); form.addRow("Server-installation", executable_row)
        script_row = QHBoxLayout(); script_row.addWidget(self.script); browse = QPushButton("Gennemse"); browse.clicked.connect(self._browse_script); script_row.addWidget(browse); form.addRow("Start-script", script_row)
        dir_row = QHBoxLayout(); dir_row.addWidget(self.directory); browse_dir = QPushButton("Gennemse"); browse_dir.clicked.connect(self._browse_directory); dir_row.addWidget(browse_dir); form.addRow("Arbejdsmappe", dir_row)
        world_dir_row = QHBoxLayout(); world_dir_row.addWidget(self.world_directory); browse_world = QPushButton("Gennemse"); browse_world.clicked.connect(self._browse_world_directory); world_dir_row.addWidget(browse_world); form.addRow("World-mappe", world_dir_row)
        group = QGroupBox("AUTOMATIK"); auto = QFormLayout(group); auto.addRow(self.auto_start); auto.addRow(self.start_on_recovery); auto.addRow(self.auto_restart); auto.addRow("Restart delay (sekunder)", self.delay); form.addRow(group)
        self.managed.toggled.connect(self._update_script_mode)
        self._update_fields()
        buttons = QHBoxLayout(); buttons.addStretch(); cancel = QPushButton("ANNULLER"); cancel.clicked.connect(self.reject); buttons.addWidget(cancel)
        if server: remove = QPushButton("FJERN SERVER"); remove.clicked.connect(lambda: self.done(2)); buttons.addWidget(remove)
        save = QPushButton("GEM"); save.clicked.connect(self._validate); buttons.addWidget(save); form.addRow(buttons); self._update_fields(); self._update_script_mode(self.managed.isChecked())

    def _update_fields(self):
        valheim = game_definition(self.game.currentData()).id == "valheim"
        for widget in (self.world, self.password, self.port, self.public, self.crossplay, self.world_directory): widget.setVisible(valheim)
        for widget in (self.world, self.password, self.port, self.public, self.crossplay, self.world_directory):
            label = self.layout().labelForField(widget)
            if label: label.setVisible(valheim)

    def _browse_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "Vælg start-script", "", "Batch files (*.bat)")
        if path: self.script.setText(path); self._populate_directory()
    def _browse_installation(self):
        path = QFileDialog.getExistingDirectory(self, "Vælg serverens installationsmappe", self.executable.text())
        if path: self.executable.setText(path)
    def _update_script_mode(self, managed):
        self.script.setEnabled(not managed); self.directory.setEnabled(not managed)
        self.executable.setEnabled(managed)
    def _browse_directory(self):
        path = QFileDialog.getExistingDirectory(self, "Vælg arbejdsmappe", self.directory.text())
        if path: self.directory.setText(path)
    def _browse_world_directory(self):
        path = QFileDialog.getExistingDirectory(self, "Vælg mappe hvor Valheim-worlds gemmes", self.world_directory.text())
        if path: self.world_directory.setText(path)
    def _populate_directory(self):
        path = Path(self.script.text().strip())
        if path.parent != Path("."): self.directory.setText(str(path.parent))
    def _validate(self):
        if not self.name.text().strip(): QMessageBox.warning(self, "Manglende oplysninger", "Udfyld servernavn."); return
        if self.managed.isChecked():
            if not self.executable.text().strip() or not Path(self.executable.text()).is_dir(): QMessageBox.warning(self, "Installation ikke fundet", "Vælg serverens installationsmappe."); return
            try: resolve_server_executable(ServerConfig(id="check", name=self.name.text().strip(), script="", working_directory="", game=self.game.currentData()), self.executable.text().strip())
            except FileNotFoundError as exc: QMessageBox.warning(self, "Serverfil ikke fundet", str(exc)); return
        elif not all((self.script.text().strip(), self.directory.text().strip())): QMessageBox.warning(self, "Manglende oplysninger", "Vælg start-script og arbejdsmappe."); return
        elif not self.script.text().lower().endswith(".bat") or not Path(self.script.text()).is_file(): QMessageBox.warning(self, "Ugyldigt script", "Vælg en eksisterende .bat-fil."); return
        elif not Path(self.directory.text()).is_dir(): QMessageBox.warning(self, "Mappe ikke fundet", "Vælg en eksisterende arbejdsmappe."); return
        self.accept()
    def server(self):
        config = ServerConfig(id=self.server_id or uuid.uuid4().hex, name=self.name.text().strip(), script=self.script.text().strip(), working_directory=self.directory.text().strip(), game=self.game.currentData(), world=self.world.text().strip(), password=self.password.text(), port=self.port.value(), public=self.public.isChecked(), crossplay=self.crossplay.isChecked(), additional_arguments=self.arguments.text().strip(), world_directory=self.world_directory.text().strip(), executable_directory=self.executable.text().strip(), auto_start=self.auto_start.isChecked(), start_on_manager_recovery=self.start_on_recovery.isChecked(), auto_restart=self.auto_restart.isChecked(), restart_delay=self.delay.value())
        if self.managed.isChecked():
            config = generate_managed_server(config, self.app_root, self.executable.text().strip())
        return config


class GameProfileDialog(QDialog):
    def __init__(self, profile: dict, parent=None):
        super().__init__(parent); self.setWindowTitle("Valheim indstillinger"); self.resize(520, 260)
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(int(profile.get("port", 2456)))
        self.password = QLineEdit(str(profile.get("password", ""))); self.password.setEchoMode(QLineEdit.Password)
        self.public = QCheckBox(); self.public.setChecked(bool(profile.get("public", True)))
        self.crossplay = QCheckBox(); self.crossplay.setChecked(bool(profile.get("crossplay", True)))
        self.arguments = QLineEdit(str(profile.get("additional_arguments", "-batchmode -nographics")))
        self.installation = QLineEdit(str(profile.get("installation_directory", "")))
        browse = QPushButton("Gennemse"); browse.clicked.connect(self._browse_installation)
        installation_row = QHBoxLayout(); installation_row.addWidget(self.installation); installation_row.addWidget(browse)
        form = QFormLayout(self); form.addRow("Port", self.port); form.addRow("Password", self.password); form.addRow("Public", self.public); form.addRow("Crossplay", self.crossplay); form.addRow("Ekstra argumenter", self.arguments); form.addRow("Server-installation", installation_row)
        buttons = QHBoxLayout(); cancel = QPushButton("ANNULLER"); save = QPushButton("GEM"); cancel.clicked.connect(self.reject); save.clicked.connect(self.accept); buttons.addWidget(cancel); buttons.addWidget(save); form.addRow(buttons)
    def _browse_installation(self):
        path = QFileDialog.getExistingDirectory(self, "Vælg Valheim dedicated server", self.installation.text())
        if path: self.installation.setText(path)
    def values(self):
        return {"port": self.port.value(), "password": self.password.text(), "public": self.public.isChecked(), "crossplay": self.crossplay.isChecked(), "additional_arguments": self.arguments.text().strip(), "installation_directory": self.installation.text().strip()}


class GameSetupDialog(QDialog):
    def __init__(self, definition, parent=None):
        super().__init__(parent)
        self.definition = definition
        self.result = GameSetupEngine(definition).evaluate()
        self.setWindowTitle(f"{definition.display_name} setup")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        title = QLabel(definition.display_name)
        title.setObjectName("serverTitle")
        layout.addWidget(title)
        status = QLabel("Klar" if self.result.ready else "Kræver opsætning")
        layout.addWidget(status)
        message = QLabel("Server Manager kan klargøre dette spil automatisk." if not self.result.ready else "Du kan oprette servere nu.")
        message.setWordWrap(True)
        layout.addWidget(message)
        checks = QGroupBox("Status")
        check_layout = QVBoxLayout(checks)
        for issue in self.result.issues:
            row = QLabel(f"{('✓' if issue.state == 'ok' else '⚠')} {issue.label}: {'Ready' if issue.state == 'ok' else 'Needs setup'}")
            row.setWordWrap(True)
            check_layout.addWidget(row)
        layout.addWidget(checks)
        buttons = QHBoxLayout()
        buttons.addStretch()
        create_btn = QPushButton("Opret server")
        create_btn.clicked.connect(self._create_default_server)
        buttons.addWidget(create_btn)
        close_btn = QPushButton("Luk")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        if not self.result.ready:
            fix_btn = QPushButton("Opsæt automatisk")
            fix_btn.clicked.connect(self._show_fix_hint)
            buttons.addWidget(fix_btn)
        layout.addLayout(buttons)

    def _create_default_server(self):
        parent = self.parent()
        if not hasattr(parent, "manager"):
            QMessageBox.information(self, "Default server", "This setup dialog is not attached to a running manager.")
            return
        engine = GameSetupEngine(self.definition)
        payload = engine.default_server_payload()
        config = ServerConfig(
            id=str(payload["id"]),
            name=str(payload["name"]),
            script=str(payload["script"]),
            working_directory=str(payload["working_directory"]),
            game=str(payload["game"]),
            world=str(payload["world"]),
            password=str(payload["password"]),
            port=int(payload["port"]),
            public=bool(payload["public"]),
            crossplay=bool(payload["crossplay"]),
            additional_arguments=str(payload["additional_arguments"]),
            world_directory=str(payload["world_directory"]),
            executable_directory=str(payload["executable_directory"]),
            auto_start=bool(payload["auto_start"]),
            start_on_manager_recovery=bool(payload.get("start_on_manager_recovery", False)),
            auto_restart=bool(payload["auto_restart"]),
            restart_delay=int(payload["restart_delay"]),
            max_restarts=int(payload["max_restarts"]),
            restart_window_minutes=int(payload["restart_window_minutes"]),
        )
        parent.manager.configs[config.id] = config
        parent.manager.processes[config.id] = parent.manager._create_process(config)
        parent._save_configuration()
        parent._rebuild_servers()
        parent._rescan_games()
        QMessageBox.information(self, "Server created", f"A default {self.definition.display_name} server was created and added to the server list.")
        self.accept()

    def _show_fix_hint(self):
        fix = GameSetupEngine(self.definition).fix()
        message = self.result.fix_message()
        if fix.get("action") == "steam_install":
            message += "\n\nOpening Steam's install link now."
        elif fix.get("action") == "java_download":
            message += "\n\nOpening the Java download page now."
        QMessageBox.information(self, "Automatic repair", message)
        GameSetupEngine(self.definition).run_fix()


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._password_hash = settings.web_control_password_hash
        self.setWindowTitle("Indstillinger")

        self.start_windows = QCheckBox(); self.start_windows.setChecked(settings.start_with_windows)
        self.minimize = QCheckBox(); self.minimize.setChecked(settings.minimize_to_tray)
        self.auto = QCheckBox(); self.auto.setChecked(settings.start_servers_automatically)
        self.refresh = QSpinBox(); self.refresh.setRange(1, 60); self.refresh.setValue(settings.refresh_interval_seconds)
        self.retention = QSpinBox(); self.retention.setRange(1, 3650); self.retention.setValue(settings.log_retention_days)
        self.auto_updates = QCheckBox(); self.auto_updates.setChecked(settings.automatic_update_checks)

        self.channel = QComboBox()
        self.channel.addItem("Stabil", "stable")
        self.channel.addItem("Beta", "beta")
        self.channel.setCurrentIndex(max(0, self.channel.findData(settings.update_channel)))

        self.frequency = QComboBox()
        self.frequency.addItem("Ved opstart", "startup")
        self.frequency.addItem("Dagligt", "daily")
        self.frequency.addItem("Ugentligt", "weekly")
        self.frequency.setCurrentIndex(max(0, self.frequency.findData(settings.update_check_frequency)))

        self.repository = QLineEdit(settings.release_repository)

        self.web_enabled = QCheckBox(); self.web_enabled.setChecked(settings.web_control_enabled)
        self.web_port = QSpinBox(); self.web_port.setRange(1, 65535); self.web_port.setValue(int(settings.web_control_port))
        self.web_bind = QLineEdit(settings.web_control_bind_address)
        self.web_https_cert = QLineEdit(settings.web_control_https_cert)
        self.web_https_key = QLineEdit(settings.web_control_https_key)
        self.web_advanced = QCheckBox("Vis avancerede web-indstillinger")
        self.web_advanced.setChecked(False)
        cert_browse = QPushButton("Gennemse")
        cert_browse.clicked.connect(self._browse_https_cert)
        key_browse = QPushButton("Gennemse")
        key_browse.clicked.connect(self._browse_https_key)
        self.password_status = QLabel("Konfigureret ✓" if settings.web_control_password_hash.strip() else "Ikke konfigureret")
        self.change_password = QPushButton("Skift web-password")
        self.change_password.clicked.connect(self._change_web_password)

        password_row = QHBoxLayout()
        password_row.addWidget(self.password_status)
        password_row.addWidget(self.change_password)

        cert_row = QHBoxLayout()
        cert_row.addWidget(self.web_https_cert)
        cert_row.addWidget(cert_browse)

        key_row = QHBoxLayout()
        key_row.addWidget(self.web_https_key)
        key_row.addWidget(key_browse)

        form = QFormLayout(self)
        form.addRow("Start med Windows", self.start_windows)
        form.addRow("Minimer til systembakke", self.minimize)
        form.addRow("Start servere automatisk", self.auto)
        form.addRow("Opdateringsinterval", self.refresh)
        form.addRow("Log-opbevaring (dage)", self.retention)
        form.addRow("Automatisk opdateringstjek", self.auto_updates)
        form.addRow("Opdateringskanal", self.channel)
        form.addRow("Tjek-frekvens", self.frequency)
        form.addRow("GitHub-repository", self.repository)

        web_group = QGroupBox("Web Control")
        web_form = QFormLayout(web_group)
        web_form.addRow("Remote control", self.web_enabled)
        web_form.addRow("Password", password_row)
        web_form.addRow("Netværk", QLabel("Automatisk"))
        web_form.addRow(self.web_advanced)

        self.web_advanced_group = QGroupBox("Avanceret")
        advanced_form = QFormLayout(self.web_advanced_group)
        advanced_form.addRow("Port", self.web_port)
        advanced_form.addRow("Bind address", self.web_bind)
        advanced_form.addRow("HTTPS cert", cert_row)
        advanced_form.addRow("HTTPS key", key_row)
        self.web_advanced_group.setVisible(False)
        self.web_advanced.toggled.connect(self.web_advanced_group.setVisible)

        web_form.addRow(self.web_advanced_group)
        form.addRow(web_group)

        buttons = QHBoxLayout()
        save = QPushButton("GEM")
        cancel = QPushButton("ANNULLER")
        save.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        form.addRow(buttons)

    def _change_web_password(self):
        dialog = WebPasswordDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._password_hash = dialog.password_hash
        self.password_status.setText("Konfigureret ✓")

    def _browse_https_cert(self):
        path, _ = QFileDialog.getOpenFileName(self, "Vælg HTTPS certificate", self.web_https_cert.text(), "Certificate files (*.pem *.crt *.cer);;All files (*.*)")
        if path:
            self.web_https_cert.setText(path)

    def _browse_https_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Vælg HTTPS private key", self.web_https_key.text(), "Key files (*.pem *.key);;All files (*.*)")
        if path:
            self.web_https_key.setText(path)

    def values(self):
        bind_value = self.web_bind.text().strip() if self.web_advanced.isChecked() else "0.0.0.0"
        cert_value = self.web_https_cert.text().strip() if self.web_advanced.isChecked() else self._settings.web_control_https_cert
        key_value = self.web_https_key.text().strip() if self.web_advanced.isChecked() else self._settings.web_control_https_key
        return AppSettings(
            start_with_windows=self.start_windows.isChecked(),
            minimize_to_tray=self.minimize.isChecked(),
            start_servers_automatically=self.auto.isChecked(),
            log_retention_days=self.retention.value(),
            refresh_interval_seconds=self.refresh.value(),
            automatic_update_checks=self.auto_updates.isChecked(),
            update_channel=str(self.channel.currentData()),
            update_check_frequency=str(self.frequency.currentData()),
            release_repository=self.repository.text().strip() or DEFAULT_RELEASE_REPO,
            web_control_enabled=self.web_enabled.isChecked(),
            web_control_port=self.web_port.value(),
            web_control_bind_address=bind_value or "0.0.0.0",
            web_control_password_hash=self._password_hash,
            web_control_https_cert=cert_value,
            web_control_https_key=key_value,
            watchdog_max_restarts=self._settings.watchdog_max_restarts,
            watchdog_window_minutes=self._settings.watchdog_window_minutes,
            watchdog_restart_delay_seconds=self._settings.watchdog_restart_delay_seconds,
        )


class WebPasswordDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Skift web-password")
        self.password_hash = ""
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Min. 8 tegn, fx MitSikrePassword123")
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        self.confirm.setPlaceholderText("Skriv samme password igen")

        form = QFormLayout(self)
        info = QLabel("Dette password bruges til login i Web Control fra telefon eller anden computer.")
        info.setWordWrap(True)
        form.addRow(info)
        form.addRow("Nyt password", self.password)
        form.addRow("Bekræft password", self.confirm)

        buttons = QHBoxLayout()
        cancel = QPushButton("Annuller")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Gem")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        form.addRow(buttons)

    def _save(self):
        from argon2 import PasswordHasher

        pwd = self.password.text()
        if len(pwd) < 8:
            QMessageBox.warning(self, "For kort", "Password skal være mindst 8 tegn.")
            return
        if pwd != self.confirm.text():
            QMessageBox.warning(self, "Matcher ikke", "Password-felterne matcher ikke.")
            return
        self.password_hash = PasswordHasher().hash(pwd)
        self.accept()


class WebControlDiagnosticsDialog(QDialog):
    def __init__(self, diagnostics: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Web Control diagnostik")
        form = QFormLayout(self)
        form.addRow("Enabled", QLabel(diagnostics.get("enabled", "-")))
        form.addRow("Running", QLabel(diagnostics.get("running", "-")))
        form.addRow("Bind", QLabel(diagnostics.get("bind", "-")))
        form.addRow("Port", QLabel(diagnostics.get("port", "-")))
        form.addRow("HTTPS", QLabel(diagnostics.get("https", "-")))
        form.addRow("Exposure", QLabel(diagnostics.get("exposure", "-")))
        form.addRow("URL", QLabel(diagnostics.get("url", "-")))
        error = QLabel(diagnostics.get("last_error", "-"))
        error.setWordWrap(True)
        form.addRow("Last error", error)
        close_btn = QPushButton("Luk")
        close_btn.clicked.connect(self.accept)
        form.addRow(close_btn)


class ModImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import mod")
        self.mod_archive = QLineEdit()
        self.game = QComboBox()
        [self.game.addItem(f"{item.icon}  {item.display_name}", item.id) for item in game_choices() if item.id != "generic"]
        browse = QPushButton("Gennemse")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.mod_archive); row.addWidget(browse)
        form = QFormLayout(self)
        form.addRow("Mod-fil (.zip/.dll)", row)
        form.addRow("Spil", self.game)
        buttons = QHBoxLayout(); cancel = QPushButton("Annuller"); cancel.clicked.connect(self.reject); add = QPushButton("Tilføj"); add.clicked.connect(self._validate); buttons.addWidget(cancel); buttons.addWidget(add); form.addRow(buttons)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Vælg mod-fil", "", "Mod files (*.zip *.dll);;All files (*.*)")
        if path:
            self.mod_archive.setText(path)

    def _validate(self):
        path = Path(self.mod_archive.text().strip())
        if not path.is_file():
            QMessageBox.warning(self, "Mod-fil mangler", "Vælg en gyldig mod-fil.")
            return
        self.accept()

    def values(self) -> tuple[Path, str]:
        return Path(self.mod_archive.text().strip()), str(self.game.currentData())


class ServerModsDialog(QDialog):
    def __init__(self, server: ServerConfig, mod_manager: ModManager, parent=None):
        super().__init__(parent)
        self.server = server
        self.mod_manager = mod_manager
        self.setWindowTitle(f"Server mods - {server.name}")
        self.resize(700, 460)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Søg mods...")
        self.search.textChanged.connect(self._render)
        self.profile = QComboBox()
        self.profile.addItem("Custom", "")
        for profile in self.mod_manager.list_profiles(server.game):
            self.profile.addItem(str(profile.get("name")), str(profile.get("name")))
        apply_profile = QPushButton("Anvend profil")
        apply_profile.clicked.connect(self._apply_profile)
        create_profile = QPushButton("Gem som profil")
        create_profile.clicked.connect(self._create_profile)
        self.list_widget = QVBoxLayout()

        layout = QVBoxLayout(self)
        top = QHBoxLayout(); top.addWidget(self.search); top.addWidget(self.profile); top.addWidget(apply_profile); top.addWidget(create_profile)
        layout.addLayout(top)
        container = QWidget(); container.setLayout(self.list_widget)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(container)
        layout.addWidget(scroll)
        close_btn = QPushButton("Luk"); close_btn.clicked.connect(self.accept); layout.addWidget(close_btn)
        self._render()

    def _clear_list(self):
        while self.list_widget.count():
            item = self.list_widget.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render(self):
        self._clear_list()
        active = {str(item.get("mod_id")): item for item in self.mod_manager.mods_for_server(self.server.id)}
        for mod in self.mod_manager.list_mods(game=self.server.game, query=self.search.text()):
            card = QWidget(); layout = QVBoxLayout(card)
            layout.addWidget(QLabel(str(mod.get("name"))))
            layout.addWidget(QLabel(f"Version: {mod.get('latest_version', 'unknown')}"))
            state = active.get(str(mod.get("id")))
            status = "ENABLED" if state and state.get("enabled") else "INSTALLED"
            if not state:
                status = "AVAILABLE"
            layout.addWidget(QLabel(f"Status: {status}"))
            actions = QHBoxLayout()
            enable = QPushButton("Enable")
            enable.clicked.connect(lambda _=False, mod_id=str(mod.get("id")): self._enable(mod_id))
            disable = QPushButton("Disable")
            disable.clicked.connect(lambda _=False, mod_id=str(mod.get("id")): self._disable(mod_id))
            uninstall = QPushButton("Uninstall")
            uninstall.clicked.connect(lambda _=False, mod_id=str(mod.get("id")): self._uninstall(mod_id))
            used = self.mod_manager.servers_using_mod(str(mod.get("id")))
            used_label = QLabel(f"Used by: {', '.join(used) if used else 'none'}")
            actions.addWidget(enable); actions.addWidget(disable); actions.addWidget(uninstall)
            layout.addLayout(actions)
            layout.addWidget(used_label)
            card.setObjectName("serverCard")
            self.list_widget.addWidget(card)
        self.list_widget.addStretch()

    def _enable(self, mod_id: str):
        try:
            self.mod_manager.enable_mod_for_server(self.server, mod_id)
            self._render()
        except Exception as exc:
            QMessageBox.warning(self, "Enable fejlede", str(exc))

    def _disable(self, mod_id: str):
        self.mod_manager.disable_mod_for_server(self.server.id, mod_id)
        self._render()

    def _uninstall(self, mod_id: str):
        self.mod_manager.uninstall_mod_from_server(self.server, mod_id)
        self._render()

    def _create_profile(self):
        active_mods = [str(item.get("mod_id")) for item in self.mod_manager.mods_for_server(self.server.id) if bool(item.get("enabled"))]
        name, ok = QInputDialog.getText(self, "Gem profil", "Profilnavn")
        if not ok:
            return
        profile_name = str(name).strip()
        if not profile_name:
            QMessageBox.warning(self, "Profilnavn mangler", "Skriv et profilnavn.")
            return
        self.mod_manager.create_profile(self.server.game, profile_name, active_mods)
        if self.profile.findData(profile_name) < 0:
            self.profile.addItem(profile_name, profile_name)

    def _apply_profile(self):
        profile_name = str(self.profile.currentData() or "")
        if not profile_name:
            return
        changes = self.mod_manager.apply_profile(self.server, profile_name)
        QMessageBox.information(self, "Profil anvendt", f"Enabled: {', '.join(changes['enable']) or '-'}\nDisabled: {', '.join(changes['disable']) or '-'}")
        self._render()


class QuickCreateServerDialog(QDialog):
    def __init__(self, game_statuses: dict[str, GameStatus], app_root: Path, parent=None):
        super().__init__(parent)
        self.game_statuses = game_statuses
        self.app_root = app_root
        self.setWindowTitle("Opret ny server")
        self.resize(520, 360)

        self.game = QComboBox()
        for status in game_statuses.values():
            if status.state == "ERROR":
                continue
            self.game.addItem(f"{status.definition.icon}  {status.definition.display_name}", status.definition.id)

        self.name = QLineEdit("")
        self.name.setPlaceholderText("Fx Kirken, Survival, Venne-server")
        self.password = QLineEdit("")
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Valgfrit for nogle spil")

        self.new_world = QRadioButton("Opret ny verden")
        self.existing_world = QRadioButton("Brug eksisterende verden")
        self.new_world.setChecked(True)
        self.world_name = QLineEdit("")
        self.world_name.setPlaceholderText("Fx MyWorld")
        self.world_list = QComboBox()

        self.mc_version = QComboBox()
        self.mc_version.addItem("Latest stable", "latest")
        self.mc_server_type = QComboBox()
        self.mc_server_type.addItem("Vanilla", "vanilla")
        self.mc_server_type.addItem("Fabric", "fabric")
        self.mc_server_type.addItem("Forge", "forge")
        self.mc_server_type.addItem("NeoForge", "neoforge")

        self.start_now = QCheckBox("Start serveren nu")
        self.start_now.setChecked(True)
        self.advanced = QCheckBox("Brug avancerede indstillinger")
        self._advanced_immediate = False

        self.game.currentIndexChanged.connect(self._refresh_world_controls)
        self.game.currentIndexChanged.connect(self._suggest_server_name)
        self.new_world.toggled.connect(self._refresh_world_controls)
        self.name.textChanged.connect(self._sync_world_name)
        self.advanced.toggled.connect(self._open_advanced_immediately)

        layout = QFormLayout(self)
        self.help_text = QLabel("")
        self.help_text.setWordWrap(True)
        layout.addRow(self.help_text)
        layout.addRow("Vælg spil", self.game)
        layout.addRow("Servernavn", self.name)
        layout.addRow("Adgangskode", self.password)
        layout.addRow(self.new_world)
        layout.addRow("Verdensnavn", self.world_name)
        layout.addRow(self.existing_world)
        layout.addRow("Eksisterende verdener", self.world_list)
        layout.addRow("Minecraft version", self.mc_version)
        layout.addRow("Server type", self.mc_server_type)
        layout.addRow(self.start_now)
        layout.addRow(self.advanced)

        buttons = QHBoxLayout()
        cancel = QPushButton("Annuller")
        cancel.clicked.connect(self.reject)
        create = QPushButton("Opret server")
        create.clicked.connect(self._validate)
        buttons.addWidget(cancel)
        buttons.addWidget(create)
        layout.addRow(buttons)

        self._refresh_world_controls()
        self._suggest_server_name()

    def _refresh_world_controls(self):
        selected = game_definition(str(self.game.currentData() or "generic"))
        has_world = selected.has_world
        minecraft = selected.id == "minecraft-java"
        if selected.id == "valheim":
            self.help_text.setText("Du skal kun vælge navn og password. Resten konfigureres automatisk.")
        elif selected.id == "minecraft-java":
            self.help_text.setText("Vælg navn. Version og servertype har sikre standarder, som du kan ændre hvis du vil.")
        else:
            self.help_text.setText("Vælg navn (og evt. password/world). Server Manager klarer resten automatisk.")
        self.new_world.setVisible(has_world)
        self.existing_world.setVisible(has_world)
        self.world_name.setVisible(has_world and self.new_world.isChecked())
        self.layout().labelForField(self.world_name).setVisible(has_world and self.new_world.isChecked())
        self.world_list.setVisible(has_world and self.existing_world.isChecked())
        self.layout().labelForField(self.world_list).setVisible(has_world and self.existing_world.isChecked())
        self.mc_version.setVisible(minecraft)
        self.layout().labelForField(self.mc_version).setVisible(minecraft)
        self.mc_server_type.setVisible(minecraft)
        self.layout().labelForField(self.mc_server_type).setVisible(minecraft)
        if has_world:
            worlds = discover_world_names(selected.id, self.app_root)
            self.world_list.clear()
            self.world_list.addItems(worlds)
            self.existing_world.setEnabled(bool(worlds))
            if not worlds:
                self.new_world.setChecked(True)
        else:
            self.world_list.clear()

    def _suggest_server_name(self):
        if self.name.text().strip():
            return
        selected = game_definition(str(self.game.currentData() or "generic"))
        stamp = datetime.now().strftime("%d-%m")
        self.name.setText(f"{selected.display_name} {stamp}")

    def _sync_world_name(self):
        if not self.new_world.isChecked():
            return
        if self.world_name.text().strip():
            return
        self.world_name.setText(self.name.text().strip())

    def _open_advanced_immediately(self, enabled: bool):
        if not enabled:
            return
        self._advanced_immediate = True
        self.accept()

    def _validate(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Mangler navn", "Skriv et servernavn.")
            return
        selected = game_definition(str(self.game.currentData() or "generic"))
        if selected.id == "valheim" and not self.password.text().strip():
            QMessageBox.warning(self, "Mangler adgangskode", "Skriv en adgangskode til serveren.")
            return
        if selected.has_world and self.new_world.isChecked() and not self.world_name.text().strip():
            self.world_name.setText(self.name.text().strip())
        if selected.has_world and self.existing_world.isChecked() and not self.world_list.currentText().strip():
            QMessageBox.warning(self, "Mangler verden", "Vælg en eksisterende verden eller opret en ny.")
            return
        self.accept()

    def payload(self) -> dict[str, object]:
        selected = game_definition(str(self.game.currentData() or "generic"))
        world = ""
        if selected.has_world:
            world = self.world_list.currentText().strip() if self.existing_world.isChecked() else self.world_name.text().strip()
        return {
            "game_id": selected.id,
            "name": self.name.text().strip(),
            "password": self.password.text(),
            "world": world,
            "minecraft_version": str(self.mc_version.currentData() or "latest"),
            "minecraft_server_type": str(self.mc_server_type.currentData() or "vanilla"),
            "start_now": self.start_now.isChecked(),
            "advanced": self.advanced.isChecked() or self._advanced_immediate,
        }


class ServerCard(QWidget):
    def __init__(self, config, manager, bridge, edit_callback, mods_callback, parent=None):
        super().__init__(parent); self.config = config; self.manager = manager; definition = game_definition(config.game); self.status = QLabel("OFFLINE"); self.status.setObjectName("status"); self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(2000); self.log.hide(); title = QLabel(f"{definition.icon}  {config.name}"); title.setObjectName("serverTitle"); header = QHBoxLayout(); header.addWidget(title); header.addStretch(); header.addWidget(self.status); layout = QVBoxLayout(self); layout.addLayout(header); info = config.world and f"World: {config.world}" or ""; self.details = QLabel(info); layout.addWidget(self.details); buttons = QHBoxLayout(); self.start_btn = QPushButton("START"); self.stop_btn = QPushButton("STOP"); self.restart_btn = QPushButton("RESTART"); log_btn = QPushButton("LOG"); mods = QPushButton("MODS"); edit = QPushButton("AVANCERET"); self.start_btn.clicked.connect(self.start); self.stop_btn.clicked.connect(self.stop); self.restart_btn.clicked.connect(self.restart); log_btn.clicked.connect(lambda: self.log.setVisible(not self.log.isVisible())); mods.clicked.connect(lambda: mods_callback(config.id)); edit.clicked.connect(lambda: edit_callback(config.id)); [buttons.addWidget(button) for button in (self.start_btn, self.stop_btn, self.restart_btn, log_btn, mods, edit)]; layout.addLayout(buttons); layout.addWidget(self.log); bridge.status.connect(self.update_status); bridge.output.connect(self.append_output); self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(1000); self.refresh(); self._add_world_actions()

    def _add_world_actions(self):
        if self.config.game != "valheim": return
        actions = QHBoxLayout(); open_world = QPushButton("ÅBN WORLDMAPPE"); backup = QPushButton("BACKUP .TAR.ZST"); open_world.clicked.connect(self.open_world_directory); backup.clicked.connect(self.backup_world); actions.addWidget(open_world); actions.addWidget(backup); self.layout().addLayout(actions)

    def open_world_directory(self):
        path = Path(self.config.world_directory or self.config.backup.source)
        if path.is_dir(): os.startfile(path)
        else: QMessageBox.warning(self, "World-mappe ikke fundet", f"Vælg world-mappen i serverens indstillinger.\n\n{path}")

    def backup_world(self):
        try:
            target = create_valheim_backup(self.config.world_directory or self.config.backup.source, self.config.id, self.config.backup.retention_days)
            QMessageBox.information(self, "Backup oprettet", f"Backup gemt som:\n{target}")
        except Exception as exc:
            QMessageBox.warning(self, "Backup mislykkedes", str(exc))
    def process(self): return self.manager.processes[self.config.id]
    def start(self):
        try: self.manager.start(self.config.id)
        except Exception as exc: QMessageBox.warning(self, "Start mislykkedes", str(exc))
    def stop(self): self.manager.stop(self.config.id)
    def restart(self):
        try: self.manager.restart(self.config.id)
        except Exception as exc: QMessageBox.warning(self, "Genstart mislykkedes", str(exc))
    def refresh(self):
        process = self.process(); self.details.setText((f"World: {self.config.world}  |  " if self.config.world else "") + f"Uptime: {time.strftime('%H:%M:%S', time.gmtime(process.uptime_seconds))}  |  PID: {process.pid or '-'}"); self.start_btn.setEnabled(process.status in (ServerStatus.OFFLINE, ServerStatus.CRASHED, ServerStatus.CRASH_LOOP)); self.stop_btn.setEnabled(process.status in (ServerStatus.STARTING, ServerStatus.ONLINE)); self.restart_btn.setEnabled(process.status in (ServerStatus.ONLINE, ServerStatus.CRASHED))
    def update_status(self, server_id, status):
        if server_id == self.config.id: self.status.setText(status); self.refresh()
    def append_output(self, server_id, line):
        if server_id == self.config.id: self.log.appendPlainText(line)


class GameCard(QWidget):
    def __init__(self, status: GameStatus, new_server_callback, setup_callback, details_callback, parent=None):
        super().__init__(parent)
        self.status_data = status
        definition = status.definition
        layout = QVBoxLayout(self)
        title = QLabel(f"{definition.icon}  {definition.display_name}")
        title.setObjectName("serverTitle")
        layout.addWidget(title)

        simple_state = status.state
        summary = "Du kan oprette en server nu."
        if status.state == "WARNING":
            simple_state = "KRÆVER OPSÆTNING"
            summary = "Serverfiler skal sættes op før du kan oprette servere."
        elif status.state == "ERROR":
            simple_state = "UTILGÆNGELIG"
            summary = "Automatisk opsætning er ikke tilgængelig endnu."
        elif status.state == "READY":
            simple_state = "KLAR"
        layout.addWidget(QLabel(simple_state))
        hint = QLabel(summary)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        if simple_state == "KLAR":
            create = QPushButton("NY SERVER")
            create.clicked.connect(lambda: new_server_callback(status.definition.id))
            buttons.addWidget(create)
        setup = QPushButton("OPSÆT AUTOMATISK")
        setup.clicked.connect(lambda: setup_callback(status.definition.id))
        setup.setEnabled(simple_state in {"KRÆVER OPSÆTNING", "KLAR"})
        buttons.addWidget(setup)
        details = QPushButton("DETALJER")
        details.clicked.connect(lambda: details_callback(status.definition.id))
        buttons.addWidget(details)
        layout.addLayout(buttons)
        self.setObjectName("serverCard")


class MainWindow(QMainWindow):
    def __init__(self, settings, servers, log_root, logger, app_root: Path | None = None, startup_reason: str = "normal", install_root: Path | None = None):
        super().__init__(); self.settings = settings; self.log_root = log_root; self.app_root = app_root or Path(__file__).resolve().parents[1]; self.install_root = install_root or Path(sys.executable).resolve().parent; self.startup_reason = startup_reason; self.logger = logger; self.game_profiles = GameProfileStore(self.app_root / "config" / "games.json").load(); self.bridge = EventBridge(); self.manager = ServerManager(servers, log_root, self._status_event, self._output_event, logger); self.mod_manager = ModManager(self.app_root, self.logger); self.game_statuses = {}; self._latest_release = None; self._startup_enabled = False; self.web_control = WebControlService(self.settings, self.manager, self.logger, self._save_configuration, self.install_root / "site" / "webcontrol"); self.bridge.update_available.connect(self._notify_update); self.bridge.update_none.connect(self._notify_no_update); self.bridge.update_error.connect(self._notify_update_error); self.setWindowTitle("Server Manager"); self.resize(1050, 720); self._build_ui(); self._build_tray(); self._build_menu(); self._rescan_games(); self.manager.reattach_existing_processes(); self._refresh_startup_state();
        self.system_timer = QTimer(self); self.system_timer.timeout.connect(self.update_system); self.system_timer.start(settings.refresh_interval_seconds * 1000); self.update_system()
        self._apply_web_control_settings()
        if self.settings.automatic_update_checks and self.settings.update_check_frequency == "startup": self.check_updates(background=True)

    def _build_ui(self):
        root = QWidget(); root_layout = QHBoxLayout(root); nav = QVBoxLayout(); brand = QLabel("SERVER\nMANAGER"); brand.setObjectName("appTitle"); nav.addWidget(brand); self.server_nav = QPushButton("🎮  SERVERE"); self.games_nav = QPushButton("🎯  SPIL"); self.mods_nav = QPushButton("🧩  MODS"); self.settings_nav = QPushButton("⚙  INDSTILLINGER"); nav.addWidget(self.server_nav); nav.addWidget(self.games_nav); nav.addWidget(self.mods_nav); nav.addWidget(self.settings_nav); nav.addStretch(); root_layout.addLayout(nav, 1); self.pages = QStackedWidget(); self.servers_page = self._build_servers_page(); self.games_page = self._build_games_page(); self.mods_page = self._build_mods_page(); self.settings_page = self._build_settings_page(); [self.pages.addWidget(page) for page in (self.servers_page, self.games_page, self.mods_page, self.settings_page)]; root_layout.addWidget(self.pages, 4); self.server_nav.clicked.connect(lambda: self.pages.setCurrentIndex(0)); self.games_nav.clicked.connect(lambda: self.pages.setCurrentIndex(1)); self.mods_nav.clicked.connect(lambda: self.pages.setCurrentIndex(2)); self.settings_nav.clicked.connect(lambda: self.pages.setCurrentIndex(3)); self.setCentralWidget(root)

    def _build_servers_page(self):
        page = QWidget(); layout = QVBoxLayout(page); title = QLabel("SERVERE"); title.setObjectName("pageTitle"); layout.addWidget(title); actions = QHBoxLayout(); add = QPushButton("＋ NY SERVER"); add.clicked.connect(self.add_server); actions.addWidget(add); layout.addLayout(actions); self.system_label = QLabel(); layout.addWidget(self.system_label); scroll = QScrollArea(); scroll.setWidgetResizable(True); self.server_container = QWidget(); self.server_layout = QVBoxLayout(self.server_container); scroll.setWidget(self.server_container); layout.addWidget(scroll); self._rebuild_servers(); return page
    def _build_games_page(self):
        page = QWidget(); layout = QVBoxLayout(page); header = QHBoxLayout(); title = QLabel("SPIL"); title.setObjectName("pageTitle"); header.addWidget(title); rescan = QPushButton("GENSCANN ALT"); rescan.clicked.connect(self._rescan_games); header.addWidget(rescan); layout.addLayout(header); scroll = QScrollArea(); scroll.setWidgetResizable(True); self.game_container = QWidget(); self.game_layout = QVBoxLayout(self.game_container); scroll.setWidget(self.game_container); layout.addWidget(scroll); return page
    def _build_mods_page(self):
        page = QWidget(); layout = QVBoxLayout(page); header = QHBoxLayout(); title = QLabel("MOD LIBRARY"); title.setObjectName("pageTitle"); header.addWidget(title); add = QPushButton("+ Add mod"); add.clicked.connect(self.import_mod_to_library); header.addWidget(add); layout.addLayout(header); self.mod_search = QLineEdit(); self.mod_search.setPlaceholderText("Search mods..."); self.mod_search.textChanged.connect(self._render_mod_library); layout.addWidget(self.mod_search); scroll = QScrollArea(); scroll.setWidgetResizable(True); self.mod_container = QWidget(); self.mod_layout = QVBoxLayout(self.mod_container); scroll.setWidget(self.mod_container); layout.addWidget(scroll); self._render_mod_library(); return page
    def _build_settings_page(self):
        page = QWidget(); layout = QVBoxLayout(page); title = QLabel("INDSTILLINGER"); title.setObjectName("pageTitle"); layout.addWidget(title); button = QPushButton("ÅBN INDSTILLINGER"); button.clicked.connect(self.edit_settings); layout.addWidget(button); self.version_label = QLabel(f"Nuværende version: {APP_VERSION}"); self.latest_label = QLabel("Seneste version: ukendt"); layout.addWidget(self.version_label); layout.addWidget(self.latest_label); updates = QPushButton("TJEK FOR OPDATERINGER"); updates.clicked.connect(lambda: self.check_updates(background=False)); layout.addWidget(updates); web_group = QGroupBox("WEB CONTROL"); web_layout = QFormLayout(web_group); self.web_status_label = QLabel("Ikke aktiv"); self.web_url_label = QLabel("-"); self.web_error_label = QLabel("-"); self.web_https_label = QLabel("Ikke konfigureret"); self.web_exposure_label = QLabel("Automatisk"); self.web_password_label = QLabel("Ikke konfigureret"); open_web = QPushButton("ÅBN WEB CONTROL"); open_web.clicked.connect(self.open_web_control); enable_web = QPushButton("AKTIVER WEB CONTROL"); enable_web.clicked.connect(self.enable_web_control_simple); disable_web = QPushButton("DEAKTIVER WEB CONTROL"); disable_web.clicked.connect(self.disable_web_control_simple); change_web_password = QPushButton("SKIFT WEB-PASSWORD"); change_web_password.clicked.connect(self.change_web_control_password_simple); restart_web = QPushButton("GENSTART WEB CONTROL"); restart_web.clicked.connect(self.restart_web_control); diagnostics_web = QPushButton("WEB DIAGNOSTIK"); diagnostics_web.clicked.connect(self.show_web_control_diagnostics); web_layout.addRow("Status", self.web_status_label); web_layout.addRow("Web-adresse", self.web_url_label); web_layout.addRow("Password", self.web_password_label); web_layout.addRow("Sikkerhed", self.web_https_label); web_layout.addRow("Netværk", self.web_exposure_label); web_layout.addRow("Fejl", self.web_error_label); web_layout.addRow(open_web); web_layout.addRow(enable_web); web_layout.addRow(change_web_password); web_layout.addRow(disable_web); web_layout.addRow(restart_web); web_layout.addRow(diagnostics_web); layout.addWidget(web_group); layout.addStretch(); return page

    def _rebuild_servers(self):
        while self.server_layout.count():
            item = self.server_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        grouped = {}
        for config in self.manager.configs.values(): grouped.setdefault(config.game, []).append(config)
        if not grouped: self.server_layout.addWidget(QLabel("Du har ingen servere endnu.\n\nOpret din første server med knappen ovenfor."))
        for game_id, configs in grouped.items():
            definition = game_definition(game_id); heading = QLabel(f"{definition.icon}  {definition.display_name.upper()}"); heading.setObjectName("gameHeading"); self.server_layout.addWidget(heading)
            for config in configs: self.server_layout.addWidget(ServerCard(config, self.manager, self.bridge, self.edit_server, self.manage_server_mods))
        self.server_layout.addStretch()

    def _render_mod_library(self):
        if not hasattr(self, "mod_layout"):
            return
        while self.mod_layout.count():
            item = self.mod_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        mods = self.mod_manager.list_mods(query=self.mod_search.text() if hasattr(self, "mod_search") else "")
        if not mods:
            self.mod_layout.addWidget(QLabel("Ingen mods i library endnu. Brug + Add mod."))
            self.mod_layout.addStretch()
            return
        for mod in mods:
            card = QWidget(); layout = QVBoxLayout(card)
            layout.addWidget(QLabel(str(mod.get("name"))))
            layout.addWidget(QLabel(str(mod.get("game")).upper()))
            used_by = self.mod_manager.servers_using_mod(str(mod.get("id")))
            layout.addWidget(QLabel(f"Status: {mod.get('status', 'AVAILABLE')}"))
            layout.addWidget(QLabel(f"Enabled on {len(used_by)} servers"))
            detail = QPushButton("Details")
            detail.clicked.connect(lambda _=False, mod_id=str(mod.get("id")): self.show_mod_details(mod_id))
            layout.addWidget(detail)
            card.setObjectName("serverCard")
            self.mod_layout.addWidget(card)
        self.mod_layout.addStretch()

    def _render_games(self):
        while self.game_layout.count():
            item = self.game_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        grouped = {"KLAR": [], "KRÆVER OPSÆTNING": [], "UTILGÆNGELIG": [], "FEJL": []}
        for status in self.game_statuses.values():
            if status.state == "READY":
                grouped["KLAR"].append(status)
            elif status.state == "WARNING":
                grouped["KRÆVER OPSÆTNING"].append(status)
            elif status.state == "ERROR":
                grouped["UTILGÆNGELIG"].append(status)
            else:
                grouped["FEJL"].append(status)

        for heading in ("KLAR", "KRÆVER OPSÆTNING", "UTILGÆNGELIG", "FEJL"):
            if not grouped[heading]:
                continue
            label = QLabel(heading)
            label.setObjectName("gameHeading")
            self.game_layout.addWidget(label)
            for status in grouped[heading]:
                self.game_layout.addWidget(GameCard(status, self.add_server_for_game, self.setup_game, self.show_game_details))
        self.game_layout.addStretch()

    def configure_game(self, game_id):
        if game_id != "valheim":
            QMessageBox.information(self, "Spilindstillinger", "Der er endnu ingen spil-specifikke indstillinger for dette spil.")
            return
        dialog = GameProfileDialog(self.game_profiles.get("valheim", {}), self)
        if dialog.exec() == QDialog.Accepted:
            self.game_profiles["valheim"] = dialog.values()
            GameProfileStore(self.app_root / "config" / "games.json").save(self.game_profiles)

    def import_mod_to_library(self):
        dialog = ModImportDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        archive, game_id = dialog.values()
        try:
            result = self.mod_manager.import_local_mod(archive, game_id)
        except Exception as exc:
            QMessageBox.warning(self, "Mod import fejlede", str(exc))
            return
        QMessageBox.information(self, "Mod tilføjet", f"{result.name} ({result.version}) blev tilføjet til library.")
        self._render_mod_library()

    def show_mod_details(self, mod_id: str):
        mod = self.mod_manager.state["mods"].get(mod_id)
        if not mod:
            return
        used_by = self.mod_manager.servers_using_mod(mod_id)
        lines = [
            f"Name: {mod.get('name')}",
            f"Game: {mod.get('game')}",
            f"Source: {mod.get('source', 'local')}",
            f"Latest version: {mod.get('latest_version', 'unknown')}",
            f"Installed versions: {', '.join(mod.get('installed_versions', [])) or '-'}",
            f"Used by: {', '.join(used_by) if used_by else 'none'}",
        ]
        QMessageBox.information(self, "Mod details", "\n".join(lines))

    def manage_server_mods(self, server_id: str):
        server = self.manager.configs.get(server_id)
        if not server:
            return
        process = self.manager.processes.get(server_id)
        if process and process.status in (ServerStatus.STARTING, ServerStatus.ONLINE, ServerStatus.STOPPING):
            answer = QMessageBox.question(self, "Server kører", f"{server.name} kører. Stop serveren før mod-ændringer?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer != QMessageBox.Yes:
                return
            self.manager.stop(server_id)
        dialog = ServerModsDialog(server, self.mod_manager, self)
        dialog.exec()
        self._render_mod_library()

    def setup_game(self, game_id):
        status = self.game_statuses.get(game_id)
        if status is None:
            self._rescan_games()
            return
        dialog = GameSetupDialog(status.definition, self)
        dialog.exec()
    def _rescan_games(self):
        self._scan_thread = QThread(); self._scan_worker = DetectionWorker(game_choices(), list(self.manager.configs.values())); self._scan_worker.moveToThread(self._scan_thread); self._scan_thread.started.connect(self._scan_worker.run); self._scan_worker.finished.connect(self._scan_finished); self._scan_worker.finished.connect(self._scan_thread.quit); self._scan_thread.start()
    def _scan_finished(self, statuses): self.game_statuses = statuses; self._render_games()
    def _open_folder(self, path):
        if os.name == "nt": os.startfile(path)
    def add_server(self):
        dialog = QuickCreateServerDialog(self.game_statuses, self.app_root, self)
        if dialog.exec() != QDialog.Accepted:
            return
        payload = dialog.payload()
        if bool(payload.get("advanced", False)):
            self._open_advanced_server_dialog(payload)
            return
        self._create_server_from_quick_payload(payload)

    def add_server_for_game(self, game_id: str):
        dialog = QuickCreateServerDialog(self.game_statuses, self.app_root, self)
        index = dialog.game.findData(game_id)
        if index >= 0:
            dialog.game.setCurrentIndex(index)
            dialog._refresh_world_controls()
        if dialog.exec() != QDialog.Accepted:
            return
        payload = dialog.payload()
        if bool(payload.get("advanced", False)):
            self._open_advanced_server_dialog(payload)
            return
        self._create_server_from_quick_payload(payload)

    def _open_advanced_server_dialog(self, payload: dict[str, object]):
        game_id = str(payload["game_id"])
        setup = GameSetupEngine(game_definition(game_id)).default_server_payload()
        draft = ServerConfig(
            id=f"draft-{uuid.uuid4().hex[:8]}",
            name=str(payload.get("name") or setup.get("name") or "Ny server"),
            script="",
            working_directory="",
            game=game_id,
            world=str(payload.get("world") or payload.get("name") or setup.get("world") or ""),
            password=str(payload.get("password") or ""),
            port=int(setup.get("port", 2456)),
            public=bool(setup.get("public", True)),
            crossplay=bool(setup.get("crossplay", True)),
            additional_arguments=str(setup.get("additional_arguments", "")),
            world_directory=str(setup.get("world_directory", "")),
            executable_directory="",
            auto_start=False,
            start_on_manager_recovery=True,
            auto_restart=True,
            restart_delay=int(setup.get("restart_delay", 10)),
        )
        dialog = ServerDialog(draft, self.app_root, self.game_profiles, self)
        result = dialog.exec()
        if result != QDialog.Accepted:
            return
        config = dialog.server()
        self.manager.configs[config.id] = config
        self.manager.processes[config.id] = self.manager._create_process(config)
        self._save_configuration()
        self._rebuild_servers()
        self._rescan_games()

    def _create_server_from_quick_payload(self, payload: dict[str, object]):
        game_id = str(payload["game_id"])
        setup = GameSetupEngine(game_definition(game_id)).default_server_payload()
        server_id = f"{game_id}-{uuid.uuid4().hex[:8]}"
        status = self.game_statuses.get(game_id)
        installation = status.installation_path if status else None
        if not installation:
            engine = GameSetupEngine(game_definition(game_id))
            engine.run_fix()
            self._rescan_games()
            status = self.game_statuses.get(game_id)
            installation = status.installation_path if status else None
            if not installation:
                QMessageBox.information(self, "Opsætning kræves", "Vi kunne ikke klargøre spillet automatisk endnu. Åbn SPIL og vælg OPSÆT AUTOMATISK for detaljer.")
                return

        extra_args = str(setup.get("additional_arguments", ""))
        if game_id == "minecraft-java":
            mc_type = str(payload.get("minecraft_server_type") or "vanilla")
            mc_version = str(payload.get("minecraft_version") or "latest")
            extra_args = (extra_args + f" --server-type {mc_type} --mc-version {mc_version}").strip()

        config = ServerConfig(
            id=server_id,
            name=str(payload["name"]),
            script="",
            working_directory="",
            game=game_id,
            world=str(payload.get("world") or str(payload["name"])),
            password=str(payload.get("password") or ""),
            port=int(setup.get("port", 2456)),
            public=bool(setup.get("public", True)),
            crossplay=bool(setup.get("crossplay", True)),
            additional_arguments=extra_args,
            world_directory=str(setup.get("world_directory", "")),
            executable_directory=str(installation),
            auto_start=False,
            start_on_manager_recovery=True,
            auto_restart=True,
            restart_delay=int(setup.get("restart_delay", 10)),
        )
        try:
            config = generate_managed_server(config, self.app_root, str(installation))
        except Exception as exc:
            QMessageBox.warning(self, "Kunne ikke oprette server", str(exc))
            return

        self.manager.configs[config.id] = config
        self.manager.processes[config.id] = self.manager._create_process(config)
        self._save_configuration()
        self._rebuild_servers()
        self._rescan_games()
        QMessageBox.information(self, "Server oprettet", "Din server er klar.")
        if bool(payload.get("start_now", True)):
            try:
                self.manager.start(config.id)
            except Exception as exc:
                QMessageBox.warning(self, "Start mislykkedes", str(exc))

    def edit_server(self, server_id): self._open_server_dialog(server_id)
    def show_game_details(self, game_id: str):
        status = self.game_statuses.get(game_id)
        if not status:
            return
        details = "\n".join(f"{'✓' if check.state == 'ok' else '⚠' if check.state == 'warning' else '✕'} {check.label}: {check.detail}" for check in status.checks)
        QMessageBox.information(self, f"{status.definition.display_name} detaljer", details or "Ingen detaljer tilgængelige.")
    def _open_server_dialog(self, server_id):
        dialog = ServerDialog(self.manager.configs.get(server_id) if server_id else None, self.app_root, self.game_profiles, self); result = dialog.exec()
        if result == 2 and server_id: return self._remove_server(server_id)
        if result != QDialog.Accepted: return
        config = dialog.server(); old = self.manager.configs.get(server_id) if server_id else None
        if old: self.manager.stop(server_id); self.manager.processes.pop(server_id, None)
        self.manager.configs[config.id] = config; self.manager.processes[config.id] = self.manager._create_process(config); self._save_configuration(); self._rebuild_servers(); self._rescan_games()
    def _remove_server(self, server_id):
        process = self.manager.processes[server_id]
        if process.status in (ServerStatus.STARTING, ServerStatus.ONLINE, ServerStatus.STOPPING): QMessageBox.warning(self, "Serveren kører", "Serveren kører. Stop serveren før den fjernes."); return
        self.manager.processes.pop(server_id, None); self.manager.configs.pop(server_id, None); self._save_configuration(); self._rebuild_servers(); self._rescan_games()
    def _save_configuration(self): ConfigStore(self.app_root / "config" / "servers.json").save(self.settings, list(self.manager.configs.values()))
    def edit_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.Accepted:
            previous_startup = self.settings.start_with_windows
            self.settings = dialog.values(); self._save_configuration(); self.system_timer.setInterval(self.settings.refresh_interval_seconds * 1000)
            if self.settings.web_control_enabled and not self.settings.web_control_password_hash.strip():
                password_dialog = WebPasswordDialog(self)
                if password_dialog.exec() != QDialog.Accepted:
                    self.settings.web_control_enabled = False
                else:
                    self.settings.web_control_password_hash = password_dialog.password_hash
                self._save_configuration()
            if previous_startup != self.settings.start_with_windows:
                try:
                    if self.settings.start_with_windows: enable_startup(self.install_root)
                    else: disable_startup()
                except StartupIntegrationError as exc:
                    QMessageBox.warning(self, "Windows-opstart", str(exc))
            self._apply_web_control_settings()
            self._refresh_startup_state()
            self._show_startup_state()

    def change_web_control_password_simple(self):
        dialog = WebPasswordDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.settings.web_control_password_hash = dialog.password_hash
        self._save_configuration()
        self._apply_web_control_settings()

    def enable_web_control_simple(self):
        if not self.settings.web_control_password_hash.strip():
            dialog = WebPasswordDialog(self)
            if dialog.exec() != QDialog.Accepted:
                return
            self.settings.web_control_password_hash = dialog.password_hash
        self.settings.web_control_enabled = True
        self.settings.web_control_bind_address = "0.0.0.0"
        self._save_configuration()
        self._apply_web_control_settings()

    def disable_web_control_simple(self):
        self.settings.web_control_enabled = False
        self._save_configuration()
        self._apply_web_control_settings()

    def _apply_web_control_settings(self):
        self._ensure_web_control_defaults()
        try:
            self.web_control.restart(self.settings)
        except Exception as exc:
            self.logger.exception("Web Control failed to start")
            QMessageBox.warning(self, "Web Control", f"Web Control kunne ikke starte: {exc}")
        if self.settings.web_control_enabled and self.settings.web_control_bind_address == "0.0.0.0" and not (self.settings.web_control_https_cert.strip() and self.settings.web_control_https_key.strip()):
            QMessageBox.information(
                self,
                "Web Control sikkerhed",
                "Web Control er tilgængelig fra dit lokale netværk. Konfigurer HTTPS og brug et stærkt password før eksponering uden for LAN.",
            )
        self._refresh_web_control_status()

    def _ensure_web_control_defaults(self):
        if not self.settings.web_control_enabled:
            return
        if not self.settings.web_control_bind_address.strip():
            self.settings.web_control_bind_address = "0.0.0.0"
        # Keep default LAN behavior and automatically move to next free port when needed.
        target_port = int(self.settings.web_control_port or 8080)
        if not self._is_tcp_port_available(self.settings.web_control_bind_address, target_port):
            new_port = self._next_available_port(self.settings.web_control_bind_address, target_port)
            self.settings.web_control_port = new_port
            self._save_configuration()

    def _is_tcp_port_available(self, host: str, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, int(port)))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def _next_available_port(self, host: str, start: int) -> int:
        for port in range(max(1, int(start)), 65536):
            if self._is_tcp_port_available(host, port):
                return port
        return max(1, int(start))

    def _refresh_web_control_status(self):
        if not hasattr(self, "web_status_label"):
            return
        https_configured = bool(self.settings.web_control_https_cert.strip() and self.settings.web_control_https_key.strip())
        self.web_https_label.setText("Konfigureret" if https_configured else "Ikke konfigureret")
        if hasattr(self, "web_password_label"):
            self.web_password_label.setText("Konfigureret" if self.settings.web_control_password_hash.strip() else "Ikke konfigureret")
        if self.settings.web_control_bind_address == "0.0.0.0":
            self.web_exposure_label.setText("Automatisk (lokalt netværk)")
        else:
            self.web_exposure_label.setText("Manuel binding")
        if not self.settings.web_control_enabled:
            self.web_status_label.setText("Deaktiveret")
            self.web_url_label.setText("-")
            self.web_error_label.setText("-")
            return
        if self.web_control.is_running:
            self.web_status_label.setText("Kører")
            self.web_url_label.setText(self.web_control.local_url())
            self.web_error_label.setText("-")
        else:
            self.web_status_label.setText("Ikke tilgængelig")
            self.web_url_label.setText("-")
            self.web_error_label.setText(self.web_control.last_error or "Ukendt fejl")

    def open_web_control(self):
        if not self.web_control.is_running:
            QMessageBox.information(self, "Web Control", "Web Control kører ikke.")
            return
        url = self.web_control.local_url()
        opened = webbrowser.open(url)
        if not opened:
            QMessageBox.information(self, "Web Control", f"Kunne ikke åbne browser automatisk. Åbn manuelt:\n\n{url}")
            return
        if url.startswith("https://"):
            QMessageBox.information(
                self,
                "HTTPS info",
                "Hvis browseren viser certifikat-advarsel, skal certifikatet accepteres i browseren før login virker.",
            )

    def restart_web_control(self):
        try:
            self.web_control.restart(self.settings)
        except Exception as exc:
            QMessageBox.warning(self, "Web Control", f"Kunne ikke genstarte Web Control: {exc}")
        self._refresh_web_control_status()

    def show_web_control_diagnostics(self):
        dialog = WebControlDiagnosticsDialog(self.web_control.diagnostics(), self)
        dialog.exec()
    def _build_menu(self):
        menu = self.menuBar().addMenu("Menu"); menu.addAction("Ny server", self.add_server); menu.addAction("Indstillinger", self.edit_settings); menu.addAction("Tjek for opdateringer", lambda: self.check_updates(background=False)); menu.addAction("Afslut", self.exit_application)
    def _build_tray(self):
        self.tray = QSystemTrayIcon(self); self.tray.setIcon(QApplication.style().standardIcon(QStyle.SP_ComputerIcon)); from PySide6.QtWidgets import QMenu; menu = QMenu(); menu.addAction("Åbn", self.showNormal); menu.addAction("Start alle", self.manager.start_all); menu.addAction("Stop alle", self.manager.stop_all); menu.addAction("Genstart alle", self.manager.restart_all); menu.addAction("Afslut", self.exit_application); self.tray.setContextMenu(menu); self.tray.show()
    def _status_event(self, server_id, status): self.bridge.status.emit(server_id, status.value)
    def _output_event(self, server_id, line): self.bridge.output.emit(server_id, line)
    def update_system(self):
        values = snapshot(); self.system_label.setText(f"CPU {values['cpu']:.0f}%   RAM {values['ram']:.0f}%   Disk {values['disk']:.0f}%   Opstart: {'Aktiveret' if self._startup_enabled else 'Deaktiveret'}"); self._refresh_web_control_status()

    def _refresh_startup_state(self):
        try:
            self._startup_enabled = startup_enabled()
        except Exception:
            self._startup_enabled = False
            self.logger.warning("Kunne ikke læse Windows-opstartstilstand", exc_info=True)

    def _show_startup_state(self):
        QMessageBox.information(self, "Opstart", f"Opstart er {'aktiveret' if self._startup_enabled else 'deaktiveret'}.")

    def _notify_update(self, release):
        self.latest_label.setText(f"Seneste version: {release.version}")
        message = f"Server Manager {release.version} er tilgængelig.\n\nNuværende version: {APP_VERSION}\nSeneste version: {release.version}\n\nInstaller nu?"
        answer = QMessageBox.question(self, "Opdatering tilgængelig", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.start_update(release)

    def check_updates(self, background: bool = True):
        manual_check = not background
        configured_repo = (self.settings.release_repository or DEFAULT_RELEASE_REPO).strip()

        def worker():
            try:
                release = None
                try:
                    release = fetch_latest_release(configured_repo, self.settings.update_channel)
                except UpdateError as first_exc:
                    if configured_repo != DEFAULT_RELEASE_REPO and "404" in str(first_exc):
                        self.logger.warning("Configured release repo not found (%s), falling back to default repo", configured_repo)
                        release = fetch_latest_release(DEFAULT_RELEASE_REPO, self.settings.update_channel)
                    else:
                        raise
                if release and is_newer(APP_VERSION, release.version):
                    self._latest_release = release
                    self.bridge.update_available.emit(release)
                elif manual_check:
                    self.bridge.update_none.emit()
            except Exception as exc:
                self.logger.warning("Opdateringstjek mislykkedes: %s", exc)
                if manual_check:
                    message = str(exc)
                    if "404" in message or "Not Found" in message:
                        message = f"Repository ikke fundet: {configured_repo}. Tjek feltet 'GitHub-repository' i Indstillinger."
                    self.bridge.update_error.emit(message)

        threading.Thread(target=worker, daemon=True, name="update-check").start()

    def start_update(self, release):
        try:
            archive = self.app_root / "updates" / "downloads" / f"ServerManager-{release.version}.zip"
            download_release_asset(release.asset_url, archive)
            validate_release_zip(archive)
            launch_updater(self.install_root, archive, os.getpid())
            QMessageBox.information(self, "Opdatering", "Opdateringsprogrammet er startet. Server Manager lukkes nu.")
            self.exit_application()
        except (UpdateError, OSError) as exc:
            QMessageBox.warning(self, "Opdatering mislykkedes", str(exc))

    def _notify_no_update(self):
        self.latest_label.setText(f"Seneste version: {APP_VERSION}")
        QMessageBox.information(self, "Opdateringer", "Ingen opdateringer tilgængelige.")

    def _notify_update_error(self, message: str):
        self.latest_label.setText("Seneste version: utilgængelig")
        QMessageBox.warning(self, "Opdateringer", f"Opdateringstjek mislykkedes: {message}")
    def _confirm_shutdown(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Luk Server Manager",
            "Hvis du lukker Server Manager, bliver alle administrerede servere stoppet.\n\nVil du fortsætte?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def closeEvent(self, event):
        if not self._confirm_shutdown():
            event.ignore()
            return
        if hasattr(self, "_scan_thread") and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(2000)
        self.web_control.stop()
        self.manager.shutdown(); self.tray.hide(); event.accept()

    def exit_application(self):
        if not self._confirm_shutdown():
            return
        if hasattr(self, "_scan_thread") and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(2000)
        self.web_control.stop()
        self.manager.shutdown(); self.tray.hide(); QApplication.instance().quit()


def apply_theme(app):
    app.setStyleSheet("""QWidget { background:#10161f; color:#e7edf5; font-family:Segoe UI; font-size:10pt; } #appTitle { font-size:18pt; font-weight:700; color:#8ed1c7; padding:8px; } #pageTitle { font-size:22pt; font-weight:700; color:#8ed1c7; } #gameHeading { color:#8ed1c7; font-size:13pt; font-weight:700; padding-top:14px; } #serverCard { background:#192431; border:1px solid #2b3a4c; border-radius:8px; padding:12px; margin:6px; } #serverTitle { font-size:14pt; font-weight:600; } #status { font-weight:700; color:#8ed1c7; } QPushButton { background:#26384c; border:1px solid #3b526b; border-radius:4px; padding:7px 12px; } QPushButton:hover { background:#31516b; } QPlainTextEdit { background:#0a0f15; color:#b9d7d0; border:1px solid #34485d; }""")
