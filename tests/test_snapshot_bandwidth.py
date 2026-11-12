"""Snapshot uploads must follow real changes only (Render bandwidth, Nov 2026).

Every upload is the whole SQLite file including thumbnails. A wake that
changes nothing must not upload, and neither must a restart.
"""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from db.database import get_connection, initialize_database
from db.durable_sqlite import (
    DurableConflict,
    publish_database,
    read_generation,
    restore_database,
    sync_on_startup,
)
from sync.growth_cycle import tick_growth_cycle
from tests.test_durable_sqlite import MemoryStore
from tests.test_growth_cycle import _friday, _measurements, _Script, _setup


class CountingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.uploads = 0
        self.fail_next = 0

    def upload(self, asset_name: str, data: bytes) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise OSError("simulated network failure")
        self.uploads += 1
        super().upload(asset_name, data)


def _wake(path: Path, store: CountingStore, moment, script, holder="inst-a"):
    """What run_growth_tick_once does on every 5-minute wake."""
    initialize_database(path)
    publish_database(path, store, holder=holder, now=moment)
    result = tick_growth_cycle(now=moment, fetcher=script)
    publish_database(path, store, holder=holder, now=moment)
    return result


def _fresh_instance(path: Path, store: CountingStore, moment) -> None:
    """A new Render instance: empty disk, then the startup restore."""
    for suffix in ("", "-wal", "-shm", ".published-sha"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    sync_on_startup(path, store, now=moment)


def _add_thumbnail(post_id: int, size: int = 50_000) -> None:
    with get_connection() as conn:
        account_id = conn.execute("SELECT id FROM accounts ORDER BY id LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO posts (id, platform, external_id, account_id, content_type, published_at)"
            " VALUES (?, 'instagram', ?, ?, 'post', '2026-11-06T00:00:00Z')",
            (post_id, f"thumb-{post_id}", account_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO post_thumbnails (post_id, jpeg, updated_at) VALUES (?, ?, ?)",
            (post_id, b"\xff\xd8" + bytes(size), "2026-11-06T00:00:00Z"),
        )
        conn.commit()


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("db.database.MEDIA_DIR", tmp_path / "media")
    path = tmp_path / "growth_cycle.db"
    store = CountingStore()
    script = _Script({})
    start = _friday()
    tick_growth_cycle(now=start, fetcher=script)  # first account stored, rest waits
    _add_thumbnail(1)
    publish_database(path, store, holder="inst-a", now=start)
    store.uploads = 0
    return path, store, script, start


def test_waiting_wakes_do_not_upload(env) -> None:
    path, store, script, start = env
    moment = start
    for _ in range(6):  # 30 minutes of 5-minute wakes, next step is an hour away
        moment += timedelta(minutes=5)
        assert _wake(path, store, moment, script).action == "wait"
    assert store.uploads == 0


def test_restart_without_changes_does_not_upload(env) -> None:
    path, store, script, start = env
    moment = start
    for minutes in (16, 32, 48):
        moment = start + timedelta(minutes=minutes)
        _fresh_instance(path, store, moment)
        assert _wake(path, store, moment, script, holder=f"inst-{minutes}").action == "wait"
        publish_database(path, store, holder=f"inst-{minutes}", now=moment)  # SIGTERM
    assert store.uploads == 0


def test_real_step_is_uploaded_and_restorable(env, tmp_path: Path) -> None:
    path, store, script, start = env
    before = _measurements()
    moment = start + timedelta(hours=1)
    assert _wake(path, store, moment, script).action == "stored"
    assert store.uploads == 1
    _add_thumbnail(2)
    publish_database(path, store, holder="inst-a", now=moment)
    assert store.uploads == 2
    # A fresh instance gets the newest copy, rows and thumbnail included.
    _fresh_instance(path, store, moment + timedelta(minutes=20))
    assert read_generation(path) == store.pointer.version
    assert len(_measurements()) >= len(before)
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM post_thumbnails").fetchone()[0] == 2


def test_failed_upload_is_retried(env) -> None:
    path, store, script, start = env
    moment = start + timedelta(hours=1)
    store.fail_next = 1
    with pytest.raises(OSError):
        _wake(path, store, moment, script)
    version_before = store.pointer.version
    # Next wake (same instance) or the next instance after a restart still uploads it.
    publish_database(path, store, holder="inst-a", now=moment + timedelta(minutes=5))
    assert store.uploads == 1
    assert store.pointer.version == version_before + 1
    restore_copy = path.with_name("restored.db")
    shutil.copy(path, restore_copy)
    assert restore_database(restore_copy, store) is False  # local already matches


def test_failed_upload_survives_a_restart(env) -> None:
    path, store, script, start = env
    moment = start + timedelta(hours=1)
    _add_thumbnail(3)
    store.fail_next = 1
    with pytest.raises(OSError):
        publish_database(path, store, holder="inst-a", now=moment)
    # Instance restarts with the same disk (crash), local copy is ahead.
    sync_on_startup(path, store, now=moment + timedelta(minutes=20))
    assert store.uploads == 1
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM post_thumbnails").fetchone()[0] == 2


def test_second_instance_cannot_overwrite_newer_snapshot(env, tmp_path: Path) -> None:
    path, store, script, start = env
    stale = tmp_path / "stale.db"
    shutil.copy(path, stale)
    shutil.copy(Path(str(path) + ".published-sha"), Path(str(stale) + ".published-sha"))
    moment = start + timedelta(hours=1)
    assert _wake(path, store, moment, script, holder="inst-a").action == "stored"
    newest = store.pointer
    # The stale instance changes its own copy and tries to publish after the lease ends.
    conn = get_connection(stale)
    conn.execute("DELETE FROM post_thumbnails")
    conn.commit()
    conn.close()
    with pytest.raises(DurableConflict):
        publish_database(stale, store, holder="inst-b", now=moment + timedelta(minutes=30))
    assert store.pointer.version == newest.version
    assert store.pointer.sha256 == newest.sha256
