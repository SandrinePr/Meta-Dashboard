"""Tests for hashtag extraction and search."""

from __future__ import annotations

from pathlib import Path

from db.database import get_connection, initialize_database
from db.repository import (
    rebuild_search_index,
    sync_post_hashtags,
    upsert_account,
    upsert_post,
    upsert_search_index,
)
from search.engine import _build_fts_query, normalize_search_term, search
from sync.mappers import extract_hashtags


def test_extract_hashtags_uses_required_regex() -> None:
    assert extract_hashtags("Hello #Cute #test_123 #yumi") == ["cute", "test_123", "yumi"]


def test_normalize_search_term_strips_hash() -> None:
    assert normalize_search_term("#cute") == "cute"
    assert normalize_search_term("cute") == "cute"
    assert _build_fts_query("#cute") == _build_fts_query("cute")


def test_hashtag_search_matches_with_and_without_hash(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    initialize_database(db_path)

    with get_connection() as conn:
        account_id = upsert_account(conn, platform="instagram", external_id="ig-1")
        post = upsert_post(
            conn,
            platform="instagram",
            external_id="post-cute",
            account_id=account_id,
            content_type="post",
            text="Yumi",
            permalink="https://instagram.com/p/cute",
            media_url=None,
            thumbnail_url=None,
            media_type="IMAGE",
            published_at="2026-07-01T10:00:00+00:00",
            raw_json="{}",
        )
        sync_post_hashtags(conn, post.row_id, extract_hashtags("Yumi #cute"))
        rebuild_search_index(conn)
        conn.commit()

        hashtag_rows = conn.execute("SELECT tag FROM hashtags").fetchall()
        link_rows = conn.execute("SELECT post_id, hashtag_id FROM post_hashtags").fetchall()
        assert [row["tag"] for row in hashtag_rows] == ["cute"]
        assert len(link_rows) == 1

    results_plain = search("cute")
    results_hash = search("#cute")
    assert len(results_plain) == 1
    assert results_plain == results_hash
    assert results_plain[0].hashtags == ["cute"]


def test_hashtag_search_finds_post_when_tag_only_in_hashtags_column(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    initialize_database(db_path)

    with get_connection() as conn:
        account_id = upsert_account(conn, platform="facebook", external_id="fb-1")
        post = upsert_post(
            conn,
            platform="facebook",
            external_id="post-yumi",
            account_id=account_id,
            content_type="post",
            text="Yumi",
            permalink="https://facebook.com/post/yumi",
            media_url=None,
            thumbnail_url=None,
            media_type="photo",
            published_at="2026-07-02T10:00:00+00:00",
            raw_json="{}",
        )
        sync_post_hashtags(conn, post.row_id, ["yumi"])
        upsert_search_index(
            conn,
            entity_type="post",
            entity_id=post.row_id,
            platform="facebook",
            text="Yumi",
            hashtags=["yumi"],
            published_at="2026-07-02T10:00:00+00:00",
            permalink="https://facebook.com/post/yumi",
            thumbnail_url=None,
        )
        conn.commit()

    assert len(search("yumi")) == 1
    assert len(search("#yumi")) == 1
