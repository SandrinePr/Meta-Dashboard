"""Tests for monthly performance analytics."""

from __future__ import annotations

import json
from pathlib import Path

from analytics.performance import (
    format_month_label,
    get_available_months,
    get_monthly_top_posts,
)
from db.database import get_connection, initialize_database
from db.repository import upsert_account, upsert_post


def _setup_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)


def _insert_post(
    conn,
    *,
    external_id: str,
    published_at: str,
    raw_json: dict,
    platform: str = "instagram",
) -> int:
    account_id = upsert_account(conn, platform=platform, external_id=f"{platform}-acct")
    result = upsert_post(
        conn,
        platform=platform,
        external_id=external_id,
        account_id=account_id,
        content_type="post",
        text=f"Post {external_id}",
        permalink=f"https://example.com/{external_id}",
        media_url=None,
        thumbnail_url=None,
        media_type="IMAGE",
        published_at=published_at,
        raw_json=json.dumps(raw_json),
    )
    return result.row_id


def test_format_month_label_dutch() -> None:
    assert format_month_label(2026, 8) == "augustus 2026"


def test_get_monthly_top_posts_returns_top_three_likes(tmp_path: Path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    with get_connection() as conn:
        for index, likes in enumerate([10, 50, 30, 40, 5], start=1):
            _insert_post(
                conn,
                external_id=f"post-{index}",
                published_at=f"2026-03-{index:02d}T12:00:00+00:00",
                raw_json={"like_count": likes, "comments_count": 1},
            )
        conn.commit()

    ranked = get_monthly_top_posts(2026, 3, platforms={"instagram"}, limit=3)
    top_likes = ranked["likes"]

    assert [post.metric_value for post in top_likes] == [50, 40, 30]
    assert all(post.metric == "likes" for post in top_likes)


def test_get_monthly_top_posts_excludes_unavailable(tmp_path: Path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    with get_connection() as conn:
        _insert_post(
            conn,
            external_id="live",
            published_at="2026-04-10T12:00:00+00:00",
            raw_json={"like_count": 12},
        )
        _insert_post(
            conn,
            external_id="gone",
            published_at="2026-04-11T12:00:00+00:00",
            raw_json={"like_count": 999, "rro_unavailable": True},
        )
        conn.commit()

    ranked = get_monthly_top_posts(2026, 4, platforms={"instagram"}, limit=3)

    assert ranked["likes"][0].metric_value == 12
    assert len(ranked["likes"]) == 1


def test_get_monthly_top_posts_skips_missing_metrics(tmp_path: Path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    with get_connection() as conn:
        _insert_post(
            conn,
            external_id="no-views",
            published_at="2026-05-01T12:00:00+00:00",
            raw_json={"like_count": 3},
        )
        _insert_post(
            conn,
            external_id="with-views",
            published_at="2026-05-02T12:00:00+00:00",
            raw_json={"like_count": 2, "insights_views": 100},
        )
        conn.commit()

    ranked = get_monthly_top_posts(2026, 5, platforms={"instagram"}, limit=3)

    assert [post.metric_value for post in ranked["views"]] == [100]
    assert len(ranked["views"]) == 1


def test_get_available_months_newest_first(tmp_path: Path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    with get_connection() as conn:
        _insert_post(
            conn,
            external_id="m1",
            published_at="2026-01-15T12:00:00+00:00",
            raw_json={"like_count": 1},
        )
        _insert_post(
            conn,
            external_id="m2",
            published_at="2026-03-15T12:00:00+00:00",
            raw_json={"like_count": 2},
        )
        conn.commit()

    assert get_available_months() == [(2026, 3), (2026, 1)]
