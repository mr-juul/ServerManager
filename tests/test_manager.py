from core.config import ServerConfig
from core.manager import ServerManager


class DummyProcess:
    def __init__(self, config):
        self.config = config
        self.started = 0
        self.attached_pid = None

    def start(self):
        self.started += 1

    def stop(self):
        return

    def restart(self):
        self.stop()
        self.start()

    def find_running_root_pid(self):
        return self.attached_pid

    def attach_existing(self, root_pid):
        self.attached_pid = root_pid


class ManagerHarness(ServerManager):
    def _create_process(self, config):
        return DummyProcess(config)


def test_auto_start_recovery_only_starts_recovery_enabled(tmp_path):
    recover = ServerConfig(
        id="recover",
        name="Recover",
        script="C:/recover.bat",
        working_directory="C:/",
        auto_start=False,
        start_on_manager_recovery=True,
    )
    normal = ServerConfig(
        id="normal",
        name="Normal",
        script="C:/normal.bat",
        working_directory="C:/",
        auto_start=True,
        start_on_manager_recovery=False,
    )
    manager = ManagerHarness([recover, normal], tmp_path)

    manager.auto_start_recovery()

    assert manager.processes["recover"].started == 1
    assert manager.processes["normal"].started == 0


def test_reattach_existing_processes_counts_attached(tmp_path):
    first = ServerConfig(id="one", name="One", script="C:/one.bat", working_directory="C:/")
    second = ServerConfig(id="two", name="Two", script="C:/two.bat", working_directory="C:/")
    manager = ManagerHarness([first, second], tmp_path)
    manager.processes["one"].attached_pid = 1001
    manager.processes["two"].attached_pid = None

    attached = manager.reattach_existing_processes()

    assert attached == 1
    assert manager.processes["one"].attached_pid == 1001
