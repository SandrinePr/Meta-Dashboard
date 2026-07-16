"""Resolve display image URLs from post/comment database fields."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from db.database import get_connection

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(__file__).resolve().parents[1] / "data" / "media"

# Exact hosts that are page sites / fixtures, not image CDNs.
_BLOCKED_HOSTS = {
    "example.com",
    "www.example.com",
    "example.org",
    "www.example.org",
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "fb.com",
    "www.fb.com",
    "instagram.com",
    "www.instagram.com",
}


def _is_valid_url(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith("http://") or value.startswith("https://")


def _is_displayable_image_url(value: str | None) -> bool:
    """Return True only for CDN-style image URLs (not page permalinks/fixtures)."""
    if not _is_valid_url(value):
        return False
    assert value is not None
    host = (urlparse(value).hostname or "").lower()
    if host in _BLOCKED_HOSTS:
        return False
    # Reject facebook.com web pages, but allow *.fbcdn.net image hosts.
    if host.endswith("facebook.com") and "fbcdn" not in host:
        return False
    return True


def local_media_path(post_id: int) -> Path | None:
    """Return the first existing cached thumbnail for a post id."""
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        path = MEDIA_DIR / f"post_{post_id}{ext}"
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def local_media_data_uri(post_id: int) -> str | None:
    """Load a cached thumbnail as a data URI for HTML cards."""
    path = local_media_path(post_id)
    if path is None:
        return None
    mime, _ = mimetypes.guess_type(path.name)
    if not mime:
        mime = "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


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
        if _is_displayable_image_url(value):
            found.append((value, f"raw_json.{key}"))

    attachments = payload.get("attachments")
    if isinstance(attachments, dict):
        items = attachments.get("data")
        if isinstance(items, list):
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                media = item.get("media")
                if isinstance(media, dict):
                    image = media.get("image")
                    if isinstance(image, dict):
                        src = image.get("src")
                        if _is_displayable_image_url(src):
                            found.append(
                                (src, f"raw_json.attachments[{idx}].media.image.src")
                            )
                url = item.get("url")
                if _is_displayable_image_url(url):
                    found.append((url, f"raw_json.attachments[{idx}].url"))

    return found


def resolve_display_image_url(
    *,
    thumbnail_url: str | None = None,
    media_url: str | None = None,
    media_type: str | None = None,
    raw_json: str | None = None,
    search_index_thumbnail: str | None = None,
    prefer_thumbnail: bool = False,
) -> tuple[str | None, str]:
    """Pick the first usable image URL and return it with a source label."""
    candidates: list[tuple[str, str]] = []

    if _is_displayable_image_url(search_index_thumbnail):
        candidates.append((search_index_thumbnail, "search_index.thumbnail_url"))

    is_video = (media_type or "").upper() in {"VIDEO", "REELS", "REEL"}
    if prefer_thumbnail or is_video:
        field_order = (
            ("thumbnail_url", thumbnail_url),
            ("media_url", media_url),
        )
    else:
        field_order = (
            ("media_url", media_url),
            ("thumbnail_url", thumbnail_url),
        )
    for field_name, value in field_order:
        if _is_displayable_image_url(value):
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
                SELECT id, platform, thumbnail_url, media_url, media_type, raw_json
                FROM posts
                WHERE id = ?
                """,
                (result.entity_id,),
            ).fetchone()
            post_id = result.entity_id
        else:
            row = conn.execute(
                """
                SELECT
                    p.id AS id,
                    p.platform,
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
            post_id = int(row["id"]) if row else None

    if post_id is not None:
        local_uri = local_media_data_uri(int(post_id))
        if local_uri:
            return local_uri, "local_cache"

    if not row:
        return resolve_display_image_url(search_index_thumbnail=result.thumbnail_url)

    prefer_thumbnail = (row["platform"] or "") == "facebook"
    return resolve_display_image_url(
        search_index_thumbnail=result.thumbnail_url,
        thumbnail_url=row["thumbnail_url"],
        media_url=row["media_url"],
        media_type=row["media_type"],
        raw_json=row["raw_json"],
        prefer_thumbnail=prefer_thumbnail,
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
        for key in (
            "total_views_count",
            "view_count",
            "play_count",
            "video_views",
            "views",
        ):
            views = _safe_int(payload.get(key))
            if views is not None:
                stats["views"] = views
                break
        for key in ("saved_count", "save_count", "saved", "saves"):
            saves = _safe_int(payload.get(key))
            if saves is not None:
                stats["saves"] = saves
                break
        for key in ("shares_count", "share_count"):
            shares = _safe_int(payload.get(key))
            if shares is not None:
                stats["shares"] = shares
                break
    else:
        # Prefer reactions (all emoji reactions) over likes-only when available.
        reactions = _summary_total(payload.get("reactions"))
        likes = _summary_total(payload.get("likes"))
        if reactions is not None:
            stats["likes"] = reactions
        elif likes is not None:
            stats["likes"] = likes
        comments = _summary_total(payload.get("comments"))
        if comments is not None:
            stats["comments"] = comments
        shares = payload.get("shares")
        if isinstance(shares, dict):
            share_count = _safe_int(shares.get("count"))
            if share_count is not None:
                stats["shares"] = share_count
        for key in ("video_views", "view_count", "views"):
            views = _safe_int(payload.get(key))
            if views is not None:
                stats["views"] = views
                break

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
                SELECT id, platform, thumbnail_url, media_url, media_type, raw_json
                FROM posts
                WHERE id = ?
                """,
                (entity_id,),
            ).fetchone()
            if not row:
                return None, "none"
            local_uri = local_media_data_uri(int(row["id"]))
            if local_uri:
                return local_uri, "local_cache"
            return resolve_display_image_url(
                thumbnail_url=row["thumbnail_url"],
                media_url=row["media_url"],
                media_type=row["media_type"],
                raw_json=row["raw_json"],
                prefer_thumbnail=(row["platform"] or "") == "facebook",
            )

        row = conn.execute(
            """
            SELECT
                p.id AS id,
                p.platform,
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
        local_uri = local_media_data_uri(int(row["id"]))
        if local_uri:
            return local_uri, "local_cache"
        return resolve_display_image_url(
            thumbnail_url=row["thumbnail_url"],
            media_url=row["media_url"],
            media_type=row["media_type"],
            raw_json=row["raw_json"],
            prefer_thumbnail=(row["platform"] or "") == "facebook",
        )
