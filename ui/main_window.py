from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QStyle, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from core.backup import create_backup
from core.config import AppSettings, ConfigStore, ServerConfig
from core.game_detection import GameStatus, detect_game
from core.game_profiles import GameProfileStore
from core.games import GameDefinition, game_choices, game_definition
from core.manager import ServerManager
from core.managed_server import generate_managed_server, resolve_server_executable
from core.valheim_backup import create_valheim_backup
from core.server_process import ServerStatus
from core.setup_engine import GameSetupEngine
from core.startup_windows import StartupIntegrationError, disable as disable_startup, enable as enable_startup, is_enabled as startup_enabled
from core.system_monitor import snapshot
from core.update_service import UpdateError, download_release_asset, fetch_latest_release, is_newer, launch_updater, validate_release_zip
from core.version import APP_VERSION, DEFAULT_RELEASE_REPO


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
        status = QLabel("Everything is ready." if self.result.ready else "Setup requires attention.")
        layout.addWidget(status)
        message = QLabel(self.result.fix_message())
        message.setWordWrap(True)
        layout.addWidget(message)
        checks = QGroupBox("Readiness")
        check_layout = QVBoxLayout(checks)
        for issue in self.result.issues:
            row = QLabel(f"{('✓' if issue.state == 'ok' else '⚠')} {issue.label}: {issue.detail}")
            row.setWordWrap(True)
            check_layout.addWidget(row)
        layout.addWidget(checks)
        buttons = QHBoxLayout()
        buttons.addStretch()
        create_btn = QPushButton("Create server")
        create_btn.clicked.connect(self._create_default_server)
        buttons.addWidget(create_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        if not self.result.ready:
            fix_btn = QPushButton("Fix automatically")
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
        super().__init__(parent); self.setWindowTitle("Indstillinger"); self.start_windows = QCheckBox(); self.start_windows.setChecked(settings.start_with_windows); self.minimize = QCheckBox(); self.minimize.setChecked(settings.minimize_to_tray); self.auto = QCheckBox(); self.auto.setChecked(settings.start_servers_automatically); self.refresh = QSpinBox(); self.refresh.setRange(1, 60); self.refresh.setValue(settings.refresh_interval_seconds); self.retention = QSpinBox(); self.retention.setRange(1, 3650); self.retention.setValue(settings.log_retention_days); self.auto_updates = QCheckBox(); self.auto_updates.setChecked(settings.automatic_update_checks); self.channel = QComboBox(); self.channel.addItems(["stable", "beta"]); self.channel.setCurrentText(settings.update_channel); self.frequency = QComboBox(); self.frequency.addItems(["startup", "daily", "weekly"]); self.frequency.setCurrentText(settings.update_check_frequency); self.repository = QLineEdit(settings.release_repository)
        form = QFormLayout(self); form.addRow("Start med Windows", self.start_windows); form.addRow("Minimer til tray", self.minimize); form.addRow("Start servere automatisk", self.auto); form.addRow("Opdateringsinterval", self.refresh); form.addRow("Log retention", self.retention); form.addRow("Automatisk update-check", self.auto_updates); form.addRow("Update kanal", self.channel); form.addRow("Check frekvens", self.frequency); form.addRow("GitHub repository", self.repository); buttons = QHBoxLayout(); save = QPushButton("GEM"); cancel = QPushButton("ANNULLER"); save.clicked.connect(self.accept); cancel.clicked.connect(self.reject); buttons.addWidget(cancel); buttons.addWidget(save); form.addRow(buttons)
    def values(self): return AppSettings(start_with_windows=self.start_windows.isChecked(), minimize_to_tray=self.minimize.isChecked(), start_servers_automatically=self.auto.isChecked(), log_retention_days=self.retention.value(), refresh_interval_seconds=self.refresh.value(), automatic_update_checks=self.auto_updates.isChecked(), update_channel=self.channel.currentText(), update_check_frequency=self.frequency.currentText(), release_repository=self.repository.text().strip() or DEFAULT_RELEASE_REPO)


class ServerCard(QWidget):
    def __init__(self, config, manager, bridge, edit_callback, parent=None):
        super().__init__(parent); self.config = config; self.manager = manager; definition = game_definition(config.game); self.status = QLabel("OFFLINE"); self.status.setObjectName("status"); self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(2000); self.log.hide(); title = QLabel(f"{definition.icon}  {config.name}"); title.setObjectName("serverTitle"); header = QHBoxLayout(); header.addWidget(title); header.addStretch(); header.addWidget(self.status); layout = QVBoxLayout(self); layout.addLayout(header); info = config.world and f"World: {config.world}" or ""; self.details = QLabel(info); layout.addWidget(self.details); buttons = QHBoxLayout(); self.start_btn = QPushButton("START"); self.stop_btn = QPushButton("STOP"); self.restart_btn = QPushButton("RESTART"); log_btn = QPushButton("LOG"); edit = QPushButton("⚙"); self.start_btn.clicked.connect(self.start); self.stop_btn.clicked.connect(self.stop); self.restart_btn.clicked.connect(self.restart); log_btn.clicked.connect(lambda: self.log.setVisible(not self.log.isVisible())); edit.clicked.connect(lambda: edit_callback(config.id)); [buttons.addWidget(button) for button in (self.start_btn, self.stop_btn, self.restart_btn, log_btn, edit)]; layout.addLayout(buttons); layout.addWidget(self.log); bridge.status.connect(self.update_status); bridge.output.connect(self.append_output); self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(1000); self.refresh(); self._add_world_actions()

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
    def __init__(self, status: GameStatus, rescan_callback, folder_callback, configure_callback, setup_callback, parent=None):
        super().__init__(parent)
        self.status_data = status
        definition = status.definition
        layout = QVBoxLayout(self)
        title = QLabel(f"{definition.icon}  {definition.display_name}")
        title.setObjectName("serverTitle")
        layout.addWidget(title)
        layout.addWidget(QLabel(definition.description))
        layout.addWidget(QLabel(f"Status: {status.state}"))
        checks = QLabel("\n".join(f"{'✓' if check.state == 'ok' else '⚠' if check.state == 'warning' else '✕'} {check.label}: {check.detail}" for check in status.checks))
        layout.addWidget(checks)
        buttons = QHBoxLayout()
        rescan = QPushButton("RESCAN")
        rescan.clicked.connect(rescan_callback)
        buttons.addWidget(rescan)
        configure = QPushButton("CONFIGURE")
        configure.clicked.connect(lambda: configure_callback(status.definition.id))
        buttons.addWidget(configure)
        setup = QPushButton("SET UP")
        setup.clicked.connect(lambda: setup_callback(status.definition.id))
        buttons.addWidget(setup)
        if status.installation_path:
            open_folder = QPushButton("OPEN FOLDER")
            open_folder.clicked.connect(lambda: folder_callback(status.installation_path))
            buttons.addWidget(open_folder)
        layout.addLayout(buttons)
        self.setObjectName("serverCard")


class MainWindow(QMainWindow):
    def __init__(self, settings, servers, log_root, logger, app_root: Path | None = None, startup_reason: str = "normal", install_root: Path | None = None):
        super().__init__(); self.settings = settings; self.log_root = log_root; self.app_root = app_root or Path(__file__).resolve().parents[1]; self.install_root = install_root or Path(sys.executable).resolve().parent; self.startup_reason = startup_reason; self.logger = logger; self.game_profiles = GameProfileStore(self.app_root / "config" / "games.json").load(); self.bridge = EventBridge(); self.manager = ServerManager(servers, log_root, self._status_event, self._output_event, logger); self.game_statuses = {}; self._latest_release = None; self._startup_enabled = False; self.bridge.update_available.connect(self._notify_update); self.bridge.update_none.connect(self._notify_no_update); self.bridge.update_error.connect(self._notify_update_error); self.setWindowTitle("Server Manager"); self.resize(1050, 720); self._build_ui(); self._build_tray(); self._build_menu(); self._rescan_games(); self.manager.reattach_existing_processes(); self._refresh_startup_state();
        self.system_timer = QTimer(self); self.system_timer.timeout.connect(self.update_system); self.system_timer.start(settings.refresh_interval_seconds * 1000); self.update_system()
        if self.settings.automatic_update_checks and self.settings.update_check_frequency == "startup": self.check_updates(background=True)

    def _build_ui(self):
        root = QWidget(); root_layout = QHBoxLayout(root); nav = QVBoxLayout(); brand = QLabel("SERVER\nMANAGER"); brand.setObjectName("appTitle"); nav.addWidget(brand); self.server_nav = QPushButton("🎮  SERVERS"); self.games_nav = QPushButton("🎯  GAMES"); self.settings_nav = QPushButton("⚙  SETTINGS"); nav.addWidget(self.server_nav); nav.addWidget(self.games_nav); nav.addWidget(self.settings_nav); nav.addStretch(); root_layout.addLayout(nav, 1); self.pages = QStackedWidget(); self.servers_page = self._build_servers_page(); self.games_page = self._build_games_page(); self.settings_page = self._build_settings_page(); [self.pages.addWidget(page) for page in (self.servers_page, self.games_page, self.settings_page)]; root_layout.addWidget(self.pages, 4); self.server_nav.clicked.connect(lambda: self.pages.setCurrentIndex(0)); self.games_nav.clicked.connect(lambda: self.pages.setCurrentIndex(1)); self.settings_nav.clicked.connect(lambda: self.pages.setCurrentIndex(2)); self.setCentralWidget(root)

    def _build_servers_page(self):
        page = QWidget(); layout = QVBoxLayout(page); title = QLabel("SERVERS"); title.setObjectName("pageTitle"); layout.addWidget(title); add = QPushButton("＋ NY SERVER"); add.clicked.connect(self.add_server); layout.addWidget(add); self.system_label = QLabel(); layout.addWidget(self.system_label); scroll = QScrollArea(); scroll.setWidgetResizable(True); self.server_container = QWidget(); self.server_layout = QVBoxLayout(self.server_container); scroll.setWidget(self.server_container); layout.addWidget(scroll); self._rebuild_servers(); return page
    def _build_games_page(self):
        page = QWidget(); layout = QVBoxLayout(page); header = QHBoxLayout(); title = QLabel("GAMES"); title.setObjectName("pageTitle"); header.addWidget(title); rescan = QPushButton("RESCAN ALL"); rescan.clicked.connect(self._rescan_games); header.addWidget(rescan); layout.addLayout(header); scroll = QScrollArea(); scroll.setWidgetResizable(True); self.game_container = QWidget(); self.game_layout = QVBoxLayout(self.game_container); scroll.setWidget(self.game_container); layout.addWidget(scroll); return page
    def _build_settings_page(self):
        page = QWidget(); layout = QVBoxLayout(page); title = QLabel("SETTINGS"); title.setObjectName("pageTitle"); layout.addWidget(title); button = QPushButton("ÅBN INDSTILLINGER"); button.clicked.connect(self.edit_settings); layout.addWidget(button); updates = QPushButton("CHECK FOR UPDATES"); updates.clicked.connect(lambda: self.check_updates(background=False)); layout.addWidget(updates); layout.addStretch(); return page

    def _rebuild_servers(self):
        while self.server_layout.count():
            item = self.server_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        grouped = {}
        for config in self.manager.configs.values(): grouped.setdefault(config.game, []).append(config)
        if not grouped: self.server_layout.addWidget(QLabel("Du har ingen servere endnu.\n\nOpret din første server med knappen ovenfor."))
        for game_id, configs in grouped.items():
            definition = game_definition(game_id); heading = QLabel(f"{definition.icon}  {definition.display_name.upper()}"); heading.setObjectName("gameHeading"); self.server_layout.addWidget(heading)
            for config in configs: self.server_layout.addWidget(ServerCard(config, self.manager, self.bridge, self.edit_server))
        self.server_layout.addStretch()

    def _render_games(self):
        while self.game_layout.count():
            item = self.game_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for game_id, status in self.game_statuses.items(): self.game_layout.addWidget(GameCard(status, self._rescan_games, self._open_folder, self.configure_game, self.setup_game))
        self.game_layout.addStretch()

    def configure_game(self, game_id):
        if game_id != "valheim":
            QMessageBox.information(self, "Spilindstillinger", "Der er endnu ingen spil-specifikke indstillinger for dette spil.")
            return
        dialog = GameProfileDialog(self.game_profiles.get("valheim", {}), self)
        if dialog.exec() == QDialog.Accepted:
            self.game_profiles["valheim"] = dialog.values()
            GameProfileStore(self.app_root / "config" / "games.json").save(self.game_profiles)

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
    def add_server(self): self._open_server_dialog(None)
    def edit_server(self, server_id): self._open_server_dialog(server_id)
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
            if previous_startup != self.settings.start_with_windows:
                try:
                    if self.settings.start_with_windows: enable_startup(self.install_root)
                    else: disable_startup()
                except StartupIntegrationError as exc:
                    QMessageBox.warning(self, "Startup integration", str(exc))
            self._refresh_startup_state()
            self._show_startup_state()
    def _build_menu(self):
        menu = self.menuBar().addMenu("Menu"); menu.addAction("Ny server", self.add_server); menu.addAction("Settings", self.edit_settings); menu.addAction("Check for updates", lambda: self.check_updates(background=False)); menu.addAction("Exit", self.exit_application)
    def _build_tray(self):
        self.tray = QSystemTrayIcon(self); self.tray.setIcon(QApplication.style().standardIcon(QStyle.SP_ComputerIcon)); from PySide6.QtWidgets import QMenu; menu = QMenu(); menu.addAction("Åbn", self.showNormal); menu.addAction("Start alle", self.manager.start_all); menu.addAction("Stop alle", self.manager.stop_all); menu.addAction("Restart alle", self.manager.restart_all); menu.addAction("Exit", self.exit_application); self.tray.setContextMenu(menu); self.tray.show()
    def _status_event(self, server_id, status): self.bridge.status.emit(server_id, status.value)
    def _output_event(self, server_id, line): self.bridge.output.emit(server_id, line)
    def update_system(self):
        values = snapshot(); self.system_label.setText(f"CPU {values['cpu']:.0f}%   RAM {values['ram']:.0f}%   Disk {values['disk']:.0f}%   Startup: {'Enabled' if self._startup_enabled else 'Disabled'}")

    def _refresh_startup_state(self):
        try:
            self._startup_enabled = startup_enabled()
        except Exception:
            self._startup_enabled = False
            self.logger.warning("Could not read Windows startup task state", exc_info=True)

    def _show_startup_state(self):
        QMessageBox.information(self, "Startup", f"Startup is {'enabled' if self._startup_enabled else 'disabled'}.")

    def _notify_update(self, release):
        message = f"Server Manager {release.version} is available.\n\nCurrent version: {APP_VERSION}\nLatest version: {release.version}\n\nInstall now?"
        answer = QMessageBox.question(self, "Update available", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.start_update(release)

    def check_updates(self, background: bool = True):
        manual_check = not background

        def worker():
            try:
                release = fetch_latest_release(self.settings.release_repository or DEFAULT_RELEASE_REPO, self.settings.update_channel)
                if release and is_newer(APP_VERSION, release.version):
                    self._latest_release = release
                    self.bridge.update_available.emit(release)
                elif manual_check:
                    self.bridge.update_none.emit()
            except Exception as exc:
                self.logger.warning("Update check failed: %s", exc)
                if manual_check:
                    self.bridge.update_error.emit(str(exc))

        threading.Thread(target=worker, daemon=True, name="update-check").start()

    def start_update(self, release):
        try:
            archive = self.app_root / "updates" / "downloads" / f"ServerManager-{release.version}.zip"
            download_release_asset(release.asset_url, archive)
            validate_release_zip(archive)
            launch_updater(self.install_root, archive, os.getpid())
            QMessageBox.information(self, "Update", "Updater launched. Server Manager will close now.")
            self.exit_application()
        except (UpdateError, OSError) as exc:
            QMessageBox.warning(self, "Update failed", str(exc))

    def _notify_no_update(self):
        QMessageBox.information(self, "Updates", "No updates available.")

    def _notify_update_error(self, message: str):
        QMessageBox.warning(self, "Updates", f"Update check failed: {message}")
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
        self.manager.shutdown(); self.tray.hide(); event.accept()

    def exit_application(self):
        if not self._confirm_shutdown():
            return
        if hasattr(self, "_scan_thread") and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(2000)
        self.manager.shutdown(); self.tray.hide(); QApplication.instance().quit()


def apply_theme(app):
    app.setStyleSheet("""QWidget { background:#10161f; color:#e7edf5; font-family:Segoe UI; font-size:10pt; } #appTitle { font-size:18pt; font-weight:700; color:#8ed1c7; padding:8px; } #pageTitle { font-size:22pt; font-weight:700; color:#8ed1c7; } #gameHeading { color:#8ed1c7; font-size:13pt; font-weight:700; padding-top:14px; } #serverCard { background:#192431; border:1px solid #2b3a4c; border-radius:8px; padding:12px; margin:6px; } #serverTitle { font-size:14pt; font-weight:600; } #status { font-weight:700; color:#8ed1c7; } QPushButton { background:#26384c; border:1px solid #3b526b; border-radius:4px; padding:7px 12px; } QPushButton:hover { background:#31516b; } QPlainTextEdit { background:#0a0f15; color:#b9d7d0; border:1px solid #34485d; }""")
