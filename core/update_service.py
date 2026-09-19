from __future__ import annotations

import json
import ssl
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

try:
    import certifi
except Exception:  # pragma: no cover - fallback when certifi is unavailable
    certifi = None


@dataclass
class ReleaseInfo:
    tag: str
    version: str
    body: str
    asset_url: str
    published_at: str


class UpdateError(RuntimeError):
    """Raised for update-related failures."""


def _ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _open_url(request: urllib.request.Request, timeout: int):
    context = _ssl_context()
    try:
        return urllib.request.urlopen(request, timeout=timeout, context=context)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Network error: {exc.reason}") from exc
    except Exception as exc:
        raise UpdateError(f"Network request failed: {exc}") from exc


def normalize_repo(repo: str) -> str:
    text = repo.strip().strip("/")
    lowered = text.lower()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.endswith(".git"):
        text = text[:-4]
    return text.strip().strip("/")


def parse_semver(version: str) -> tuple[int, int, int]:
    text = version.strip().lstrip("vV")
    parts = text.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid version: {version}")
    return tuple(int(part) for part in parts)


def is_newer(current: str, candidate: str) -> bool:
    return parse_semver(candidate) > parse_semver(current)


def fetch_latest_release(repo: str, channel: str = "stable") -> ReleaseInfo | None:
    normalized_repo = normalize_repo(repo)
    if normalized_repo.count("/") != 1:
        raise UpdateError("Repository skal være i formatet owner/repo")
    endpoint = f"https://api.github.com/repos/{normalized_repo}/releases"
    request = urllib.request.Request(endpoint, headers={"Accept": "application/vnd.github+json", "User-Agent": "ServerManager"})
    with _open_url(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, list):
        raise UpdateError("Unexpected releases API response")

    want_prerelease = channel.lower() == "beta"
    for item in payload:
        if not isinstance(item, dict):
            continue
        prerelease = bool(item.get("prerelease", False))
        if not want_prerelease and prerelease:
            continue
        if channel.lower() == "beta" and not prerelease and item.get("draft", False):
            continue
        assets = item.get("assets") or []
        asset_url = ""
        for asset in assets:
            if str(asset.get("name", "")).lower() == "servermanager.zip":
                asset_url = str(asset.get("browser_download_url", ""))
                break
        if not asset_url:
            continue
        tag = str(item.get("tag_name") or "")
        version = tag.lstrip("vV")
        return ReleaseInfo(
            tag=tag,
            version=version,
            body=str(item.get("body") or ""),
            asset_url=asset_url,
            published_at=str(item.get("published_at") or ""),
        )
    return None


def download_release_asset(asset_url: str, target_zip: Path) -> Path:
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(asset_url, headers={"User-Agent": "ServerManager"})
    with _open_url(request, timeout=60) as response:
        target_zip.write_bytes(response.read())
    return target_zip


def validate_release_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = {name.lower() for name in archive.namelist()}
        required = {"servermanager.exe", "servermanagerwatchdog.exe", "servermanagerupdater.exe"}
        missing = [name for name in required if not any(item.endswith(name) for item in names)]
        if missing:
            raise UpdateError(f"Release archive is missing required files: {', '.join(missing)}")


def launch_updater(app_root: Path, archive_path: Path, manager_pid: int) -> None:
    updater_exe = app_root / "ServerManagerUpdater.exe"
    if updater_exe.exists():
        subprocess.Popen([
            str(updater_exe),
            "--archive",
            str(archive_path),
            "--app-root",
            str(app_root),
            "--pid",
            str(manager_pid),
        ])
        return

    updater_script = app_root / "updater_main.py"
    pythonw = Path(sys.executable)
    if updater_script.exists():
        subprocess.Popen([
            str(pythonw),
            str(updater_script),
            "--archive",
            str(archive_path),
            "--app-root",
            str(app_root),
            "--pid",
            str(manager_pid),
        ])
        return

    raise UpdateError("Updater executable not found")


def _ignore_backup_subtree(backup_root: Path):
    backup_resolved = backup_root.resolve(strict=False)

    def _ignore(current_dir: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        current = Path(current_dir).resolve(strict=False)
        for name in names:
            candidate = (current / name).resolve(strict=False)
            if candidate == backup_resolved or backup_resolved.is_relative_to(candidate):
                ignored.add(name)
        return ignored

    return _ignore


def apply_update_archive(archive: Path, app_root: Path, backup_root: Path) -> None:
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_dir = backup_root / f"app-backup-{archive.stem}"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    ignore = _ignore_backup_subtree(backup_root) if backup_root.resolve(strict=False).is_relative_to(app_root.resolve(strict=False)) else None
    shutil.copytree(app_root, backup_dir, ignore=ignore)

    extract_root = Path(tempfile.mkdtemp(prefix="servermanager-update-"))
    with zipfile.ZipFile(archive) as zip_file:
        zip_file.extractall(extract_root)

    extracted_items = list(extract_root.iterdir())
    source_root = extracted_items[0] if len(extracted_items) == 1 and extracted_items[0].is_dir() else extract_root

    for item in source_root.iterdir():
        destination = app_root / item.name
        if destination.is_dir() and item.is_dir():
            shutil.rmtree(destination)
            shutil.copytree(item, destination)
        elif destination.exists() and destination.is_file() and item.is_file():
            destination.unlink()
            shutil.copy2(item, destination)
        elif item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
