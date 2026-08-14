"""Detect Meta Graph API errors for deleted or unavailable objects."""

from __future__ import annotations

import json
import sqlite3

from meta.client import MetaClient, MetaClientError, MetaRequestError, format_meta_client_error

_UNAVAILABLE_PHRASES = (
    "does not exist",
    "cannot be loaded due to missing permissions",
    "has been deleted",
    "has been removed",
    "is not available",
    "no longer available",
    "unsupported get request",
    "some of the aliases you requested do not exist",
)


def is_unavailable_meta_error(exc: MetaClientError) -> bool:
    """Return True when Meta indicates the post/media object is gone or inaccessible."""
    if not isinstance(exc, MetaRequestError):
        return False
    if exc.error_code in {10, 190}:
        return False
    if exc.error_subcode == 33:
        return True
    if exc.error_code == 803:
        return True
    if exc.error_code == 100:
        message = str(exc).lower()
        if "pages_read_engagement" in message or "page public content access" in message:
            return False
        return any(
            phrase in message
            for phrase in (
                "does not exist",
                "has been deleted",
                "has been removed",
                "no longer available",
            )
        )
    message = str(exc).lower()
    return any(phrase in message for phrase in _UNAVAILABLE_PHRASES)


def is_post_unavailable(raw_json: str | None) -> bool:
    """Return True when raw_json marks the post as removed on the platform."""
    if not raw_json:
        return False
    try:
        payload = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("rro_unavailable"))


def mark_unavailable_posts(
    client: MetaClient,
    conn: sqlite3.Connection,
    *,
    platform: str | None = None,
) -> int:
    """Probe stored posts via Graph API and flag removed ones. Returns count marked."""
    from db.database import get_connection
    from db.repository import mark_post_unavailable

    sql = "SELECT id, platform, external_id, raw_json FROM posts"
    params: list[object] = []
    if platform:
        sql += " WHERE platform = ?"
        params.append(platform)
    sql += " ORDER BY published_at DESC"
    rows = conn.execute(sql, params).fetchall()

    marked = 0
    for row in rows:
        if is_post_unavailable(row["raw_json"]):
            continue
        try:
            client.get_json(row["external_id"], params={"fields": "id"})
        except MetaClientError as exc:
            if getattr(exc, "error_code", None) == 190:
                break
            if is_unavailable_meta_error(exc):
                with get_connection() as mark_conn:
                    mark_post_unavailable(
                        mark_conn,
                        int(row["id"]),
                        reason=format_meta_client_error(exc),
                    )
                    mark_conn.commit()
                marked += 1
    return marked
