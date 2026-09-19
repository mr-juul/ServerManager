import zipfile

from core.update_service import apply_update_archive, is_newer, normalize_repo, parse_semver


def test_parse_semver_accepts_v_prefix():
    assert parse_semver("v1.5.0") == (1, 5, 0)


def test_is_newer_compares_semantic_versions():
    assert is_newer("1.4.0", "1.5.0") is True
    assert is_newer("1.5.0", "1.5.0") is False
    assert is_newer("2.0.0", "1.9.9") is False


def test_normalize_repo_accepts_full_github_url():
    assert normalize_repo("https://github.com/mr-juul/ServerManager") == "mr-juul/ServerManager"


def test_normalize_repo_trims_git_suffix_and_slashes():
    assert normalize_repo("github.com/mr-juul/ServerManager.git/") == "mr-juul/ServerManager"


def test_apply_update_archive_handles_backup_root_inside_app_root(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "ServerManager.exe").write_text("old", encoding="utf-8")
    (app_root / "updates" / "backups").mkdir(parents=True)

    archive = tmp_path / "ServerManager-1.0.9.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ServerManager.exe", "new")

    apply_update_archive(archive, app_root, app_root / "updates" / "backups")

    assert (app_root / "ServerManager.exe").read_text(encoding="utf-8") == "new"
    backup_dir = app_root / "updates" / "backups" / "app-backup-ServerManager-1.0.9"
    assert backup_dir.is_dir()
    assert (backup_dir / "ServerManager.exe").read_text(encoding="utf-8") == "old"
