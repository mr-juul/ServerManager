from pathlib import Path

from core import startup_windows


def test_startup_command_prefers_watchdog(tmp_path):
    (tmp_path / "ServerManagerWatchdog.exe").write_text("x")
    command = startup_windows.startup_command(tmp_path)
    assert "ServerManagerWatchdog.exe" in command


def test_startup_command_falls_back_to_manager(tmp_path):
    command = startup_windows.startup_command(tmp_path)
    assert "ServerManager.exe" in command


def test_is_enabled_uses_schtasks_return_code(monkeypatch):
    class Result:
        def __init__(self, returncode):
            self.returncode = returncode

    monkeypatch.setattr(startup_windows, "_run_schtasks", lambda args: Result(0))
    assert startup_windows.is_enabled() is True
    monkeypatch.setattr(startup_windows, "_run_schtasks", lambda args: Result(1))
    assert startup_windows.is_enabled() is False
