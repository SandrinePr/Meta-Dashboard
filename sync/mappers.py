"""Map Meta API payloads to normalized sync records."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

HASHTAG_PATTERN = re.compile(r"#([A-Za-z0-9_]+)")


def extract_hashtags(text: str | None) -> list[str]:
    """Extract unique lowercase hashtags from post text."""
    if not text:
        return []
    return sorted({match.group(1).lower() for match in HASHTAG_PATTERN.finditer(text)})


def parse_published_at(value: str | None) -> str:
    """Parse Meta timestamp into ISO 8601 UTC string for SQLite storage."""
    if not value:
        return datetime.now(tz=timezone.utc).isoformat()
    normalized = value.replace("+0000", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(tz=timezone.utc).isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def normalize_instagram_post(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Instagram media payload."""
    text = payload.get("caption")
    return {
        "external_id": str(payload.get("id", "")),
        "text": text,
        "hashtags": extract_hashtags(text),
        "permalink": payload.get("permalink"),
        "media_type": payload.get("media_type"),
        "media_url": payload.get("media_url"),
        "thumbnail_url": payload.get("thumbnail_url"),
        "published_at": parse_published_at(payload.get("timestamp")),
        "raw_json": json.dumps(payload, ensure_ascii=False),
    }


def normalize_facebook_post(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Facebook post payload."""
    text = payload.get("message")
    media_type: str | None = None
    media_url: str | None = None
    thumbnail_url: str | None = payload.get("full_picture")

    attachments = payload.get("attachments")
    if isinstance(attachments, dict):
        items = attachments.get("data")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                media_type = first.get("type")
                media = first.get("media")
                if isinstance(media, dict):
                    image = media.get("image")
                    if isinstance(image, dict):
                        media_url = image.get("src")
                # Do NOT fall back to attachments[].url — that is often a
                # facebook.com page link, not an image file.

    return {
        "external_id": str(payload.get("id", "")),
        "text": text,
        "hashtags": extract_hashtags(text),
        "permalink": payload.get("permalink_url"),
        "media_type": media_type,
        "media_url": media_url,
        "thumbnail_url": thumbnail_url,
        "published_at": parse_published_at(payload.get("created_time")),
        "raw_json": json.dumps(payload, ensure_ascii=False),
    }


def normalize_instagram_comment(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Instagram comment payload."""
    return {
        "external_id": str(payload.get("id", "")),
        "text": payload.get("text") or "",
        "author_name": payload.get("username"),
        "author_id": None,
        "published_at": parse_published_at(payload.get("timestamp")),
        "raw_json": json.dumps(payload, ensure_ascii=False),
    }


def normalize_facebook_comment(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Facebook comment payload."""
    author_name: str | None = None
    author_id: str | None = None
    from_obj = payload.get("from")
    if isinstance(from_obj, dict):
        author_name = from_obj.get("name")
        author_id = from_obj.get("id")
        if author_id is not None:
            author_id = str(author_id)

    return {
        "external_id": str(payload.get("id", "")),
        "text": payload.get("message") or "",
        "author_name": author_name,
        "author_id": author_id,
        "published_at": parse_published_at(payload.get("created_time")),
        "raw_json": json.dumps(payload, ensure_ascii=False),
    }
