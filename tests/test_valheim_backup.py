import tarfile

import zstandard

from core.valheim_backup import create_valheim_backup


def test_valheim_backup_is_tar_zst_and_keeps_world_files(tmp_path):
    world = tmp_path / "worlds"
    world.mkdir()
    (world / "Kirken.db").write_text("world-data")
    (world / "Kirken.fwl").write_text("metadata")

    archive_path = create_valheim_backup(str(world), "kirken")

    assert archive_path.suffix == ".zst"
    assert archive_path.name.endswith(".tar.zst")
    assert (world / "Kirken.db").read_text() == "world-data"
    decompressor = zstandard.ZstdDecompressor()
    with archive_path.open("rb") as compressed:
        with decompressor.stream_reader(compressed) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as archive:
                names = archive.getnames()
    assert "worlds/Kirken.db" in names
    assert "worlds/Kirken.fwl" in names
