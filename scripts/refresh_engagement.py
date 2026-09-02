"""Refresh engagement + view insights on existing posts without a full media re-sync."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from db.database import initialize_database  # noqa: E402
from sync.engagement_refresh import refresh_post_rows  # noqa: E402
from db.database import get_connection  # noqa: E402
from meta.client import MetaClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("refresh_engagement")


def refresh_posts(
    *,
    platform: str | None = None,
    limit: int | None = None,
    missing_only: bool = False,
) -> tuple[int, int, int]:
    """Return (updated, skipped, failed)."""
    import json

    initialize_database()
    client = MetaClient.from_settings()

    with get_connection() as conn:
        sql = "SELECT id, platform, external_id, raw_json FROM posts"
        params: list[object] = []
        if platform:
            sql += " WHERE platform = ?"
            params.append(platform)
        sql += " ORDER BY published_at DESC"
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]

    if missing_only:
        filtered = []
        for row in rows:
            try:
                payload = json.loads(row["raw_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if payload.get("insights_views") is None:
                filtered.append(row)
        rows = filtered
        logger.info("missing_only: %s posts without insights_views", len(rows))

    if limit:
        rows = rows[:limit]

    stats = refresh_post_rows(rows, client=client)
    return stats.updated, stats.skipped, stats.failed


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Refresh engagement + view insights on posts."
    )
    parser.add_argument("--platform", choices=["instagram", "facebook"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only refresh posts that do not yet have insights_views.",
    )
    args = parser.parse_args()
    updated, skipped, failed = refresh_posts(
        platform=args.platform,
        limit=args.limit,
        missing_only=args.missing_only,
    )
    print(f"DONE updated={updated} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
