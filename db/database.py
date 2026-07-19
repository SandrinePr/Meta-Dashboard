"""SQLite database helpers for schema initialization and connections."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import zipfile
from pathlib import Path

from config import get_settings

logger = logging.getLogger(__name__)

SEED_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "seed_social_search.db"
SEED_MEDIA_ZIP = Path(__file__).resolve().parents[1] / "data" / "seed_media.zip"
MEDIA_DIR = Path(__file__).resolve().parents[1] / "data" / "media"


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


def _media_file_count() -> int:
    if not MEDIA_DIR.exists():
        return 0
    return len(list(MEDIA_DIR.glob("post_*.*")))


def _zip_file_count() -> int:
    if not SEED_MEDIA_ZIP.exists():
        return 0
    try:
        with zipfile.ZipFile(SEED_MEDIA_ZIP, "r") as zf:
            return sum(1 for name in zf.namelist() if name.startswith("post_"))
    except zipfile.BadZipFile:
        return 0


def ensure_media_cache(*, force: bool = False) -> bool:
    """Extract bundled thumbnail zip when missing, stale, or forced after reseed."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if not SEED_MEDIA_ZIP.exists():
        logger.warning("No seed media zip found at %s", SEED_MEDIA_ZIP)
        return False

    existing = _media_file_count()
    zip_count = _zip_file_count()
    zip_newer = (
        existing > 0
        and SEED_MEDIA_ZIP.stat().st_mtime > max(
            (p.stat().st_mtime for p in MEDIA_DIR.glob("post_*.*")),
            default=0.0,
        )
    )
    count_mismatch = existing > 0 and zip_count > 0 and existing < zip_count
    if existing and not force and not zip_newer and not count_mismatch:
        return False

    for path in MEDIA_DIR.glob("post_*.*"):
        path.unlink(missing_ok=True)
    with zipfile.ZipFile(SEED_MEDIA_ZIP, "r") as zf:
        zf.extractall(MEDIA_DIR)
    count = _media_file_count()
    logger.info(
        "Extracted %s cached thumbnails from %s (force=%s zip_newer=%s mismatch=%s)",
        count,
        SEED_MEDIA_ZIP,
        force,
        zip_newer,
        count_mismatch,
    )
    return True


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
    seed_newer = (
        effective_db_path.exists()
        and SEED_DB_PATH.stat().st_mtime > effective_db_path.stat().st_mtime
    )
    # mtime alone fails on Render persistent disks: runtime keeps getting written,
    # so a newly deployed seed can look "older" even when content changed.
    seed_content_changed = False
    if effective_db_path.exists() and not runtime_empty:
        seed_content_changed = (
            _post_count(SEED_DB_PATH) != _post_count(effective_db_path)
            or SEED_DB_PATH.stat().st_size != effective_db_path.stat().st_size
        )
    if runtime_empty or seed_newer or seed_content_changed:
        reason = (
            "empty"
            if runtime_empty
            else "seed_newer"
            if seed_newer
            else "seed_content_changed"
        )
        shutil.copy2(SEED_DB_PATH, effective_db_path)
        logger.info(
            "Seeded runtime database from %s (%s posts, reason=%s)",
            SEED_DB_PATH,
            _post_count(effective_db_path),
            reason,
        )
        seeded = True

    # Always refresh media when the DB seed was replaced so Render gets new images.
    ensure_media_cache(force=seeded)
    return seeded


def initialize_database(db_path: Path | None = None, schema_path: Path | None = None) -> None:
    """Create all tables and indexes from schema.sql, seeding first if needed."""
    settings = get_settings()
    effective_db_path = db_path or settings.database_path
    effective_schema_path = schema_path or Path(__file__).with_name("schema.sql")

    ensure_seed_database(effective_db_path)

    schema_sql = effective_schema_path.read_text(encoding="utf-8")
    with get_connection(effective_db_path) as conn:
        conn.executescript(schema_sql)
