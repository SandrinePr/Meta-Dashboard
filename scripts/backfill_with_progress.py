"""One-off resilient backfill: same sync logic as sync.orchestrator, but commits
incrementally (every COMMIT_EVERY items) and prints live progress, so an
interruption never loses more than one batch of work. Does not modify
sync/orchestrator.py or any core sync/search/db logic.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings
from db.database import get_connection, initialize_database
from db.repository import (
    rebuild_search_index,
    sync_post_hashtags,
    upsert_account,
    upsert_comment,
    upsert_post,
    upsert_search_index,
)
from meta.client import MetaClient, MetaClientError, format_meta_client_error
from sync.mappers import (
    normalize_facebook_comment,
    normalize_facebook_post,
    normalize_instagram_comment,
    normalize_instagram_post,
)
from sync.orchestrator import _should_store_post

COMMIT_EVERY = 50
LOOKBACK_DAYS = 548  # ~18 months


def backfill_instagram(client: MetaClient) -> None:
    settings = get_settings()
    ig_account_id = settings.meta_instagram_business_account_id
    page_id = settings.meta_page_id
    if not ig_account_id or not settings.meta_page_access_token:
        print("SKIP instagram: missing config")
        return

    t0 = time.time()
    since_ts = int((datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)).timestamp())
    print(
        f"Instagram: media lijst ophalen vanaf {LOOKBACK_DAYS} dagen geleden...",
        flush=True,
    )
    media_items = client.get_instagram_media(ig_account_id, since=since_ts)
    total = len(media_items)
    print(f"Instagram: {total} media items gevonden ({time.time()-t0:.1f}s)", flush=True)

    with get_connection() as conn:
        account_id = upsert_account(conn, platform="instagram", external_id=ig_account_id, page_id=page_id)
        added = updated = comments_added = comments_updated = 0
        t_loop = time.time()

        for idx, raw_post in enumerate(media_items, start=1):
            post_data = normalize_instagram_post(raw_post)
            if post_data["external_id"] and _should_store_post(post_data["permalink"], post_data["text"]):
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
                added += 1 if result.created else 0
                updated += 0 if result.created else 1

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
                    print(f"  WARN comments mislukt voor {post_data['external_id']}: {format_meta_client_error(exc)}", flush=True)
                    raw_comments = []

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
                    comments_added += 1 if comment_result.created else 0
                    comments_updated += 0 if comment_result.created else 1
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

            if idx % COMMIT_EVERY == 0 or idx == total:
                conn.commit()
                elapsed = time.time() - t_loop
                rate = idx / elapsed if elapsed > 0 else 0
                remaining = (total - idx) / rate if rate > 0 else 0
                print(
                    f"Instagram progress: {idx}/{total} posts "
                    f"(+{added} nieuw/{updated} bijgewerkt, comments +{comments_added}/{comments_updated}) "
                    f"~{remaining/60:.1f} min resterend",
                    flush=True,
                )

    print(f"Instagram klaar in {(time.time()-t0)/60:.1f} min.", flush=True)


def backfill_facebook(client: MetaClient) -> None:
    settings = get_settings()
    page_id = settings.meta_page_id
    if not page_id or not settings.meta_page_access_token:
        print("SKIP facebook: missing config")
        return

    t0 = time.time()
    since_ts = int((datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)).timestamp())
    print(
        f"Facebook: posts lijst ophalen vanaf {LOOKBACK_DAYS} dagen geleden...",
        flush=True,
    )
    posts = client.get_facebook_page_posts(page_id, since=since_ts)
    total = len(posts)
    print(f"Facebook: {total} posts gevonden ({time.time()-t0:.1f}s)", flush=True)

    with get_connection() as conn:
        account_id = upsert_account(conn, platform="facebook", external_id=page_id, page_id=page_id)
        added = updated = comments_added = comments_updated = 0
        t_loop = time.time()

        for idx, raw_post in enumerate(posts, start=1):
            post_data = normalize_facebook_post(raw_post)
            if post_data["external_id"] and _should_store_post(post_data["permalink"], post_data["text"]):
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
                added += 1 if result.created else 0
                updated += 0 if result.created else 1

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
                    print(f"  WARN comments mislukt voor {post_data['external_id']}: {format_meta_client_error(exc)}", flush=True)
                    raw_comments = []

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
                    comments_added += 1 if comment_result.created else 0
                    comments_updated += 0 if comment_result.created else 1
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

            if idx % COMMIT_EVERY == 0 or idx == total:
                conn.commit()
                elapsed = time.time() - t_loop
                rate = idx / elapsed if elapsed > 0 else 0
                remaining = (total - idx) / rate if rate > 0 else 0
                print(
                    f"Facebook progress: {idx}/{total} posts "
                    f"(+{added} nieuw/{updated} bijgewerkt, comments +{comments_added}/{comments_updated}) "
                    f"~{remaining/60:.1f} min resterend",
                    flush=True,
                )

    print(f"Facebook klaar in {(time.time()-t0)/60:.1f} min.", flush=True)


def main() -> None:
    initialize_database()
    client = MetaClient.from_settings()
    backfill_instagram(client)
    backfill_facebook(client)
    with get_connection() as conn:
        rebuild_search_index(conn)
        conn.commit()
    print("BACKFILL_DONE", flush=True)


if __name__ == "__main__":
    main()
