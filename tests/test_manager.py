from core.config import ServerConfig
from core.manager import ServerManager


class DummyProcess:
    def __init__(self, config):
        self.config = config
        self.started = 0

    def start(self):
        self.started += 1

    def stop(self):
        return

    def restart(self):
        self.stop()
        self.start()


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
