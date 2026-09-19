from core.game_profiles import GameProfileStore


def test_game_profile_round_trip(tmp_path):
    store = GameProfileStore(tmp_path / "games.json")
    profiles = store.load()
    profiles["valheim"].update({"port": 2456, "crossplay": True, "additional_arguments": "-batchmode -nographics"})
    store.save(profiles)
    loaded = store.load()
    assert loaded["valheim"]["port"] == 2456
    assert loaded["valheim"]["additional_arguments"] == "-batchmode -nographics"
