"""Refresh engagement + view insights on existing posts without a full media re-sync."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from db.database import get_connection, initialize_database  # noqa: E402
from meta.client import MetaClient, MetaClientError, format_meta_client_error  # noqa: E402
from meta.endpoints import (  # noqa: E402
    FACEBOOK_POST_FIELDS,
    INSTAGRAM_MEDIA_FIELDS,
    as_fields_param,
)
from meta.insights import flatten_facebook_insights, flatten_instagram_insights  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("refresh_engagement")

IG_ENGAGEMENT_KEYS = (
    "like_count",
    "comments_count",
    "saved_count",
    "shares_count",
)
FB_ENGAGEMENT_KEYS = ("likes", "reactions", "comments", "shares", "video_views", "view_count")


def _merge_engagement(existing: dict, fresh: dict, keys: tuple[str, ...]) -> dict:
    merged = dict(existing)
    for key in keys:
        if key in fresh:
            merged[key] = fresh[key]
    return merged


def refresh_posts(
    *,
    platform: str | None = None,
    limit: int | None = None,
    missing_only: bool = False,
) -> tuple[int, int, int]:
    """Return (updated, skipped, failed)."""
    initialize_database()
    client = MetaClient.from_settings()
    updated = skipped = failed = 0

    with get_connection() as conn:
        sql = "SELECT id, platform, external_id, raw_json FROM posts"
        params: list[object] = []
        if platform:
            sql += " WHERE platform = ?"
            params.append(platform)
        sql += " ORDER BY published_at DESC"
        rows = conn.execute(sql, params).fetchall()

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

    ig_fields = as_fields_param(INSTAGRAM_MEDIA_FIELDS)
    fb_fields = as_fields_param(FACEBOOK_POST_FIELDS)

    for idx, row in enumerate(rows, start=1):
        post_id = int(row["id"])
        plat = row["platform"]
        external_id = row["external_id"]
        try:
            existing = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}

        try:
            if plat == "instagram":
                fresh = client.get_json(external_id, params={"fields": ig_fields})
                merged = _merge_engagement(existing, fresh, IG_ENGAGEMENT_KEYS)
                for key in ("id", "caption", "media_type", "media_product_type", "permalink", "timestamp"):
                    if key in fresh:
                        merged[key] = fresh[key]
                insights = client.get_instagram_media_insights(external_id)
                merged = flatten_instagram_insights(merged, insights)
                # After a successful insights fetch, always persist saves as an int
                # (incl. 0). Meta sometimes omits the field when there are no saves.
                if "saved_count" not in merged:
                    if "insights_saved" in merged:
                        merged["saved_count"] = merged["insights_saved"]
                    elif "insights_views" in merged:
                        merged["saved_count"] = 0
                if "shares_count" not in merged and "insights_views" in merged:
                    merged["shares_count"] = int(merged.get("shares_count") or 0)
            else:
                fresh = client.get_json(external_id, params={"fields": fb_fields})
                merged = _merge_engagement(existing, fresh, FB_ENGAGEMENT_KEYS)
                for key in (
                    "id",
                    "message",
                    "created_time",
                    "permalink_url",
                    "full_picture",
                    "attachments",
                ):
                    if key in fresh:
                        merged[key] = fresh[key]
                insights = client.get_facebook_post_insights(external_id)
                merged = flatten_facebook_insights(merged, insights)
        except MetaClientError as exc:
            failed += 1
            logger.warning(
                "fail id=%s platform=%s: %s",
                post_id,
                plat,
                format_meta_client_error(exc),
            )
            if getattr(exc, "error_code", None) == 190:
                logger.error("Token probleem — stop refresh.")
                break
            continue

        new_raw = json.dumps(merged, ensure_ascii=False)
        if new_raw == (row["raw_json"] or ""):
            skipped += 1
        else:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE posts SET raw_json = ? WHERE id = ?",
                    (new_raw, post_id),
                )
                conn.commit()
            updated += 1

        if idx % 25 == 0 or idx == len(rows):
            logger.info(
                "progress %s/%s updated=%s skipped=%s failed=%s",
                idx,
                len(rows),
                updated,
                skipped,
                failed,
            )

    return updated, skipped, failed


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
