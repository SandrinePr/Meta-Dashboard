"""SQLite snapshots must not lose a step or let an older copy win."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from db.durable_sqlite import (
    DurableConflict,
    Pointer,
    SnapshotStore,
    acquire_lease,
    publish_database,
    read_generation,
    restore_database,
    sync_on_startup,
)


class MemoryStore(SnapshotStore):
    def __init__(self) -> None:
        self.pointer: Pointer | None = None
        self.assets: dict[str, bytes] = {}
        self.serial = 0

    def read_pointer(self) -> Pointer | None:
        return self.pointer

    def compare_and_swap(self, pointer: Pointer, expected_content_sha: str | None) -> Pointer:
        current = None if self.pointer is None else self.pointer.content_sha
        if current != expected_content_sha:
            raise DurableConflict("pointer moved")
        self.serial += 1
        stored = Pointer(
            version=pointer.version,
            sha256=pointer.sha256,
            asset_name=pointer.asset_name,
            lease_holder=pointer.lease_holder,
            lease_until=pointer.lease_until,
            content_sha=str(self.serial),
        )
        self.pointer = stored
        return stored

    def upload(self, asset_name: str, data: bytes) -> None:
        existing = self.assets.get(asset_name)
        if existing is not None and existing != data:
            raise DurableConflict("asset changed")
        self.assets[asset_name] = data

    def download(self, asset_name: str) -> bytes:
        return self.assets[asset_name]

    def prune(self, keep: set[str]) -> None:
        for name in list(self.assets):
            if name not in keep:
                del self.assets[name]


def _db(path: Path, marker: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE notes (value TEXT)")
        conn.execute("INSERT INTO notes (value) VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _marker(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM notes").fetchone()
    finally:
        conn.close()
    return str(row[0])


def test_publish_then_restore_keeps_the_step(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    copy = tmp_path / "copy.db"
    _db(source, "step-1")
    store = MemoryStore()
    assert publish_database(source, store, holder="web-1") == "published"
    assert read_generation(source) == 1
    assert restore_database(copy, store) is True
    assert _marker(copy) == "step-1"
    assert read_generation(copy) == 1


def test_second_step_is_not_replaced_by_the_previous_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _db(source, "step-1")
    store = MemoryStore()
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    publish_database(source, store, holder="web-1", now=now)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE notes SET value = 'step-2'")
        conn.commit()
    assert publish_database(source, store, holder="web-1", now=now) == "published"
    assert read_generation(source) == 2
    assert len(store.assets) == 2

    stale = tmp_path / "stale.db"
    _db(stale, "old")
    assert sync_on_startup(stale, store) is None
    assert _marker(stale) == "step-2"
    assert read_generation(stale) == 2


def test_older_process_cannot_overwrite_a_newer_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _db(source, "step-1")
    store = MemoryStore()
    publish_database(source, store, holder="web-1")
    published = store.pointer
    assert published is not None

    older = tmp_path / "older.db"
    _db(older, "forgotten")
    with pytest.raises(DurableConflict):
        publish_database(older, store, holder="web-2")
    assert store.pointer is not None
    assert store.pointer.sha256 == published.sha256
    assert store.pointer.version == 1


def test_lease_blocks_a_second_writer_until_it_expires() -> None:
    store = MemoryStore()
    now = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
    assert acquire_lease(store, "web-1", now) is True
    assert acquire_lease(store, "web-2", now + timedelta(minutes=1)) is False
    assert acquire_lease(store, "web-2", now + timedelta(minutes=11)) is True


def test_startup_uploads_a_local_copy_that_is_ahead(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _db(source, "step-1")
    store = MemoryStore()
    publish_database(source, store, holder="web-1")
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE notes SET value = 'step-2'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS durable_generation (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                generation INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO durable_generation (id, generation) VALUES (1, 2) "
            "ON CONFLICT(id) DO UPDATE SET generation = 2"
        )
        conn.commit()
    assert store.pointer is not None
    store.pointer = Pointer(
        version=store.pointer.version,
        sha256=store.pointer.sha256,
        asset_name=store.pointer.asset_name,
        lease_holder=store.pointer.lease_holder,
        lease_until="2000-01-01T00:00:00Z",
        content_sha=store.pointer.content_sha,
    )
    sync_on_startup(source, store)
    restored = tmp_path / "restored.db"
    assert restore_database(restored, store) is True
    assert _marker(restored) == "step-2"


class _CrashOnce(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_swap = False

    def compare_and_swap(self, pointer: Pointer, expected_content_sha: str | None) -> Pointer:
        if self.fail_next_swap:
            self.fail_next_swap = False
            raise RuntimeError("killed during upload")
        return super().compare_and_swap(pointer, expected_content_sha)


def test_kill_during_upload_is_finished_on_the_next_start(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _db(source, "step-1")
    store = _CrashOnce()
    publish_database(source, store, holder="web-1")
    conn = sqlite3.connect(source)
    try:
        conn.execute("UPDATE notes SET value = 'step-2'")
        conn.commit()
    finally:
        conn.close()
    store.fail_next_swap = True
    with pytest.raises(RuntimeError, match="killed during upload"):
        publish_database(source, store, holder="web-1")
    assert store.pointer is not None
    assert store.pointer.version == 1
    assert read_generation(source) == 2
    assert _marker(source) == "step-2"
    sync_on_startup(source, store)
    assert store.pointer.version == 1
    assert _marker(source) == "step-2"
    store.pointer = Pointer(
        version=store.pointer.version,
        sha256=store.pointer.sha256,
        asset_name=store.pointer.asset_name,
        lease_holder=store.pointer.lease_holder,
        lease_until="2000-01-01T00:00:00Z",
        content_sha=store.pointer.content_sha,
    )
    sync_on_startup(source, store)
    restored = tmp_path / "restored.db"
    assert restore_database(restored, store) is True
    assert _marker(restored) == "step-2"
    assert store.pointer is not None
    assert store.pointer.version == 2


def test_kill_after_commit_and_before_upload_keeps_the_step(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _db(source, "step-1")
    store = MemoryStore()
    publish_database(source, store, holder="web-1")
    conn = sqlite3.connect(source)
    try:
        conn.execute("UPDATE notes SET value = 'step-2'")
        conn.commit()
    finally:
        conn.close()
    assert read_generation(source) == 1
    assert store.pointer is not None
    store.pointer = Pointer(
        version=store.pointer.version,
        sha256=store.pointer.sha256,
        asset_name=store.pointer.asset_name,
        lease_holder=store.pointer.lease_holder,
        lease_until="2000-01-01T00:00:00Z",
        content_sha=store.pointer.content_sha,
    )
    sync_on_startup(source, store)
    restored = tmp_path / "restored.db"
    assert restore_database(restored, store) is True
    assert _marker(restored) == "step-2"


def test_expired_lease_cannot_overwrite_the_next_holder(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _db(source, "step-1")
    store = MemoryStore()
    start = datetime(2026, 11, 6, tzinfo=timezone.utc)
    publish_database(source, store, holder="slow", now=start)
    published = store.pointer
    assert published is not None
    assert acquire_lease(store, "next", start + timedelta(minutes=11)) is True
    conn = sqlite3.connect(source)
    try:
        conn.execute("UPDATE notes SET value = 'step-2'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(DurableConflict, match="lease"):
        publish_database(
            source,
            store,
            holder="slow",
            now=start + timedelta(minutes=12),
        )
    assert store.pointer is not None
    assert store.pointer.lease_holder == "next"
    assert store.pointer.sha256 == published.sha256
    restored = tmp_path / "restored.db"
    assert restore_database(restored, store) is True
    assert _marker(restored) == "step-1"


def test_public_github_repository_never_uploads() -> None:
    from db.github_snapshot import GitHubSnapshotStore

    calls: list[str] = []

    def transport(method: str, url: str, headers: dict[str, str], data: bytes | None):
        calls.append(url)
        return 200, b'{"private": false, "visibility": "public"}'

    store = GitHubSnapshotStore(
        "SandrinePr/Meta-Dashboard",
        "test-token-not-real",
        transport=transport,
    )
    with pytest.raises(DurableConflict, match="public"):
        store.upload("social_search-1.db", b"database")
    assert calls == ["https://api.github.com/repos/SandrinePr/Meta-Dashboard"]


def test_store_from_env_refuses_the_public_dashboard(monkeypatch) -> None:
    from db.durable_sqlite import store_from_env

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("GITHUB_SNAPSHOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("GITHUB_SNAPSHOT_REPO", "SandrinePr/Meta-Dashboard")
    assert store_from_env() is None
    monkeypatch.setenv("GITHUB_SNAPSHOT_REPO", "SandrinePr/Meta-Dashboard-storage")
    store = store_from_env()
    assert store is not None
    assert store.repo == "SandrinePr/Meta-Dashboard-storage"


def test_initialize_restores_before_seed(tmp_path: Path, monkeypatch) -> None:
    order: list[str] = []

    def _restore(path: Path) -> None:
        order.append("restore")

    def _seed(path: Path) -> bool:
        order.append("seed")
        return False

    monkeypatch.setattr("db.durable_sqlite.restore_if_configured", _restore)
    monkeypatch.setattr("db.database.ensure_seed_database", _seed)
    from db.database import initialize_database

    initialize_database(tmp_path / "app.db")
    assert order[:2] == ["restore", "seed"]
