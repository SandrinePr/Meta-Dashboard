"""Competitor account resolve must replace pending placeholders without wiping data."""

from __future__ import annotations

from pathlib import Path

from db.comparison_repository import (
    ROLE_COMPETITOR,
    get_account_by_username,
    upsert_comparison_account,
    upsert_comparison_post,
)
from db.database import get_connection, initialize_database
from sync.comparison import _ensure_competitor_account


def _setup(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "resolve.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)


def test_ensure_replaces_pending_and_keeps_posts(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        pending_id = upsert_comparison_account(
            conn,
            external_id="pending:bestinteriors_official",
            username="bestinteriors_official",
            comparison_role=ROLE_COMPETITOR,
            access_status="pending",
        )
        upsert_comparison_post(
            conn,
            account_id=pending_id,
            external_id="media-1",
            text=None,
            permalink=None,
            media_type="IMAGE",
            media_product_type="FEED",
            published_at="2026-09-01T12:00:00+00:00",
            raw_json="{}",
        )
        conn.commit()

        resolved = _ensure_competitor_account(
            conn,
            username="bestinteriors_official",
            account_payload={
                "id": "17841499999999999",
                "username": "bestinteriors_official",
                "name": "Best Interiors",
                "followers_count": 12345,
            },
        )
        assert resolved == pending_id
        acc = get_account_by_username(conn, "bestinteriors_official")
        assert acc is not None
        assert acc.external_id == "17841499999999999"
        assert acc.access_status == "ok"
        posts = conn.execute(
            "SELECT COUNT(*) AS c FROM posts WHERE account_id = ?",
            (pending_id,),
        ).fetchone()
        assert int(posts["c"]) == 1
        conn.commit()
