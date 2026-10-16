"""Business Discovery cost/resume optimizations — no live Meta calls."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from db.comparison_repository import (
    ROLE_COMPETITOR,
    get_discovery_checkpoint,
    list_known_post_external_ids,
    log_discovery_request,
    save_discovery_checkpoint,
    upsert_comparison_account,
    upsert_comparison_post,
)
from db.database import get_connection, initialize_database
from meta.client import MetaRequestMeta
from meta.discovery import (
    ACCOUNT_FIELDS,
    MEDIA_FIELDS,
    MEDIA_PAGE_LIMIT,
    business_discovery_fields,
    configured_media_page_limit,
    iter_discovery_media_pages,
)


def _setup(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "discovery.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("DISCOVERY_MEDIA_PAGE_SIZE", "25")
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    return db_path


def test_discovery_fields_omit_unused_costly_fields() -> None:
    assert "caption" not in MEDIA_FIELDS
    assert "media_count" not in ACCOUNT_FIELDS
    assert "id" in MEDIA_FIELDS
    assert "like_count" in MEDIA_FIELDS
    assert "comments_count" in MEDIA_FIELDS
    assert "media_product_type" in MEDIA_FIELDS
    assert "permalink" in MEDIA_FIELDS
    assert "followers_count" in ACCOUNT_FIELDS
    query = business_discovery_fields("hoog.design", media_limit=25)
    assert "caption" not in query
    assert "media_count" not in query
    assert "media.limit(25)" in query


def test_account_fields_skipped_on_follow_up_pages() -> None:
    first = business_discovery_fields("x", include_account=True, include_media=True)
    later = business_discovery_fields(
        "x", after="CURSOR", include_account=False, include_media=True
    )
    assert "followers_count" in first
    assert "followers_count" not in later
    assert "media.limit(25).after(CURSOR)" in later


def test_page_size_configurable(monkeypatch) -> None:
    assert MEDIA_PAGE_LIMIT == 25
    monkeypatch.setenv("DISCOVERY_MEDIA_PAGE_SIZE", "25")
    # Clear settings cache if any — get_settings reads env each call.
    assert configured_media_page_limit() == 25
    monkeypatch.setenv("DISCOVERY_MEDIA_PAGE_SIZE", "10")
    assert configured_media_page_limit() == 10


def test_checkpoint_and_request_log_persist(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        account_id = upsert_comparison_account(
            conn,
            external_id="ig-comp-1",
            username="bestinteriors_official",
            comparison_role=ROLE_COMPETITOR,
            access_status="ok",
        )
        save_discovery_checkpoint(
            conn,
            account_id=account_id,
            username="bestinteriors_official",
            period_start="2026-07-14T00:00:00+00:00",
            period_end="2026-10-14T23:59:59+00:00",
            cursor_after="CURSOR123",
            pages_fetched=3,
            posts_fetched=70,
            is_complete=False,
            last_fetch_run_id=None,
            account_payload={"id": "ig-comp-1", "username": "bestinteriors_official"},
        )
        log_discovery_request(
            conn,
            fetch_run_id=None,
            account_id=account_id,
            username="bestinteriors_official",
            page_index=3,
            page_size=25,
            posts_received=25,
            duration_ms=812.5,
            usage_headers={"x-app-usage": {"call_count": 7, "total_time": 40}},
            cursor_before="CURSOR0",
            cursor_after="CURSOR123",
            status="ok",
            measured_at="2026-10-14T12:00:00+00:00",
        )
        conn.commit()

    with get_connection() as conn:
        cp = get_discovery_checkpoint(conn, account_id)
        assert cp is not None
        assert cp.cursor_after == "CURSOR123"
        assert cp.pages_fetched == 3
        assert cp.is_complete is False
        row = conn.execute(
            "SELECT page_size, posts_received, duration_ms, usage_json FROM "
            "comparison_discovery_request_log WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        assert row["page_size"] == 25
        assert row["posts_received"] == 25
        assert row["duration_ms"] == 812.5
        assert "total_time" in (row["usage_json"] or "")


def test_dedupe_seen_ids_across_overlapping_pages(monkeypatch) -> None:
    cutoff = datetime(2026, 7, 14, tzinfo=timezone.utc)
    pages = [
        {
            "id": "acc-1",
            "username": "demo",
            "followers_count": 10,
            "media": {
                "data": [
                    {
                        "id": "p1",
                        "timestamp": "2026-09-01T12:00:00+00:00",
                        "like_count": 1,
                        "comments_count": 0,
                        "media_type": "IMAGE",
                    },
                    {
                        "id": "p2",
                        "timestamp": "2026-08-01T12:00:00+00:00",
                        "like_count": 2,
                        "comments_count": 0,
                        "media_type": "IMAGE",
                    },
                ],
                "paging": {"cursors": {"after": "C2"}},
            },
        },
        {
            "media": {
                "data": [
                    # Overlap with previous page boundary.
                    {
                        "id": "p2",
                        "timestamp": "2026-08-01T12:00:00+00:00",
                        "like_count": 2,
                        "comments_count": 0,
                        "media_type": "IMAGE",
                    },
                    {
                        "id": "p3",
                        "timestamp": "2026-06-01T12:00:00+00:00",
                        "like_count": 3,
                        "comments_count": 0,
                        "media_type": "IMAGE",
                    },
                ],
                "paging": {},
            },
        },
    ]
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def get_json_with_meta(self, endpoint, *, params=None, **kwargs):
            calls.append(params or {})
            idx = len(calls) - 1
            payload = {"business_discovery": pages[idx]}
            meta = MetaRequestMeta(duration_ms=10.0, usage_headers={"x-app-usage": {"call_count": 1}})
            return payload, meta

    results = list(
        iter_discovery_media_pages(
            FakeClient(),  # type: ignore[arg-type]
            "host",
            "demo",
            cutoff_utc=cutoff,
            media_limit=25,
        )
    )
    assert len(results) == 2
    # Second page skips duplicate p2; p3 is before cutoff so not in_period.
    assert [p["id"] for p in results[0].posts_in_period] == ["p1", "p2"]
    assert [p["id"] for p in results[1].posts_in_period] == []
    assert results[1].reached_cutoff is True
    # First request includes account fields; second must not re-request them.
    assert "followers_count" in calls[0]["fields"]
    assert "followers_count" not in calls[1]["fields"]


def test_known_post_ids_support_resume_dedupe(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        account_id = upsert_comparison_account(
            conn,
            external_id="ig-2",
            username="vtwonen",
            comparison_role=ROLE_COMPETITOR,
        )
        upsert_comparison_post(
            conn,
            account_id=account_id,
            external_id="post-a",
            text=None,
            permalink=None,
            media_type="IMAGE",
            media_product_type="FEED",
            published_at="2026-09-01T00:00:00+00:00",
            raw_json="{}",
        )
        conn.commit()
        known = list_known_post_external_ids(conn, account_id)
    assert known == {"post-a"}


def test_fresh_discovery_remeasures_known_posts_resume_still_dedupes() -> None:
    """Daily refresh must keep known posts; resume may skip already-seen IDs."""
    cutoff = datetime(2026, 7, 14, tzinfo=timezone.utc)
    payload = {
        "id": "ig",
        "username": "demo",
        "followers_count": 10,
        "media": {
            "data": [
                {
                    "id": "known-1",
                    "timestamp": "2026-09-01T12:00:00+0000",
                    "like_count": 9,
                    "comments_count": 1,
                }
            ],
            "paging": {},
        },
    }

    class FakeClient:
        def get_json_with_meta(self, endpoint, *, params=None, **kwargs):
            meta = MetaRequestMeta(duration_ms=1.0, usage_headers={})
            return {"business_discovery": payload}, meta

    fresh = list(
        iter_discovery_media_pages(
            FakeClient(),  # type: ignore[arg-type]
            "host",
            "demo",
            cutoff_utc=cutoff,
            known_seen_ids=None,
        )
    )
    assert [p["id"] for p in fresh[0].posts_in_period] == ["known-1"]

    resume = list(
        iter_discovery_media_pages(
            FakeClient(),  # type: ignore[arg-type]
            "host",
            "demo",
            cutoff_utc=cutoff,
            known_seen_ids={"known-1"},
        )
    )
    assert resume[0].posts_in_period == []
