import json
from pathlib import Path

import pytest

from core.config import ConfigStore, ConfigurationError, default_data_root


def test_missing_config_is_created_with_defaults(tmp_path):
    path = tmp_path / "config" / "servers.json"
    settings, servers = ConfigStore(path).load()
    assert settings.start_servers_automatically is True
    assert servers == []
    assert path.exists()
    assert (tmp_path / "config" / "settings.json").exists()
    assert (tmp_path / "logs" / "server-manager").is_dir()
    assert (tmp_path / "updates" / "downloads").is_dir()


def test_config_round_trip(tmp_path):
    path = tmp_path / "config" / "servers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"servers": [{"id": "one", "name": "One", "script": "C:/one.bat", "working_directory": "C:/"}]}))
    settings, servers = ConfigStore(path).load()
    assert settings.start_servers_automatically is True
    assert servers[0].id == "one"
    servers[0].start_on_manager_recovery = True
    ConfigStore(path).save(settings, servers)
    assert ConfigStore(path).load()[1][0].name == "One"
    assert ConfigStore(path).load()[1][0].start_on_manager_recovery is True


def test_invalid_json_is_reported(tmp_path):
    path = tmp_path / "config" / "servers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{")
    with pytest.raises(ConfigurationError, match="Invalid JSON"):
        ConfigStore(path).load()


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "config" / "servers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    item = {"id": "same", "name": "One", "script": "C:/one.bat", "working_directory": "C:/"}
    path.write_text(json.dumps({"servers": [item, item]}))
    with pytest.raises(ConfigurationError, match="unique"):
        ConfigStore(path).load()


def test_legacy_embedded_settings_are_migrated_to_settings_file(tmp_path):
    path = tmp_path / "config" / "servers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "schema_version": 2,
        "settings": {"start_servers_automatically": False, "refresh_interval_seconds": 7},
        "servers": [{"id": "one", "name": "One", "script": "C:/one.bat", "working_directory": "C:/"}],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    settings, servers = ConfigStore(path).load()
    assert settings.start_servers_automatically is False
    assert settings.refresh_interval_seconds == 7
    assert servers[0].id == "one"
    settings_file = tmp_path / "config" / "settings.json"
    assert settings_file.exists()
    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3


def test_default_data_root_uses_programdata(monkeypatch):
    monkeypatch.setenv("PROGRAMDATA", r"C:\\ProgramData")
    root = default_data_root()
    assert root == Path(r"C:\ProgramData\ServerManager")
