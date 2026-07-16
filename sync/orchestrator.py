"""Sync orchestration from Meta API into SQLite."""

from __future__ import annotations

from dataclasses import dataclass, field

from config import get_settings
from db.database import get_connection, initialize_database
from db.repository import (
    count_records,
    rebuild_search_index,
    sync_post_hashtags,
    upsert_account,
    upsert_comment,
    upsert_post,
    upsert_search_index,
)
from meta.client import MetaClient, MetaClientError, TOKEN_EXPIRED_MESSAGE, format_meta_client_error
from meta.insights import flatten_facebook_insights, flatten_instagram_insights
from sync.mappers import (
    normalize_facebook_comment,
    normalize_facebook_post,
    normalize_instagram_comment,
    normalize_instagram_post,
)


@dataclass(slots=True)
class SyncStats:
    instagram_posts_added: int = 0
    instagram_posts_updated: int = 0
    instagram_comments_added: int = 0
    instagram_comments_updated: int = 0
    facebook_posts_added: int = 0
    facebook_posts_updated: int = 0
    facebook_comments_added: int = 0
    facebook_comments_updated: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "SyncStats") -> None:
        self.instagram_posts_added += other.instagram_posts_added
        self.instagram_posts_updated += other.instagram_posts_updated
        self.instagram_comments_added += other.instagram_comments_added
        self.instagram_comments_updated += other.instagram_comments_updated
        self.facebook_posts_added += other.facebook_posts_added
        self.facebook_posts_updated += other.facebook_posts_updated
        self.facebook_comments_added += other.facebook_comments_added
        self.facebook_comments_updated += other.facebook_comments_updated
        self.errors.extend(other.errors)


def _should_store_post(permalink: str | None, text: str | None) -> bool:
    return bool(permalink or (text and text.strip()))


def _append_meta_sync_error(stats: SyncStats, context: str, exc: MetaClientError) -> None:
    message = format_meta_client_error(exc)
    if message == TOKEN_EXPIRED_MESSAGE:
        stats.errors.append(message)
    else:
        stats.errors.append(f"{context}: {message}")


def sync_instagram(client: MetaClient) -> SyncStats:
    """Sync Instagram media and comments."""
    settings = get_settings()
    stats = SyncStats()
    ig_account_id = settings.meta_instagram_business_account_id
    page_id = settings.meta_page_id

    if not ig_account_id:
        stats.errors.append("META_INSTAGRAM_BUSINESS_ACCOUNT_ID ontbreekt.")
        return stats
    if not settings.meta_page_access_token:
        stats.errors.append("META_PAGE_ACCESS_TOKEN ontbreekt.")
        return stats

    with get_connection() as conn:
        account_id = upsert_account(
            conn,
            platform="instagram",
            external_id=ig_account_id,
            page_id=page_id,
        )

        try:
            media_items = client.get_instagram_media(ig_account_id)
        except MetaClientError as exc:
            _append_meta_sync_error(stats, "Instagram media ophalen mislukt", exc)
            conn.commit()
            return stats

        insights_failures = 0
        insights_stopped = False
        for raw_post in media_items:
            media_id = str(raw_post.get("id") or "")
            if media_id and not insights_stopped:
                try:
                    insights = client.get_instagram_media_insights(media_id)
                    raw_post = flatten_instagram_insights(raw_post, insights)
                except MetaClientError as exc:
                    if getattr(exc, "error_code", None) == 190:
                        insights_stopped = True
                        _append_meta_sync_error(
                            stats,
                            "Instagram insights ophalen gestopt",
                            exc,
                        )
                    else:
                        insights_failures += 1

            post_data = normalize_instagram_post(raw_post)
            if not post_data["external_id"]:
                continue
            if not _should_store_post(post_data["permalink"], post_data["text"]):
                continue

            result = upsert_post(
                conn,
                platform="instagram",
                external_id=post_data["external_id"],
                account_id=account_id,
                content_type="post",
                text=post_data["text"],
                permalink=post_data["permalink"],
                media_url=post_data["media_url"],
                thumbnail_url=post_data["thumbnail_url"],
                media_type=post_data["media_type"],
                published_at=post_data["published_at"],
                raw_json=post_data["raw_json"],
            )
            if result.created:
                stats.instagram_posts_added += 1
            else:
                stats.instagram_posts_updated += 1

            sync_post_hashtags(conn, result.row_id, post_data["hashtags"])
            upsert_search_index(
                conn,
                entity_type="post",
                entity_id=result.row_id,
                platform="instagram",
                text=post_data["text"],
                hashtags=post_data["hashtags"],
                published_at=post_data["published_at"],
                permalink=post_data["permalink"],
                thumbnail_url=post_data["thumbnail_url"],
            )

            try:
                raw_comments = client.get_instagram_comments(post_data["external_id"])
            except MetaClientError as exc:
                _append_meta_sync_error(
                    stats,
                    f"Instagram comments voor media {post_data['external_id']} mislukt",
                    exc,
                )
                continue

            for raw_comment in raw_comments:
                comment_data = normalize_instagram_comment(raw_comment)
                if not comment_data["external_id"] or not comment_data["text"].strip():
                    continue

                comment_result = upsert_comment(
                    conn,
                    platform="instagram",
                    external_id=comment_data["external_id"],
                    post_id=result.row_id,
                    text=comment_data["text"],
                    author_name=comment_data["author_name"],
                    author_id=comment_data["author_id"],
                    published_at=comment_data["published_at"],
                    raw_json=comment_data["raw_json"],
                )
                if comment_result.created:
                    stats.instagram_comments_added += 1
                else:
                    stats.instagram_comments_updated += 1

                upsert_search_index(
                    conn,
                    entity_type="comment",
                    entity_id=comment_result.row_id,
                    platform="instagram",
                    text=comment_data["text"],
                    hashtags=post_data["hashtags"],
                    published_at=comment_data["published_at"],
                    permalink=post_data["permalink"],
                    thumbnail_url=post_data["thumbnail_url"],
                )

        if insights_failures:
            stats.errors.append(
                f"Instagram weergaven (insights) mislukt voor {insights_failures} media-items."
            )

        conn.commit()

    return stats


def sync_facebook(client: MetaClient) -> SyncStats:
    """Sync Facebook page posts and comments."""
    settings = get_settings()
    stats = SyncStats()
    page_id = settings.meta_page_id

    if not page_id:
        stats.errors.append("META_PAGE_ID ontbreekt.")
        return stats
    if not settings.meta_page_access_token:
        stats.errors.append("META_PAGE_ACCESS_TOKEN ontbreekt.")
        return stats

    with get_connection() as conn:
        account_id = upsert_account(
            conn,
            platform="facebook",
            external_id=page_id,
            page_id=page_id,
        )

        try:
            posts = client.get_facebook_page_posts(page_id)
        except MetaClientError as exc:
            _append_meta_sync_error(stats, "Facebook posts ophalen mislukt", exc)
            conn.commit()
            return stats

        insights_failures = 0
        insights_stopped = False
        for raw_post in posts:
            post_id = str(raw_post.get("id") or "")
            if post_id and not insights_stopped:
                try:
                    insights = client.get_facebook_post_insights(post_id)
                    raw_post = flatten_facebook_insights(raw_post, insights)
                except MetaClientError as exc:
                    if getattr(exc, "error_code", None) == 190:
                        insights_stopped = True
                        _append_meta_sync_error(
                            stats,
                            "Facebook insights ophalen gestopt",
                            exc,
                        )
                    else:
                        insights_failures += 1

            post_data = normalize_facebook_post(raw_post)
            if not post_data["external_id"]:
                continue
            if not _should_store_post(post_data["permalink"], post_data["text"]):
                continue

            result = upsert_post(
                conn,
                platform="facebook",
                external_id=post_data["external_id"],
                account_id=account_id,
                content_type="post",
                text=post_data["text"],
                permalink=post_data["permalink"],
                media_url=post_data["media_url"],
                thumbnail_url=post_data["thumbnail_url"],
                media_type=post_data["media_type"],
                published_at=post_data["published_at"],
                raw_json=post_data["raw_json"],
            )
            if result.created:
                stats.facebook_posts_added += 1
            else:
                stats.facebook_posts_updated += 1

            sync_post_hashtags(conn, result.row_id, post_data["hashtags"])
            upsert_search_index(
                conn,
                entity_type="post",
                entity_id=result.row_id,
                platform="facebook",
                text=post_data["text"],
                hashtags=post_data["hashtags"],
                published_at=post_data["published_at"],
                permalink=post_data["permalink"],
                thumbnail_url=post_data["thumbnail_url"],
            )

            try:
                raw_comments = client.get_facebook_comments(post_data["external_id"])
            except MetaClientError as exc:
                _append_meta_sync_error(
                    stats,
                    f"Facebook comments voor post {post_data['external_id']} mislukt",
                    exc,
                )
                continue

            for raw_comment in raw_comments:
                comment_data = normalize_facebook_comment(raw_comment)
                if not comment_data["external_id"] or not comment_data["text"].strip():
                    continue

                comment_result = upsert_comment(
                    conn,
                    platform="facebook",
                    external_id=comment_data["external_id"],
                    post_id=result.row_id,
                    text=comment_data["text"],
                    author_name=comment_data["author_name"],
                    author_id=comment_data["author_id"],
                    published_at=comment_data["published_at"],
                    raw_json=comment_data["raw_json"],
                )
                if comment_result.created:
                    stats.facebook_comments_added += 1
                else:
                    stats.facebook_comments_updated += 1

                upsert_search_index(
                    conn,
                    entity_type="comment",
                    entity_id=comment_result.row_id,
                    platform="facebook",
                    text=comment_data["text"],
                    hashtags=post_data["hashtags"],
                    published_at=comment_data["published_at"],
                    permalink=post_data["permalink"],
                    thumbnail_url=post_data["thumbnail_url"],
                )

        if insights_failures:
            stats.errors.append(
                f"Facebook weergaven (insights) mislukt voor {insights_failures} posts."
            )

        conn.commit()

    return stats


def run_sync(platform: str = "all") -> SyncStats:
    """Run sync for instagram, facebook, or all platforms."""
    initialize_database()
    client = MetaClient.from_settings()
    stats = SyncStats()

    if platform in {"instagram", "all"}:
        stats.merge(sync_instagram(client))
    if platform in {"facebook", "all"}:
        stats.merge(sync_facebook(client))

    with get_connection() as conn:
        rebuild_search_index(conn)
        conn.commit()

    return stats


def get_sync_summary() -> dict[str, int | str | None]:
    """Return synced record counts and last sync timestamp from SQLite."""
    summary: dict[str, int | str | None] = {
        "instagram_posts": 0,
        "instagram_comments": 0,
        "facebook_posts": 0,
        "facebook_comments": 0,
        "last_sync_at": None,
    }
    try:
        with get_connection() as conn:
            summary.update(count_records(conn))
            row = conn.execute(
                "SELECT MAX(last_synced_at) AS last_sync_at FROM posts"
            ).fetchone()
            if row and row["last_sync_at"]:
                summary["last_sync_at"] = row["last_sync_at"]
    except Exception:
        return summary
    return summary
