"""SQLite database helpers for schema initialization and connections."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from config import get_settings

logger = logging.getLogger(__name__)

# Optional local file, never committed. A fresh install without this file
# gets an empty database with the same schema.
SEED_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "seed_social_search.db"
SEED_MEDIA_ZIP = Path(__file__).resolve().parents[1] / "data" / "seed_media.zip"
SEED_VERSION_PATH = Path(__file__).resolve().parents[1] / "data" / "seed_version.txt"
MEDIA_DIR = Path(__file__).resolve().parents[1] / "data" / "media"
RUNTIME_SEED_VERSION_SUFFIX = ".seed_version"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Create a SQLite connection with foreign keys enabled."""
    settings = get_settings()
    resolved_path = db_path or settings.database_path
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    # busy_timeout avoids hanging forever when another process (e.g. refresh_engagement)
    # holds a write lock on the same DB.
    conn = sqlite3.connect(resolved_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 60000;")
    return conn


def _post_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM posts").fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _cached_mtime(path: Path) -> float | None:
    """Return a thumbnail mtime. A file that vanished mid-scan counts as missing."""
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _write_atomic(target: Path, payload: bytes) -> None:
    """Replace one thumbnail so readers never see a half-written file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".thumb-", suffix=".part")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def ensure_media_cache(*, force: bool = False) -> bool:
    """Fill missing seed thumbnails and replace outdated ones.

    Thumbnails that only exist because the SQLite snapshot restored them stay
    on disk. A different file count must not empty the cache. ``force`` still
    only refreshes files that are missing or older than the zip.
    """
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if not SEED_MEDIA_ZIP.exists():
        logger.warning("No seed media zip found at %s", SEED_MEDIA_ZIP)
        return False
    zip_mtime = _cached_mtime(SEED_MEDIA_ZIP)
    if zip_mtime is None:
        return False

    on_disk: dict[str, float] = {}
    for path in list(MEDIA_DIR.glob("post_*.*")):
        mtime = _cached_mtime(path)
        if mtime is None:
            continue
        on_disk[path.name] = mtime

    try:
        with zipfile.ZipFile(SEED_MEDIA_ZIP, "r") as zf:
            members = [name for name in zf.namelist() if Path(name).name.startswith("post_")]
            written = 0
            for name in members:
                filename = Path(name).name
                target = MEDIA_DIR / filename
                current = on_disk.get(filename)
                if current is None:
                    current = _cached_mtime(target)
                # Newer files, including ones restored from the SQLite snapshot,
                # stay. force does not delete or replace them.
                if current is not None and current >= zip_mtime:
                    continue
                _write_atomic(target, zf.read(name))
                written += 1
    except zipfile.BadZipFile:
        logger.warning("Seed media zip is unreadable: %s", SEED_MEDIA_ZIP)
        return False
    except FileNotFoundError:
        logger.info("Thumbnail cache changed while it was being filled.")
        return False

    if written:
        logger.info(
            "Updated %s cached thumbnails from %s (force=%s)",
            written,
            SEED_MEDIA_ZIP,
            force,
        )
    return written > 0


def _read_seed_version(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _runtime_seed_version_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + RUNTIME_SEED_VERSION_SUFFIX)


def ensure_seed_database(db_path: Path | None = None) -> bool:
    """Copy the bundled seed DB when the runtime DB is missing, empty, or older.

    On hosts like Render the runtime DB is created once from seed. Without a
    "seed is newer" check, later seed updates in git never replace the stale
    runtime copy — so engagement fields stay missing after deploy.
    """
    settings = get_settings()
    effective_db_path = db_path or settings.database_path
    effective_db_path.parent.mkdir(parents=True, exist_ok=True)

    seeded = False
    if not SEED_DB_PATH.exists():
        logger.warning("No seed database found at %s", SEED_DB_PATH)
        ensure_media_cache()
        return False

    runtime_empty = _post_count(effective_db_path) == 0

    def _has_comparison_data(db_path: Path) -> bool:
        if not db_path.exists():
            return False
        try:
            with sqlite3.connect(db_path) as probe:
                tables = {
                    str(row[0])
                    for row in probe.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "comparison_fetch_runs" in tables:
                    if int(
                        probe.execute(
                            "SELECT COUNT(*) FROM comparison_fetch_runs"
                        ).fetchone()[0]
                    ):
                        return True
                if "account_measurements" in tables:
                    if int(
                        probe.execute(
                            "SELECT COUNT(*) FROM account_measurements"
                        ).fetchone()[0]
                    ):
                        return True
        except sqlite3.Error:
            return False
        return False

    protect_comparison = _has_comparison_data(effective_db_path)
    seed_newer = (
        effective_db_path.exists()
        and SEED_DB_PATH.stat().st_mtime > effective_db_path.stat().st_mtime
        and not protect_comparison
    )
    # Only replace runtime when the bundled seed clearly has *more* content.
    # Never wipe a runtime DB that grew past the seed (e.g. comparison posts).
    seed_content_changed = False
    if effective_db_path.exists() and not runtime_empty and not protect_comparison:
        seed_posts = _post_count(SEED_DB_PATH)
        runtime_posts = _post_count(effective_db_path)
        seed_content_changed = seed_posts > runtime_posts
    bundled_version = _read_seed_version(SEED_VERSION_PATH)
    runtime_version = _read_seed_version(_runtime_seed_version_path(effective_db_path))
    version_mismatch = bool(bundled_version) and bundled_version != runtime_version
    seed_version_changed = version_mismatch and not protect_comparison
    if runtime_empty or seed_newer or seed_content_changed or seed_version_changed:
        reason = (
            "empty"
            if runtime_empty
            else "seed_newer"
            if seed_newer
            else "seed_version_changed"
            if seed_version_changed
            else "seed_content_changed"
        )
        shutil.copy2(SEED_DB_PATH, effective_db_path)
        if bundled_version:
            _runtime_seed_version_path(effective_db_path).write_text(
                bundled_version,
                encoding="utf-8",
            )
        logger.info(
            "Seeded runtime database from %s (%s posts, reason=%s)",
            SEED_DB_PATH,
            _post_count(effective_db_path),
            reason,
        )
        seeded = True

    # Refresh media when the DB was replaced, or when a newer seed zip shipped
    # while comparison data blocked replacing the runtime SQLite file.
    ensure_media_cache(force=seeded or version_mismatch)
    if version_mismatch and bundled_version and not seeded:
        _runtime_seed_version_path(effective_db_path).write_text(
            bundled_version,
            encoding="utf-8",
        )
    return seeded


def initialize_database(db_path: Path | None = None, schema_path: Path | None = None) -> None:
    """Create all tables and indexes from schema.sql, seeding first if needed."""
    from db.migrate import apply_comparison_schema

    from db.durable_sqlite import restore_if_configured

    settings = get_settings()
    effective_db_path = db_path or settings.database_path
    effective_schema_path = schema_path or Path(__file__).with_name("schema.sql")

    restore_if_configured(effective_db_path)
    ensure_seed_database(effective_db_path)

    schema_sql = effective_schema_path.read_text(encoding="utf-8")
    with get_connection(effective_db_path) as conn:
        conn.executescript(schema_sql)
        apply_comparison_schema(conn)
        conn.commit()
    from db.thumb_store import materialize_thumbnails

    materialize_thumbnails(MEDIA_DIR, effective_db_path)
