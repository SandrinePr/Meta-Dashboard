"""Resolve display image URLs from post/comment database fields."""

from __future__ import annotations

import json
import logging
from typing import Any

from db.database import get_connection

logger = logging.getLogger(__name__)


def _is_valid_url(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith("http://") or value.startswith("https://")


def _urls_from_raw_json(raw_json: str | None) -> list[tuple[str, str]]:
    if not raw_json:
        return []
    try:
        payload = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    found: list[tuple[str, str]] = []
    for key in ("thumbnail_url", "media_url", "full_picture"):
        value = payload.get(key)
        if _is_valid_url(value):
            found.append((value, f"raw_json.{key}"))

    attachments = payload.get("attachments")
    if isinstance(attachments, dict):
        items = attachments.get("data")
        if isinstance(items, list):
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                if _is_valid_url(url):
                    found.append((url, f"raw_json.attachments[{idx}].url"))
                media = item.get("media")
                if isinstance(media, dict):
                    image = media.get("image")
                    if isinstance(image, dict):
                        src = image.get("src")
                        if _is_valid_url(src):
                            found.append((src, f"raw_json.attachments[{idx}].media.image.src"))

    return found


def resolve_display_image_url(
    *,
    thumbnail_url: str | None = None,
    media_url: str | None = None,
    media_type: str | None = None,
    raw_json: str | None = None,
    search_index_thumbnail: str | None = None,
) -> tuple[str | None, str]:
    """Pick the first usable image URL and return it with a source label."""
    candidates: list[tuple[str, str]] = []

    if _is_valid_url(search_index_thumbnail):
        candidates.append((search_index_thumbnail, "search_index.thumbnail_url"))

    is_video = (media_type or "").upper() == "VIDEO"
    field_order = (
        ("thumbnail_url", thumbnail_url),
        ("media_url", media_url),
    ) if is_video else (
        ("media_url", media_url),
        ("thumbnail_url", thumbnail_url),
    )
    for field_name, value in field_order:
        if _is_valid_url(value):
            candidates.append((value, field_name))

    candidates.extend(_urls_from_raw_json(raw_json))

    seen: set[str] = set()
    for url, source in candidates:
        if url in seen:
            continue
        seen.add(url)
        logger.debug("Using image URL from %s", source)
        return url, source

    logger.debug("No image URL found (media_type=%s)", media_type)
    return None, "none"


def get_image_for_search_result(result) -> tuple[str | None, str]:
    """Resolve the best image URL for a search result card."""
    with get_connection() as conn:
        if result.entity_type == "post":
            row = conn.execute(
                """
                SELECT thumbnail_url, media_url, media_type, raw_json
                FROM posts
                WHERE id = ?
                """,
                (result.entity_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT
                    p.thumbnail_url,
                    p.media_url,
                    p.media_type,
                    p.raw_json
                FROM comments c
                JOIN posts p ON p.id = c.post_id
                WHERE c.id = ?
                """,
                (result.entity_id,),
            ).fetchone()

    if not row:
        return resolve_display_image_url(search_index_thumbnail=result.thumbnail_url)

    return resolve_display_image_url(
        search_index_thumbnail=result.thumbnail_url,
        thumbnail_url=row["thumbnail_url"],
        media_url=row["media_url"],
        media_type=row["media_type"],
        raw_json=row["raw_json"],
    )


def _media_product_type_from_raw(raw_json: str | None) -> str:
    if not raw_json:
        return ""
    try:
        payload = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    value = payload.get("media_product_type") or payload.get("media_type") or ""
    return str(value).upper()


def _count_facebook_images(raw_json: str | None) -> int:
    """Count images/attachments in a Facebook post payload."""
    if not raw_json:
        return 0
    try:
        payload = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0

    count = 0
    attachments = payload.get("attachments")
    data = attachments.get("data") if isinstance(attachments, dict) else None
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            sub = item.get("subattachments")
            sub_data = sub.get("data") if isinstance(sub, dict) else None
            if isinstance(sub_data, list) and sub_data:
                count += len(sub_data)
            else:
                count += 1

    child = payload.get("child_attachments")
    if isinstance(child, list):
        count = max(count, len(child))

    return count


def classify_content_type(
    platform: str,
    *,
    media_type: str | None = None,
    content_type: str | None = None,
    raw_json: str | None = None,
) -> str:
    """Classify a post into a display content type: Post, Reel, or Carousel."""
    media = (media_type or "").upper()
    product = _media_product_type_from_raw(raw_json)

    if platform == "instagram":
        if "REEL" in product or "REEL" in media or media == "VIDEO":
            return "Reel"
        if media == "CAROUSEL_ALBUM" or "CAROUSEL" in media or "CAROUSEL" in product:
            return "Carousel"
        return "Post"

    if _count_facebook_images(raw_json) > 1:
        return "Carousel"
    return "Post"


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _summary_total(node: object) -> int | None:
    if not isinstance(node, dict):
        return None
    summary = node.get("summary")
    if isinstance(summary, dict):
        return _safe_int(summary.get("total_count"))
    return None


def extract_post_stats(platform: str, raw_json: str | None) -> dict[str, int]:
    """Extract engagement stats from a post's raw_json. Missing fields are skipped."""
    if not raw_json:
        return {}
    try:
        payload = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    stats: dict[str, int] = {}
    if platform == "instagram":
        likes = _safe_int(payload.get("like_count"))
        if likes is not None:
            stats["likes"] = likes
        comments = _safe_int(payload.get("comments_count"))
        if comments is not None:
            stats["comments"] = comments
        for key in ("play_count", "view_count", "video_views", "views"):
            views = _safe_int(payload.get(key))
            if views is not None:
                stats["views"] = views
                break
        for key in ("save_count", "saved", "saves"):
            saves = _safe_int(payload.get(key))
            if saves is not None:
                stats["saves"] = saves
                break
    else:
        likes = _summary_total(payload.get("likes"))
        if likes is not None:
            stats["likes"] = likes
        comments = _summary_total(payload.get("comments"))
        if comments is not None:
            stats["comments"] = comments
        shares = payload.get("shares")
        if isinstance(shares, dict):
            share_count = _safe_int(shares.get("count"))
            if share_count is not None:
                stats["shares"] = share_count

    return stats


def extract_comment_stats(raw_json: str | None) -> dict[str, int]:
    """Extract engagement stats from a comment's raw_json (likes only, if any)."""
    if not raw_json:
        return {}
    try:
        payload = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    stats: dict[str, int] = {}
    likes = _safe_int(payload.get("like_count"))
    if likes is not None:
        stats["likes"] = likes
    return stats


def get_stats_for_result(result) -> dict[str, int]:
    """Resolve engagement stats for a post result (comments return no stats)."""
    if result.entity_type != "post":
        return {}
    with get_connection() as conn:
        row = conn.execute(
            "SELECT raw_json FROM posts WHERE id = ?",
            (result.entity_id,),
        ).fetchone()
    if not row:
        return {}
    return extract_post_stats(result.platform, row["raw_json"])


def get_engagement_stats(result) -> dict[str, int]:
    """Resolve engagement stats for any result (posts full, comments own likes)."""
    with get_connection() as conn:
        if result.entity_type == "post":
            row = conn.execute(
                "SELECT raw_json FROM posts WHERE id = ?",
                (result.entity_id,),
            ).fetchone()
            return extract_post_stats(result.platform, row["raw_json"]) if row else {}

        row = conn.execute(
            "SELECT raw_json FROM comments WHERE id = ?",
            (result.entity_id,),
        ).fetchone()
    return extract_comment_stats(row["raw_json"]) if row else {}


def get_comment_parent_post_id(comment_id: int) -> int | None:
    """Return the internal post id a comment belongs to."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT post_id FROM comments WHERE id = ?",
            (comment_id,),
        ).fetchone()
    if not row or row["post_id"] is None:
        return None
    return int(row["post_id"])


def get_comment_parent_caption(comment_id: int) -> str:
    """Return the caption/text of the post a comment belongs to."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT p.text
            FROM comments c
            JOIN posts p ON p.id = c.post_id
            WHERE c.id = ?
            """,
            (comment_id,),
        ).fetchone()
    if not row:
        return ""
    return row["text"] or ""


def get_content_type_for_result(result) -> str:
    """Resolve the display content type (Post/Reel/Carousel) for a result."""
    with get_connection() as conn:
        if result.entity_type == "post":
            row = conn.execute(
                "SELECT media_type, content_type, raw_json FROM posts WHERE id = ?",
                (result.entity_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT p.media_type, p.content_type, p.raw_json
                FROM comments c
                JOIN posts p ON p.id = c.post_id
                WHERE c.id = ?
                """,
                (result.entity_id,),
            ).fetchone()

    if not row:
        return "Post"

    return classify_content_type(
        result.platform,
        media_type=row["media_type"],
        content_type=row["content_type"],
        raw_json=row["raw_json"],
    )


def get_image_for_entity(entity_type: str, entity_id: int) -> tuple[str | None, str]:
    """Load image URL for a search result entity from SQLite."""
    with get_connection() as conn:
        if entity_type == "post":
            row = conn.execute(
                """
                SELECT thumbnail_url, media_url, media_type, raw_json
                FROM posts
                WHERE id = ?
                """,
                (entity_id,),
            ).fetchone()
            if not row:
                return None, "none"
            return resolve_display_image_url(
                thumbnail_url=row["thumbnail_url"],
                media_url=row["media_url"],
                media_type=row["media_type"],
                raw_json=row["raw_json"],
            )

        row = conn.execute(
            """
            SELECT
                p.thumbnail_url,
                p.media_url,
                p.media_type,
                p.raw_json
            FROM comments c
            JOIN posts p ON p.id = c.post_id
            WHERE c.id = ?
            """,
            (entity_id,),
        ).fetchone()
        if not row:
            return None, "none"
        return resolve_display_image_url(
            thumbnail_url=row["thumbnail_url"],
            media_url=row["media_url"],
            media_type=row["media_type"],
            raw_json=row["raw_json"],
        )
