"""Tests for database upserts and search index."""

from __future__ import annotations

from pathlib import Path

from db.database import get_connection, initialize_database
from db.repository import (
    sync_post_hashtags,
    upsert_account,
    upsert_comment,
    upsert_post,
    upsert_search_index,
)
from search.engine import search


def _setup_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)


def test_upsert_post_avoids_duplicates(tmp_path: Path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    with get_connection() as conn:
        account_id = upsert_account(
            conn,
            platform="instagram",
            external_id="ig-account",
        )
        first = upsert_post(
            conn,
            platform="instagram",
            external_id="post-1",
            account_id=account_id,
            content_type="post",
            text="Hello #tag",
            permalink="https://example.com/p/1",
            media_url=None,
            thumbnail_url=None,
            media_type="IMAGE",
            published_at="2026-07-01T10:00:00+00:00",
            raw_json="{}",
        )
        second = upsert_post(
            conn,
            platform="instagram",
            external_id="post-1",
            account_id=account_id,
            content_type="post",
            text="Updated",
            permalink="https://example.com/p/1",
            media_url=None,
            thumbnail_url=None,
            media_type="IMAGE",
            published_at="2026-07-02T10:00:00+00:00",
            raw_json="{}",
        )
        conn.commit()

    assert first.created is True
    assert second.created is False
    assert first.row_id == second.row_id

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM posts").fetchone()["c"]
        assert count == 1


def test_search_index_returns_matching_post(tmp_path: Path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    with get_connection() as conn:
        account_id = upsert_account(conn, platform="facebook", external_id="page-1")
        post = upsert_post(
            conn,
            platform="facebook",
            external_id="fb-post-1",
            account_id=account_id,
            content_type="post",
            text="CULIMAAT special offer",
            permalink="https://facebook.com/post/1",
            media_url=None,
            thumbnail_url="https://example.com/thumb.jpg",
            media_type="photo",
            published_at="2026-07-03T10:00:00+00:00",
            raw_json="{}",
        )
        sync_post_hashtags(conn, post.row_id, ["culimaat"])
        upsert_search_index(
            conn,
            entity_type="post",
            entity_id=post.row_id,
            platform="facebook",
            text="CULIMAAT special offer",
            hashtags=["culimaat"],
            published_at="2026-07-03T10:00:00+00:00",
            permalink="https://facebook.com/post/1",
            thumbnail_url="https://example.com/thumb.jpg",
        )
        comment = upsert_comment(
            conn,
            platform="facebook",
            external_id="comment-1",
            post_id=post.row_id,
            text="Nice CULIMAAT post",
            author_name="Alice",
            author_id=None,
            published_at="2026-07-03T11:00:00+00:00",
            raw_json="{}",
        )
        upsert_search_index(
            conn,
            entity_type="comment",
            entity_id=comment.row_id,
            platform="facebook",
            text="Nice CULIMAAT post",
            hashtags=[],
            published_at="2026-07-03T11:00:00+00:00",
            permalink="https://facebook.com/post/1",
            thumbnail_url="https://example.com/thumb.jpg",
        )
        conn.commit()

    results = search("CULIMAAT")
    assert len(results) == 2
    assert results[0].published_at >= results[1].published_at
    assert {result.entity_type for result in results} == {"post", "comment"}


def test_get_recent_posts_excludes_competitors(tmp_path: Path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    monkeypatch.setenv("META_INSTAGRAM_BUSINESS_ACCOUNT_ID", "own-ig")
    monkeypatch.setenv("META_PAGE_ID", "")
    from search.engine import get_recent_posts

    with get_connection() as conn:
        own_id = upsert_account(
            conn, platform="instagram", external_id="own-ig", username="own.brand"
        )
        competitor_id = upsert_account(
            conn,
            platform="instagram",
            external_id="comp-ig",
            username="competitor.brand",
        )
        conn.execute(
            "UPDATE accounts SET comparison_role = 'own' WHERE id = ?",
            (own_id,),
        )
        conn.execute(
            "UPDATE accounts SET comparison_role = 'competitor' WHERE id = ?",
            (competitor_id,),
        )
        upsert_post(
            conn,
            platform="instagram",
            external_id="own-post",
            account_id=own_id,
            content_type="post",
            text="Eigen caption met tekst",
            permalink="https://example.com/own",
            media_url="https://scontent.cdninstagram.com/v/t51/own.jpg",
            thumbnail_url=None,
            media_type="IMAGE",
            published_at="2026-10-15T12:00:00+00:00",
            raw_json="{}",
        )
        upsert_post(
            conn,
            platform="instagram",
            external_id="comp-post",
            account_id=competitor_id,
            content_type="post",
            text=None,
            permalink="https://example.com/comp",
            media_url=None,
            thumbnail_url=None,
            media_type="IMAGE",
            published_at="2026-10-16T12:00:00+00:00",
            raw_json="{}",
        )
        conn.commit()

    recent = get_recent_posts(platforms={"instagram"}, limit=20)
    assert len(recent) == 1
    assert recent[0].text == "Eigen caption met tekst"
    assert recent[0].entity_id is not None
