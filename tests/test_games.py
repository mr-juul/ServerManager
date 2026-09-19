from core.game_detection import detect_game
from core.games import game_definition
from core.setup_engine import GameSetupEngine


def test_generic_game_is_ready_without_fake_installation():
    status = detect_game(game_definition("generic"))
    assert status.state == "READY"
    assert status.definition.id == "generic"


def test_valheim_detection_checks_configured_server_files(tmp_path):
    (tmp_path / "valheim_server.exe").write_text("")
    status = detect_game(game_definition("valheim"), [tmp_path])
    assert any(check.label == "Dedicated server" and check.state == "ok" for check in status.checks)
    assert status.installation_path == tmp_path


def test_steam_libraries_are_detected_from_libraryfolders_vdf(tmp_path, monkeypatch):
    steam_root = tmp_path / "Steam"
    library_root = tmp_path / "SteamLibrary"
    (steam_root / "steamapps").mkdir(parents=True)
    (library_root / "steamapps" / "common").mkdir(parents=True)
    (steam_root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n    "0"\n    {{\n        "path"\t"{steam_root.as_posix()}"\n    }}\n    "1"\n    {{\n        "path"\t"{library_root.as_posix()}"\n    }}\n}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    libraries = __import__("core.game_detection", fromlist=["_steam_libraries"])._steam_libraries()
    assert steam_root in libraries
    assert library_root in libraries


def test_setup_engine_reports_missing_steam_for_valheim(monkeypatch):
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    engine = GameSetupEngine(game_definition("valheim"))
    result = engine.evaluate()
    assert result.ready is False
    assert any(issue.label == "Steam" for issue in result.issues)


def test_setup_engine_returns_steam_install_target_for_valheim(tmp_path, monkeypatch):
    steam_root = tmp_path / "Steam"
    (steam_root / "steamapps").mkdir(parents=True)
    (steam_root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n    "0"\n    {{\n        "path"\t"{steam_root.as_posix()}"\n    }}\n}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    engine = GameSetupEngine(game_definition("valheim"))
    fix = engine.fix()
    assert fix["action"] == "steam_install"
    assert fix["target"] == "steam://install/896660"
