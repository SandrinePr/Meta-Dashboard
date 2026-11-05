"""Keep compressed thumbnails inside SQLite so a snapshot can restore them."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from db.database import get_connection

logger = logging.getLogger(__name__)

_JPEG_MAGIC = b"\xff\xd8"


def store_jpeg(post_id: int, data: bytes, *, db_path: Path | None = None) -> None:
    """Save one compressed JPEG. Ignore anything that is not a JPEG."""
    if not data.startswith(_JPEG_MAGIC):
        return
    updated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO post_thumbnails (post_id, jpeg, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                jpeg = excluded.jpeg,
                updated_at = excluded.updated_at
            """,
            (int(post_id), data, updated_at),
        )
        conn.commit()


def materialize_thumbnails(media_dir: Path, db_path: Path | None = None) -> int:
    """Write stored JPEGs to disk when the file is missing. Return how many."""
    media_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with get_connection(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "post_thumbnails" not in tables:
            return 0
        rows = conn.execute("SELECT post_id, jpeg FROM post_thumbnails").fetchall()
    for row in rows:
        post_id = int(row["post_id"])
        blob = row["jpeg"]
        if not isinstance(blob, (bytes, bytearray)) or not bytes(blob).startswith(_JPEG_MAGIC):
            continue
        target = media_dir / f"post_{post_id}.jpg"
        if target.exists() and target.stat().st_size > 0:
            continue
        target.write_bytes(bytes(blob))
        written += 1
    if written:
        logger.info("Restored %s thumbnails from the database snapshot", written)
    return written
