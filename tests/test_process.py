import sys
import time

from core.config import ServerConfig
from core.server_process import ServerProcess, ServerStatus


def test_start_and_stop_batch_process(tmp_path):
    if sys.platform != "win32":
        return
    script = tmp_path / "server.bat"
    script.write_text("@echo off\necho server-started\nping 127.0.0.1 -n 30 > nul\n", encoding="utf-8")
    process = ServerProcess(ServerConfig("test", "Test", str(script), str(tmp_path)), tmp_path / "server.log")
    process.start()
    assert process.status == ServerStatus.ONLINE
    deadline = time.time() + 3
    while "server-started" not in process.recent_output and time.time() < deadline:
        time.sleep(0.05)
    assert "server-started" in process.recent_output
    process.stop(timeout=2)
    assert process.status == ServerStatus.OFFLINE
