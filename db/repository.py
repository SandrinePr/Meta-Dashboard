"""Database upsert and search-index helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class UpsertResult:
    row_id: int
    created: bool


def upsert_account(
    conn: sqlite3.Connection,
    *,
    platform: str,
    external_id: str,
    name: str | None = None,
    username: str | None = None,
    page_id: str | None = None,
) -> int:
    """Insert or update an account and return its internal id."""
    existing = conn.execute(
        "SELECT id FROM accounts WHERE platform = ? AND external_id = ?",
        (platform, external_id),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE accounts
            SET name = COALESCE(?, name),
                username = COALESCE(?, username),
                page_id = COALESCE(?, page_id)
            WHERE id = ?
            """,
            (name, username, page_id, existing["id"]),
        )
        return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO accounts (platform, external_id, name, username, page_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (platform, external_id, name, username, page_id),
    )
    return int(cursor.lastrowid)


def upsert_post(
    conn: sqlite3.Connection,
    *,
    platform: str,
    external_id: str,
    account_id: int,
    content_type: str,
    text: str | None,
    permalink: str | None,
    media_url: str | None,
    thumbnail_url: str | None,
    media_type: str | None,
    published_at: str,
    raw_json: str | None,
) -> UpsertResult:
    """Insert or update a post."""
    existing = conn.execute(
        "SELECT id FROM posts WHERE platform = ? AND external_id = ?",
        (platform, external_id),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE posts
            SET account_id = ?,
                content_type = ?,
                text = ?,
                permalink = ?,
                media_url = ?,
                thumbnail_url = ?,
                media_type = ?,
                published_at = ?,
                raw_json = ?,
                last_synced_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                account_id,
                content_type,
                text,
                permalink,
                media_url,
                thumbnail_url,
                media_type,
                published_at,
                raw_json,
                existing["id"],
            ),
        )
        return UpsertResult(row_id=int(existing["id"]), created=False)

    cursor = conn.execute(
        """
        INSERT INTO posts (
            platform, external_id, account_id, content_type, text, permalink,
            media_url, thumbnail_url, media_type, published_at, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            platform,
            external_id,
            account_id,
            content_type,
            text,
            permalink,
            media_url,
            thumbnail_url,
            media_type,
            published_at,
            raw_json,
        ),
    )
    return UpsertResult(row_id=int(cursor.lastrowid), created=True)


def upsert_comment(
    conn: sqlite3.Connection,
    *,
    platform: str,
    external_id: str,
    post_id: int,
    text: str,
    author_name: str | None,
    author_id: str | None,
    published_at: str,
    raw_json: str | None,
) -> UpsertResult:
    """Insert or update a top-level comment."""
    existing = conn.execute(
        "SELECT id FROM comments WHERE platform = ? AND external_id = ?",
        (platform, external_id),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE comments
            SET post_id = ?,
                text = ?,
                author_name = ?,
                author_id = ?,
                published_at = ?,
                raw_json = ?
            WHERE id = ?
            """,
            (post_id, text, author_name, author_id, published_at, raw_json, existing["id"]),
        )
        return UpsertResult(row_id=int(existing["id"]), created=False)

    cursor = conn.execute(
        """
        INSERT INTO comments (
            platform, external_id, post_id, parent_comment_id,
            author_name, author_id, text, published_at, raw_json
        ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (platform, external_id, post_id, author_name, author_id, text, published_at, raw_json),
    )
    return UpsertResult(row_id=int(cursor.lastrowid), created=True)


def sync_post_hashtags(conn: sqlite3.Connection, post_id: int, hashtags: list[str]) -> None:
    """Replace hashtag links for a post."""
    conn.execute("DELETE FROM post_hashtags WHERE post_id = ?", (post_id,))
    for tag in hashtags:
        conn.execute("INSERT OR IGNORE INTO hashtags (tag) VALUES (?)", (tag,))
        row = conn.execute("SELECT id FROM hashtags WHERE tag = ?", (tag,)).fetchone()
        if row:
            conn.execute(
                "INSERT OR IGNORE INTO post_hashtags (post_id, hashtag_id) VALUES (?, ?)",
                (post_id, row["id"]),
            )


def upsert_search_index(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int,
    platform: str,
    text: str | None,
    hashtags: list[str],
    published_at: str,
    permalink: str | None,
    thumbnail_url: str | None,
) -> None:
    """Replace one FTS index row for an entity."""
    conn.execute(
        "DELETE FROM search_index WHERE entity_type = ? AND entity_id = ?",
        (entity_type, str(entity_id)),
    )
    conn.execute(
        """
        INSERT INTO search_index (
            entity_type, entity_id, platform, text, hashtags,
            published_at, permalink, thumbnail_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            str(entity_id),
            platform,
            text or "",
            " ".join(hashtags),
            published_at,
            permalink,
            thumbnail_url,
        ),
    )


def get_post_hashtags(conn: sqlite3.Connection, post_id: int) -> list[str]:
    """Return hashtags linked to a post."""
    rows = conn.execute(
        """
        SELECT h.tag
        FROM hashtags h
        JOIN post_hashtags ph ON ph.hashtag_id = h.id
        WHERE ph.post_id = ?
        ORDER BY h.tag
        """,
        (post_id,),
    ).fetchall()
    return [row["tag"] for row in rows]


def get_post_by_platform_external_id(
    conn: sqlite3.Connection,
    *,
    platform: str,
    external_id: str,
) -> sqlite3.Row | None:
    """Fetch one post row by platform and external id."""
    return conn.execute(
        "SELECT * FROM posts WHERE platform = ? AND external_id = ?",
        (platform, external_id),
    ).fetchone()


def rebuild_search_index(conn: sqlite3.Connection) -> None:
    """Rebuild FTS rows from posts, comments, and linked hashtags."""
    posts = conn.execute(
        """
        SELECT id, platform, text, published_at, permalink, thumbnail_url
        FROM posts
        """
    ).fetchall()
    for post in posts:
        hashtags = get_post_hashtags(conn, int(post["id"]))
        upsert_search_index(
            conn,
            entity_type="post",
            entity_id=int(post["id"]),
            platform=post["platform"],
            text=post["text"],
            hashtags=hashtags,
            published_at=post["published_at"],
            permalink=post["permalink"],
            thumbnail_url=post["thumbnail_url"],
        )

    comments = conn.execute(
        """
        SELECT
            c.id,
            c.platform,
            c.text,
            c.published_at,
            c.post_id,
            p.permalink,
            p.thumbnail_url
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        """
    ).fetchall()
    for comment in comments:
        hashtags = get_post_hashtags(conn, int(comment["post_id"]))
        upsert_search_index(
            conn,
            entity_type="comment",
            entity_id=int(comment["id"]),
            platform=comment["platform"],
            text=comment["text"],
            hashtags=hashtags,
            published_at=comment["published_at"],
            permalink=comment["permalink"],
            thumbnail_url=comment["thumbnail_url"],
        )


def count_records(conn: sqlite3.Connection) -> dict[str, int]:
    """Return simple record counts for sync summary."""
    return {
        "instagram_posts": conn.execute(
            "SELECT COUNT(*) AS c FROM posts WHERE platform = 'instagram'"
        ).fetchone()["c"],
        "instagram_comments": conn.execute(
            "SELECT COUNT(*) AS c FROM comments WHERE platform = 'instagram'"
        ).fetchone()["c"],
        "facebook_posts": conn.execute(
            "SELECT COUNT(*) AS c FROM posts WHERE platform = 'facebook'"
        ).fetchone()["c"],
        "facebook_comments": conn.execute(
            "SELECT COUNT(*) AS c FROM comments WHERE platform = 'facebook'"
        ).fetchone()["c"],
    }
