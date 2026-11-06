"""Thumbnail cache fills gaps and keeps snapshot files when a file vanishes."""

from __future__ import annotations

import os
import threading
import zipfile
from pathlib import Path

from db.database import ensure_media_cache, initialize_database
from db.thumb_store import store_jpeg


def _jpeg() -> bytes:
    # Minimal JPEG that materialize_thumbnails will accept.
    return b"\xff\xd8\xff\xd9"


def _zip_with(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    os.utime(path, (1_700_000_000, 1_700_000_000))


def _point_cache(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    media = tmp_path / "media"
    media.mkdir()
    archive = tmp_path / "seed_media.zip"
    monkeypatch.setattr("db.database.MEDIA_DIR", media)
    monkeypatch.setattr("db.database.SEED_MEDIA_ZIP", archive)
    monkeypatch.setattr("db.database.SEED_DB_PATH", tmp_path / "missing-seed.db")
    return media, archive


def test_extra_snapshot_thumbnail_is_kept(tmp_path: Path, monkeypatch) -> None:
    media, archive = _point_cache(monkeypatch, tmp_path)
    _zip_with(archive, {"post_1.jpg": b"from-zip"})
    snapshot = media / "post_4048.jpg"
    snapshot.write_bytes(b"from-snapshot")
    os.utime(snapshot, (1_800_000_000, 1_800_000_000))

    assert ensure_media_cache() is True
    assert (media / "post_1.jpg").read_bytes() == b"from-zip"
    assert snapshot.read_bytes() == b"from-snapshot"
    assert ensure_media_cache(force=True) is False
    assert snapshot.read_bytes() == b"from-snapshot"
    assert list(media.glob("post_*.*"))


def test_missing_thumbnail_is_filled_and_outdated_one_replaced(tmp_path: Path, monkeypatch) -> None:
    media, archive = _point_cache(monkeypatch, tmp_path)
    _zip_with(archive, {"post_1.jpg": b"new-bytes", "post_2.jpg": b"second"})
    outdated = media / "post_1.jpg"
    outdated.write_bytes(b"old-bytes")
    os.utime(outdated, (1_600_000_000, 1_600_000_000))

    assert ensure_media_cache() is True
    assert outdated.read_bytes() == b"new-bytes"
    assert (media / "post_2.jpg").read_bytes() == b"second"
    assert list(media.glob(".thumb-*")) == []


def test_comparison_load_survives_thumbnail_vanishing(tmp_path: Path, monkeypatch) -> None:
    """Social vergelijking calls initialize_database while the cache is scanned."""
    media, archive = _point_cache(monkeypatch, tmp_path)
    _zip_with(archive, {"post_1.jpg": b"from-zip"})
    db_path = tmp_path / "social.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    initialize_database(db_path)

    from db.database import get_connection

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO accounts (platform, external_id, name)
            VALUES ('instagram', 'acct', 'TAOL')
            """
        )
        conn.execute(
            """
            INSERT INTO posts (
                id, platform, external_id, account_id, content_type, published_at
            )
            VALUES (4048, 'instagram', 'post-4048', 1, 'post', '2026-11-01T00:00:00+00:00')
            """
        )
        conn.commit()
    store_jpeg(4048, _jpeg(), db_path=db_path)
    snapshot = media / "post_4048.jpg"
    snapshot.write_bytes(_jpeg())
    (media / "post_1.jpg").write_bytes(b"from-zip")

    original_glob = Path.glob

    def glob_then_remove(self: Path, pattern: str):
        matches = list(original_glob(self, pattern))
        if self == media and "post_" in str(pattern):
            snapshot.unlink(missing_ok=True)
        return iter(matches)

    monkeypatch.setattr(Path, "glob", glob_then_remove)
    initialize_database(db_path)

    assert (media / "post_1.jpg").read_bytes() == b"from-zip"
    assert snapshot.read_bytes() == _jpeg()


def test_two_requests_update_the_cache_together(tmp_path: Path, monkeypatch) -> None:
    media, archive = _point_cache(monkeypatch, tmp_path)
    _zip_with(archive, {"post_1.jpg": b"from-zip"})
    snapshot = media / "post_4048.jpg"
    snapshot.write_bytes(b"from-snapshot")
    os.utime(snapshot, (1_800_000_000, 1_800_000_000))
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            ensure_media_cache()
        except BaseException as exc:  # noqa: BLE001 - the test records every failure
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert (media / "post_1.jpg").read_bytes() == b"from-zip"
    assert snapshot.read_bytes() == b"from-snapshot"
    assert list(media.glob(".thumb-*")) == []
