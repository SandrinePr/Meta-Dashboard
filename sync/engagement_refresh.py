"""Refresh Meta engagement and view insights on stored posts."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from config import get_settings
from db.database import get_connection
from db.repository import mark_post_unavailable, merge_post_raw_json
from meta.availability import is_unavailable_meta_error
from meta.client import MetaClient, MetaClientError, format_meta_client_error
from meta.endpoints import (
    FACEBOOK_POST_FIELDS,
    INSTAGRAM_MEDIA_FIELDS,
    as_fields_param,
)
from meta.insights import flatten_facebook_insights, flatten_instagram_insights
from sync.orchestrator import _apply_instagram_insight_defaults

IG_ENGAGEMENT_KEYS = (
    "like_count",
    "comments_count",
    "saved_count",
    "shares_count",
)
FB_ENGAGEMENT_KEYS = ("likes", "reactions", "comments", "shares", "video_views", "view_count")

IG_FIELDS = as_fields_param(INSTAGRAM_MEDIA_FIELDS)
FB_FIELDS = as_fields_param(FACEBOOK_POST_FIELDS)

# Parallel Meta insight fetches (each post is independent).
_REFRESH_WORKERS = 8
_thread_clients = threading.local()


@dataclass(slots=True)
class RefreshInsightsStats:
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    token_expired: bool = False


def _merge_engagement(existing: dict, fresh: dict, keys: tuple[str, ...]) -> dict:
    merged = dict(existing)
    for key in keys:
        if key in fresh:
            merged[key] = fresh[key]
    return merged


def _apply_instagram_insight_fields(merged: dict) -> dict:
    enriched = _apply_instagram_insight_defaults(merged)
    if "saved_count" not in enriched:
        if "insights_saved" in enriched:
            enriched["saved_count"] = enriched["insights_saved"]
        elif "insights_views" in enriched:
            enriched["saved_count"] = 0
    if "shares_count" not in enriched and "insights_views" in enriched:
        enriched["shares_count"] = int(enriched.get("shares_count") or 0)
    return enriched


def _thread_client() -> MetaClient:
    client = getattr(_thread_clients, "client", None)
    if client is None:
        client = MetaClient.from_settings()
        _thread_clients.client = client
    return client


def refresh_post_insights(
    client: MetaClient,
    *,
    post_id: int,
    platform: str,
    external_id: str,
    raw_json: str | None,
    insights_only: bool = False,
) -> tuple[str | None, MetaClientError | None]:
    """Fetch fresh insights for one post. Returns merged raw_json or an error."""
    try:
        existing = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        existing = {}
    if not isinstance(existing, dict):
        existing = {}

    try:
        if platform == "instagram":
            if insights_only:
                merged = dict(existing)
            else:
                fresh = client.get_json(external_id, params={"fields": IG_FIELDS})
                merged = _merge_engagement(existing, fresh, IG_ENGAGEMENT_KEYS)
                for key in (
                    "id",
                    "caption",
                    "media_type",
                    "media_product_type",
                    "permalink",
                    "timestamp",
                ):
                    if key in fresh:
                        merged[key] = fresh[key]
            insights = client.get_instagram_media_insights(external_id)
            merged = _apply_instagram_insight_fields(
                flatten_instagram_insights(merged, insights)
            )
        elif platform == "facebook":
            if insights_only:
                merged = dict(existing)
            else:
                fresh = client.get_json(external_id, params={"fields": FB_FIELDS})
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
        else:
            return None, None
    except MetaClientError as exc:
        return None, exc

    new_raw = merge_post_raw_json(
        raw_json,
        json.dumps(merged, ensure_ascii=False),
        clear_unavailable=True,
    )
    return new_raw, None


def _fetch_row_result(
    row: dict,
    *,
    insights_only: bool,
) -> tuple[dict, str | None, MetaClientError | None]:
    """Fetch insights for one row using a thread-local Meta client."""
    new_raw, error = refresh_post_insights(
        _thread_client(),
        post_id=int(row["id"]),
        platform=row["platform"],
        external_id=row["external_id"],
        raw_json=row["raw_json"],
        insights_only=insights_only,
    )
    return row, new_raw, error


def refresh_post_rows(
    rows: list[dict],
    *,
    client: MetaClient | None = None,
    insights_only: bool = False,
    max_workers: int = _REFRESH_WORKERS,
) -> RefreshInsightsStats:
    """Refresh insights for database post rows (id, platform, external_id, raw_json)."""
    stats = RefreshInsightsStats()
    if not rows:
        return stats

    # Single-threaded path when a client is injected (tests) or tiny batches.
    if client is not None or len(rows) == 1 or max_workers <= 1:
        meta_client = client or MetaClient.from_settings()
        for row in rows:
            post_id = int(row["id"])
            new_raw, error = refresh_post_insights(
                meta_client,
                post_id=post_id,
                platform=row["platform"],
                external_id=row["external_id"],
                raw_json=row["raw_json"],
                insights_only=insights_only,
            )
            if error is not None:
                if is_unavailable_meta_error(error):
                    mark_post_unavailable_in_conn(post_id, format_meta_client_error(error))
                stats.failed += 1
                if getattr(error, "error_code", None) == 190:
                    stats.token_expired = True
                    break
                continue
            if new_raw is None or new_raw == (row["raw_json"] or ""):
                stats.skipped += 1
                continue
            with get_connection() as conn:
                conn.execute(
                    "UPDATE posts SET raw_json = ? WHERE id = ?",
                    (new_raw, post_id),
                )
                conn.commit()
            stats.updated += 1
        return stats

    workers = min(max_workers, len(rows))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_fetch_row_result, row, insights_only=insights_only)
            for row in rows
        ]
        for future in as_completed(futures):
            row, new_raw, error = future.result()
            post_id = int(row["id"])
            if error is not None:
                if is_unavailable_meta_error(error):
                    mark_post_unavailable_in_conn(post_id, format_meta_client_error(error))
                stats.failed += 1
                if getattr(error, "error_code", None) == 190:
                    stats.token_expired = True
                    # Cancel remaining work once the token is dead.
                    for pending in futures:
                        pending.cancel()
                    break
                continue
            if new_raw is None or new_raw == (row["raw_json"] or ""):
                stats.skipped += 1
                continue
            with get_connection() as conn:
                conn.execute(
                    "UPDATE posts SET raw_json = ? WHERE id = ?",
                    (new_raw, post_id),
                )
                conn.commit()
            stats.updated += 1

    return stats


def mark_post_unavailable_in_conn(post_id: int, reason: str) -> None:
    with get_connection() as conn:
        mark_post_unavailable(conn, post_id, reason=reason)
        conn.commit()


def refresh_month_insights(
    year: int,
    month: int,
    *,
    platforms: set[str] | None = None,
    insights_only: bool = False,
) -> RefreshInsightsStats:
    """Refresh insights for all posts published in a calendar month."""
    settings = get_settings()
    if not settings.meta_page_access_token:
        return RefreshInsightsStats()

    ym = f"{year:04d}-{month:02d}"
    query = """
        SELECT id, platform, external_id, raw_json
        FROM posts
        WHERE strftime('%Y-%m', published_at) = ?
    """
    params: list[object] = [ym]
    if platforms:
        placeholders = ", ".join("?" for _ in platforms)
        query += f" AND platform IN ({placeholders})"
        params.extend(sorted(platforms))

    with get_connection() as conn:
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]

    try:
        return refresh_post_rows(rows, insights_only=insights_only)
    except MetaClientError:
        return RefreshInsightsStats(failed=len(rows))
