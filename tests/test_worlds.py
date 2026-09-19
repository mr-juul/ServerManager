import json

from core.worlds import discover_valheim_worlds, discover_world_names


def test_discover_valheim_worlds_from_local_low(monkeypatch, tmp_path):
    local = tmp_path / "User" / "AppData" / "Local"
    worlds = tmp_path / "User" / "AppData" / "LocalLow" / "IronGate" / "Valheim" / "worlds_local"
    worlds.mkdir(parents=True)
    (worlds / "Kirken2026.fwl").write_text("x", encoding="utf-8")
    (worlds / "Kirken2026.db").write_text("x", encoding="utf-8")
    (worlds / "MyWorld.db.old").write_text("x", encoding="utf-8")

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    names = discover_valheim_worlds()

    assert "Kirken2026" in names
    assert "MyWorld" in names


def test_discover_valheim_worlds_from_app_root(tmp_path):
    app_root = tmp_path / "ProgramData" / "ServerManager"
    server_world = app_root / "servers" / "valheim" / "kirken" / "world"
    server_world.mkdir(parents=True)
    (server_world / "OldServer.fwl").write_text("x", encoding="utf-8")

    names = discover_valheim_worlds(app_root)

    assert "OldServer" in names


def test_discover_world_names_uses_saved_server_config(tmp_path):
    app_root = tmp_path / "ProgramData" / "ServerManager"
    config_dir = app_root / "config"
    config_dir.mkdir(parents=True)
    raw = {
        "settings": {},
        "servers": [
            {"id": "1", "name": "A", "game": "valheim", "world": "Kirken"},
            {"id": "2", "name": "B", "game": "valheim", "world": "Asgard"},
            {"id": "3", "name": "C", "game": "generic", "world": "Ignored"},
        ],
    }
    (config_dir / "servers.json").write_text(json.dumps(raw), encoding="utf-8")

    names = discover_world_names("valheim", app_root)

    assert "Kirken" in names
    assert "Asgard" in names
    assert "Ignored" not in names
