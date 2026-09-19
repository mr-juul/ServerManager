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


def test_config_round_trip(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"servers": [{"id": "one", "name": "One", "script": "C:/one.bat", "working_directory": "C:/"}]}))
    settings, servers = ConfigStore(path).load()
    assert settings.start_servers_automatically is True
    assert servers[0].id == "one"
    ConfigStore(path).save(settings, servers)
    assert ConfigStore(path).load()[1][0].name == "One"


def test_invalid_json_is_reported(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text("{")
    with pytest.raises(ConfigurationError, match="Invalid JSON"):
        ConfigStore(path).load()


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "servers.json"
    item = {"id": "same", "name": "One", "script": "C:/one.bat", "working_directory": "C:/"}
    path.write_text(json.dumps({"servers": [item, item]}))
    with pytest.raises(ConfigurationError, match="unique"):
        ConfigStore(path).load()


def test_default_data_root_uses_programdata(monkeypatch):
    monkeypatch.setenv("PROGRAMDATA", r"C:\\ProgramData")
    root = default_data_root()
    assert root == Path(r"C:\ProgramData\ServerManager")
