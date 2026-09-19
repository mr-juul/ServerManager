from core.config import ServerConfig
from core.managed_server import generate_managed_server, resolve_server_executable


def test_managed_valheim_launcher_creates_folder_and_script(tmp_path):
    installation = tmp_path / "valheim-installation"
    installation.mkdir()
    (installation / "valheim_server.exe").write_text("")
    config = ServerConfig(
        id="kirken",
        name="Kirken",
        script="",
        working_directory="",
        game="valheim",
        world="Kirken2026",
        password="secret",
        port=2456,
        public=True,
        crossplay=True,
    )
    updated = generate_managed_server(config, tmp_path, str(installation))
    script = tmp_path / "servers" / "kirken" / "start.bat"
    assert updated.script == str(script)
    assert updated.working_directory == str(script.parent)
    content = script.read_text(encoding="utf-8")
    assert "valheim_server.exe" in content
    assert "Kirken2026" in content
    assert "-crossplay" in content
    assert script.parent.is_dir()


def test_managed_launcher_finds_valheim_executable_in_installation_folder(tmp_path):
    executable = tmp_path / "valheim_server.exe"
    executable.write_text("")
    config = ServerConfig("v", "Valheim", "", "", game="valheim", world="World")
    assert resolve_server_executable(config, str(tmp_path)) == executable
