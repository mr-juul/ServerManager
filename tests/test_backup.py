import os
import time

from core.backup import cleanup_backups, create_backup
from core.config import BackupConfig


def test_backup_copies_source_and_retention(tmp_path):
    source = tmp_path / "worlds"
    source.mkdir()
    (source / "world.db").write_text("world")
    destination = tmp_path / "backups"
    config = BackupConfig(True, str(source), str(destination), 30)
    created = create_backup(config)
    assert (created / "world.db").read_text() == "world"
    old = destination / "old"
    old.mkdir()
    old_timestamp = time.time() - 40 * 86400
    os.utime(old, (old_timestamp, old_timestamp))
    cleanup_backups(config)
    assert not old.exists()
    assert source.exists()
