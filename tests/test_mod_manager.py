from pathlib import Path
import zipfile

from core.config import AppSettings, ConfigStore, ServerConfig
from core.logging_setup import build_logger
from core.mod_manager import ModManager


def _create_store(tmp_path: Path) -> ConfigStore:
    config_file = tmp_path / "config" / "servers.json"
    return ConfigStore(config_file)


def _create_server(tmp_path: Path, game: str = "valheim") -> ServerConfig:
    work = tmp_path / "servers" / "alpha"
    work.mkdir(parents=True, exist_ok=True)
    return ServerConfig(
        id="alpha",
        name="Alpha",
        game=game,
        executable=str(work / "server.exe"),
        working_directory=str(work),
        args="",
    )


def test_local_zip_import_and_listing(tmp_path):
    logger = build_logger(tmp_path / "logs")
    manager = ModManager(tmp_path, logger)

    source_zip = tmp_path / "sample_mod-1.2.3.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("readme.txt", "hello")

    result = manager.import_local_mod(source_zip, "valheim")

    assert result.game == "valheim"
    mods = manager.list_mods("valheim")
    assert len(mods) == 1
    assert mods[0]["latest_version"] == "1.2.3"


def test_server_enable_disable_uninstall_cycle(tmp_path):
    logger = build_logger(tmp_path / "logs")
    manager = ModManager(tmp_path, logger)

    source_zip = tmp_path / "cool_mod-0.1.0.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("mod.dll", "bin")

    result = manager.import_local_mod(source_zip, "valheim")
    server = _create_server(tmp_path)

    manager.enable_mod_for_server(server, result.mod_id)
    items = manager.mods_for_server(server.id)
    assert any(item["mod_id"] == result.mod_id and item["enabled"] for item in items)

    manager.disable_mod_for_server(server.id, result.mod_id)
    items = manager.mods_for_server(server.id)
    assert any(item["mod_id"] == result.mod_id and not item["enabled"] for item in items)

    manager.uninstall_mod_from_server(server, result.mod_id)
    items = manager.mods_for_server(server.id)
    assert all(item["mod_id"] != result.mod_id for item in items)


def test_profiles_apply_diff(tmp_path):
    logger = build_logger(tmp_path / "logs")
    manager = ModManager(tmp_path, logger)

    first = tmp_path / "first_mod-1.0.0.zip"
    second = tmp_path / "second_mod-2.0.0.zip"
    with zipfile.ZipFile(first, "w") as archive:
        archive.writestr("a.txt", "a")
    with zipfile.ZipFile(second, "w") as archive:
        archive.writestr("b.txt", "b")

    first_mod = manager.import_local_mod(first, "valheim")
    second_mod = manager.import_local_mod(second, "valheim")
    server = _create_server(tmp_path)

    manager.enable_mod_for_server(server, first_mod.mod_id)
    manager.create_profile("valheim", "test", [second_mod.mod_id])

    changes = manager.apply_profile(server, "test")

    assert first_mod.mod_id in changes["disable"]
    assert second_mod.mod_id in changes["enable"]


def test_mod_directory_layout_created(tmp_path):
    store = _create_store(tmp_path)
    settings, _ = store.load()
    assert isinstance(settings, AppSettings)
    assert (tmp_path / "mods").exists()
    assert (tmp_path / "mods" / "library").exists()
