"""Save and restore the SQLite file outside the Render disk.

The live web service has one writer. Each published copy is a complete
SQLite backup under its own name. A small pointer names the current copy
and is updated with compare-and-swap, so an older process cannot replace
a newer one. The pointer also holds a short lease: a second process does
not start a step while that lease is still valid.

Nothing is uploaded when the snapshot settings are absent.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
LEASE_SECONDS = 10 * 60

_DDL = """
CREATE TABLE IF NOT EXISTS durable_generation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    generation INTEGER NOT NULL
)
"""


class DurableConflict(RuntimeError):
    """The published copy moved, or two snapshots disagree."""


@dataclass
class Pointer:
    version: int
    sha256: str
    asset_name: str
    lease_holder: str
    lease_until: str
    content_sha: str | None = None


class SnapshotStore:
    """Remote pointer plus immutable database copies."""

    def ensure_private(self) -> None:
        """Refuse storage that anyone on the internet can download."""
        return None

    def read_pointer(self) -> Pointer | None:
        raise NotImplementedError

    def compare_and_swap(self, pointer: Pointer, expected_content_sha: str | None) -> Pointer:
        raise NotImplementedError

    def upload(self, asset_name: str, data: bytes) -> None:
        raise NotImplementedError

    def download(self, asset_name: str) -> bytes:
        raise NotImplementedError

    def prune(self, keep: set[str]) -> None:
        return None


def configured() -> bool:
    return store_from_env() is not None


_PUBLIC_DASHBOARD_REPOS = {"sandrinepr/meta-dashboard"}


def store_from_env() -> SnapshotStore | None:
    """Return the private snapshot store, or nothing when it is not configured.

    The public dashboard repository is refused by name. Tests never open a
    live store. ``ensure_private`` still runs before every upload.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    repo = os.environ.get("GITHUB_SNAPSHOT_REPO", "").strip().strip("/")
    token = os.environ.get("GITHUB_SNAPSHOT_TOKEN", "").strip()
    if not repo or not token or repo.lower() in _PUBLIC_DASHBOARD_REPOS:
        return None
    branch = os.environ.get("GITHUB_SNAPSHOT_BRANCH", "").strip() or "durable-snapshot"
    from db.github_snapshot import GitHubSnapshotStore

    return GitHubSnapshotStore(repo, token, branch=branch)


def holder_id() -> str:
    return f"{os.environ.get('RENDER_INSTANCE_ID', '').strip() or socket_name()}-{os.getpid()}"


def socket_name() -> str:
    import socket

    return socket.gethostname()


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return _utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000;")
    return conn


def read_generation(path: Path) -> int:
    if not path.exists():
        return 0
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT generation FROM durable_generation WHERE id = 1"
        ).fetchone()
    except sqlite3.Error:
        return 0
    finally:
        conn.close()
    if row is None:
        return 0
    return int(row[0])


def _set_generation(path: Path, generation: int) -> None:
    conn = _connect(path)
    try:
        conn.execute(_DDL)
        conn.execute(
            """
            INSERT INTO durable_generation (id, generation) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET generation = excluded.generation
            """,
            (generation,),
        )
        conn.commit()
    finally:
        conn.close()


def backup_bytes(path: Path) -> bytes:
    temp = path.with_name(path.name + ".snapshot")
    source = _connect(path)
    target = sqlite3.connect(temp)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    try:
        return temp.read_bytes()
    finally:
        temp.unlink(missing_ok=True)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".published-sha")


def _read_sidecar(path: Path) -> str:
    file = _sidecar(path)
    if not file.exists():
        return ""
    return file.read_text(encoding="utf-8").strip()


def _write_sidecar(path: Path, digest: str) -> None:
    _sidecar(path).write_text(digest, encoding="utf-8")


def _asset_name(version: int, digest: str) -> str:
    return f"social_search-{version}-{digest[:16]}.db"


def acquire_lease(
    store: SnapshotStore,
    holder: str,
    now: datetime,
    ttl_seconds: int = LEASE_SECONDS,
) -> bool:
    """Claim the pointer lease. False when another live holder already has it."""
    until = _iso(_utc(now) + timedelta(seconds=ttl_seconds))
    pointer = store.read_pointer()
    if pointer is None:
        fresh = Pointer(
            version=0,
            sha256="",
            asset_name="",
            lease_holder=holder,
            lease_until=until,
        )
        try:
            store.compare_and_swap(fresh, None)
        except DurableConflict:
            return False
        return True
    lease_end = _parse(pointer.lease_until)
    if (
        pointer.lease_holder
        and pointer.lease_holder != holder
        and lease_end is not None
        and lease_end > _utc(now)
    ):
        return False
    try:
        store.compare_and_swap(
            replace(pointer, lease_holder=holder, lease_until=until),
            pointer.content_sha,
        )
    except DurableConflict:
        return False
    return True


def _other_lease_is_active(pointer: Pointer, holder: str, now: datetime) -> bool:
    if not pointer.lease_holder or pointer.lease_holder == holder:
        return False
    lease_end = _parse(pointer.lease_until)
    return lease_end is not None and lease_end > _utc(now)


def publish_database(
    path: Path,
    store: SnapshotStore,
    *,
    holder: str | None = None,
    now: datetime | None = None,
) -> str:
    """Upload the current file when it differs from the published copy.

    Returns ``unchanged``, ``published``, or raises ``DurableConflict`` when
    a newer copy is already published. An older process cannot move the
    pointer backwards.
    """
    owner = holder or holder_id()
    moment = now or datetime.now(tz=timezone.utc)
    with _LOCK:
        store.ensure_private()
        pointer = store.read_pointer()
        if pointer is not None and _other_lease_is_active(pointer, owner, moment):
            raise DurableConflict("lease held by another process")
        local_version = read_generation(path) if path.exists() else 0
        remote_version = pointer.version if pointer is not None else 0
        if pointer is not None and remote_version > local_version and pointer.asset_name:
            raise DurableConflict("remote snapshot is newer")
        current = backup_bytes(path)
        digest = _sha256(current)
        if pointer is not None and pointer.sha256 == digest and pointer.asset_name:
            _write_sidecar(path, digest)
            return "unchanged"
        # A local copy is ahead only after a bump that did not finish its
        # pointer update. Otherwise the copy must still be based on the
        # pointer this process last acknowledged, so two writers cannot
        # each publish a different file at the same step.
        ahead = local_version > remote_version
        based_on_pointer = pointer is None or _read_sidecar(path) == (pointer.sha256 or "")
        if pointer is not None and pointer.asset_name and not ahead and not based_on_pointer:
            raise DurableConflict("pointer moved")
        if ahead:
            new_version = local_version
            payload = current
        else:
            new_version = remote_version + 1
            _set_generation(path, new_version)
            payload = backup_bytes(path)
        published_sha = _sha256(payload)
        asset_name = _asset_name(new_version, published_sha)
        store.upload(asset_name, payload)
        fresh = store.read_pointer()
        if fresh is not None and _other_lease_is_active(fresh, owner, moment):
            raise DurableConflict("lease held by another process")
        if (
            fresh is not None
            and pointer is not None
            and (fresh.version != pointer.version or fresh.sha256 != pointer.sha256)
        ):
            raise DurableConflict("pointer moved")
        expected = None if fresh is None else fresh.content_sha
        previous_asset = "" if fresh is None else fresh.asset_name
        lease_until = _iso(_utc(moment) + timedelta(seconds=LEASE_SECONDS))
        store.compare_and_swap(
            Pointer(
                version=new_version,
                sha256=published_sha,
                asset_name=asset_name,
                lease_holder=owner,
                lease_until=lease_until,
            ),
            expected,
        )
        _write_sidecar(path, published_sha)
        keep = {asset_name}
        if previous_asset:
            keep.add(previous_asset)
        store.prune(keep)
        logger.info("Published SQLite snapshot version %s", new_version)
        return "published"


def restore_database(path: Path, store: SnapshotStore) -> bool:
    """Replace the local file with the published copy when that copy is newer.

    A local file with a higher generation is left in place so an unfinished
    publish can still be uploaded. The downloaded bytes must match the
    pointer hash and generation.
    """
    pointer = store.read_pointer()
    if pointer is None or pointer.version <= 0 or not pointer.asset_name:
        return False
    local_version = read_generation(path) if path.exists() else 0
    if local_version > pointer.version:
        return False
    if local_version == pointer.version and path.exists():
        local_sha = _sha256(backup_bytes(path))
        if local_sha == pointer.sha256:
            return False
    payload = store.download(pointer.asset_name)
    if _sha256(payload) != pointer.sha256:
        raise DurableConflict("downloaded snapshot hash does not match the pointer")
    temp = path.with_name(path.name + ".download")
    temp.write_bytes(payload)
    try:
        found = read_generation(temp)
        if found != pointer.version:
            raise DurableConflict("downloaded snapshot generation does not match the pointer")
        path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            sidecar.unlink(missing_ok=True)
        gc.collect()
        os.replace(temp, path)
        _write_sidecar(path, pointer.sha256)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    logger.info("Restored SQLite snapshot version %s", pointer.version)
    return True


def _local_needs_publish(path: Path, pointer: Pointer) -> bool:
    digest = _sha256(backup_bytes(path))
    if pointer.asset_name and pointer.sha256 == digest:
        return False
    if read_generation(path) > pointer.version:
        return True
    return bool(pointer.sha256) and _read_sidecar(path) == pointer.sha256


def sync_on_startup(
    path: Path,
    store: SnapshotStore | None = None,
    now: datetime | None = None,
) -> None:
    """Restore a newer published copy, or upload a local copy that is ahead."""
    active = store if store is not None else store_from_env()
    if active is None:
        return
    pointer = active.read_pointer()
    # A kill during upload leaves the new rows in the local file and the
    # pointer on the previous copy. Publish that local file. Restoring first
    # would throw the uncommitted snapshot away.
    if pointer is not None and path.exists() and _local_needs_publish(path, pointer):
        try:
            publish_database(path, active, now=now)
        except DurableConflict:
            # Keep the newer local file. Replacing it with the published copy
            # would drop the step that had not finished uploading.
            return
        return
    restore_database(path, active)


def publish_if_configured(path: Path) -> str:
    store = store_from_env()
    if store is None:
        return "skipped"
    return publish_database(path, store)


def restore_if_configured(path: Path) -> None:
    store = store_from_env()
    if store is None:
        return
    try:
        sync_on_startup(path, store)
    except DurableConflict:
        logger.exception("Durable snapshot was not restored")
    except Exception:
        logger.exception("Durable snapshot restore failed")
