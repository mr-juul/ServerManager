from core.update_service import is_newer, normalize_repo, parse_semver


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
