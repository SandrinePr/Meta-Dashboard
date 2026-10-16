"""Tests for social comparison persistence, analytics and UI helpers."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from analytics.comparison import (
    account_comparison_rows,
    content_mix_rows,
    growth_overview,
    growth_rows,
    top_posts,
)
from db.comparison_repository import (
    DEFAULT_COMPETITORS,
    DEFAULT_OWN_EXTERNAL_ID,
    DEFAULT_OWN_USERNAME,
    ROLE_COMPETITOR,
    ROLE_OWN,
    ensure_default_comparison_accounts,
    find_active_fetch_run,
    finish_fetch_run,
    get_preferences,
    mark_inaccessible_accounts,
    save_preferences,
    start_fetch_run,
    upsert_account_measurement,
    upsert_comparison_account,
    upsert_comparison_post,
    upsert_post_measurement,
)
from db.database import get_connection, initialize_database
from ui.comparison import (
    EMPTY_DATA_MESSAGE,
    ERROR_DUPLICATE,
    ERROR_LIMIT,
    INSUFFICIENT_GROWTH,
    MAX_COMPETITORS,
    PAGE_TITLE,
    _fmt_pct,
    _should_show_rate_limit_banner,
    add_competitor,
    remove_competitor,
    resolve_period,
)


def _setup(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "comparison.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    return db_path


def test_page_copy() -> None:
    assert PAGE_TITLE == "Social vergelijking"
    assert EMPTY_DATA_MESSAGE == "Nog geen gegevens opgehaald"
    assert INSUFFICIENT_GROWTH == "Nog onvoldoende meetgeschiedenis"


def test_rate_limit_banner_hides_stale_after_success() -> None:
    class _Rate:
        def __init__(self, active: bool) -> None:
            self.active = active

    class _Sync:
        def __init__(self, rate_limited: bool) -> None:
            self.rate_limited = rate_limited

    # Stale DB row + successful latest run → no banner.
    assert not _should_show_rate_limit_banner(_Rate(False), _Sync(False))
    assert not _should_show_rate_limit_banner(_Rate(False), None)
    # Active Retry-After still shows.
    assert _should_show_rate_limit_banner(_Rate(True), _Sync(False))
    assert _should_show_rate_limit_banner(_Rate(True), None)
    # Latest sync itself hit #4 (unknown reset) → informational banner.
    assert _should_show_rate_limit_banner(_Rate(False), _Sync(True))
    assert _should_show_rate_limit_banner(None, _Sync(True))


def test_competitor_helpers() -> None:
    added, error = add_competitor([], " @Hoog.Design ")
    assert error is None
    assert added == ["hoog.design"]
    dup, error = add_competitor(added, "hoog.design")
    assert error == ERROR_DUPLICATE
    assert remove_competitor(added, "hoog.design") == []


def test_competitor_limit() -> None:
    assert MAX_COMPETITORS == 10
    assert "10" in ERROR_LIMIT
    current = [f"merk-{i}" for i in range(MAX_COMPETITORS)]
    updated, error = add_competitor(current, "extra")
    assert error == ERROR_LIMIT
    assert updated == current


def test_save_preferences_preserves_oversized_list(tmp_path: Path, monkeypatch) -> None:
    import json

    db_path = tmp_path / "prefs.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    oversized = [f"merk-{i}" for i in range(12)]
    with get_connection() as conn:
        # Seed as legacy oversized selection (bypass add helper).
        conn.execute(
            """
            INSERT INTO comparison_preferences (id, own_account_id, competitor_usernames_json, updated_at)
            VALUES (1, NULL, ?, ?)
            """,
            (json.dumps(oversized), "2026-10-15T00:00:00+00:00"),
        )
        conn.commit()
        save_preferences(conn, own_account_id=None, competitors=oversized)
        conn.commit()
        _, loaded = get_preferences(conn)
    assert loaded == oversized

    # From a normal (<=10) list, saving more than 10 is capped.
    with get_connection() as conn:
        save_preferences(
            conn,
            own_account_id=None,
            competitors=[f"kort-{i}" for i in range(4)],
        )
        conn.commit()
        save_preferences(
            conn,
            own_account_id=None,
            competitors=[f"nieuw-{i}" for i in range(12)],
        )
        conn.commit()
        _, capped = get_preferences(conn)
    assert len(capped) == 10
    assert capped == [f"nieuw-{i}" for i in range(10)]


def test_period_presets() -> None:
    today = date(2026, 10, 14)
    assert resolve_period("7d", today=today) == (date(2026, 10, 8), today)
    assert resolve_period("3m", today=today) == (date(2026, 7, 14), today)


def test_defaults_and_inaccessible_flag(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO accounts (platform, external_id, username)
            VALUES ('instagram', '17841433048165203', 'legacy')
            """
        )
        own = ensure_default_comparison_accounts(conn)
        own_id, competitors = get_preferences(conn)
        conn.commit()

    assert own.username == DEFAULT_OWN_USERNAME
    assert own.external_id == DEFAULT_OWN_EXTERNAL_ID
    assert own.comparison_role == ROLE_OWN
    assert own.is_discovery_host is True
    assert own_id == own.account_id
    assert competitors == list(DEFAULT_COMPETITORS)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT access_status FROM accounts WHERE external_id = ?",
            ("17841433048165203",),
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM accounts WHERE external_id = ?",
            ("17841433048165203",),
        ).fetchone()["c"]
    assert row["access_status"] == "inaccessible"
    assert count == 1


def test_measurements_survive_second_run_and_retry(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        own = ensure_default_comparison_accounts(conn)
        run1 = start_fetch_run(
            conn,
            trigger_source="test",
            measured_at="2026-10-01T10:00:00+00:00",
            period_start="2026-07-01T00:00:00+00:00",
            period_end="2026-10-01T23:59:59+00:00",
        )
        upsert_account_measurement(
            conn,
            account_id=own.account_id,
            fetch_run_id=run1,
            measured_at="2026-10-01T10:00:00+00:00",
            followers_count=100,
            media_count=10,
        )
        post = upsert_comparison_post(
            conn,
            account_id=own.account_id,
            external_id="media-1",
            text="hello",
            permalink="https://instagram.com/p/x",
            media_type="IMAGE",
            media_product_type="FEED",
            published_at="2026-09-01T12:00:00+00:00",
            raw_json=json.dumps({"id": "media-1", "like_count": 5}),
        )
        upsert_post_measurement(
            conn,
            post_id=post.row_id,
            fetch_run_id=run1,
            measured_at="2026-10-01T10:00:00+00:00",
            like_count=5,
            comments_count=0,
        )
        # Retry same run should upsert, not duplicate.
        upsert_post_measurement(
            conn,
            post_id=post.row_id,
            fetch_run_id=run1,
            measured_at="2026-10-01T10:00:00+00:00",
            like_count=5,
            comments_count=0,
        )
        finish_fetch_run(conn, run1, status="completed")

        run2 = start_fetch_run(
            conn,
            trigger_source="test",
            measured_at="2026-10-14T10:00:00+00:00",
            period_start="2026-07-14T00:00:00+00:00",
            period_end="2026-10-14T23:59:59+00:00",
        )
        upsert_account_measurement(
            conn,
            account_id=own.account_id,
            fetch_run_id=run2,
            measured_at="2026-10-14T10:00:00+00:00",
            followers_count=120,
            media_count=12,
        )
        upsert_post_measurement(
            conn,
            post_id=post.row_id,
            fetch_run_id=run2,
            measured_at="2026-10-14T10:00:00+00:00",
            like_count=9,
            comments_count=1,
        )
        finish_fetch_run(conn, run2, status="completed")
        conn.commit()

        account_m = conn.execute(
            "SELECT COUNT(*) AS c FROM account_measurements WHERE account_id = ?",
            (own.account_id,),
        ).fetchone()["c"]
        post_m = conn.execute(
            "SELECT COUNT(*) AS c FROM post_measurements WHERE post_id = ?",
            (post.row_id,),
        ).fetchone()["c"]

    assert account_m == 2
    assert post_m == 2

    grow = growth_rows(own_account_id=own.account_id, competitor_usernames=[])
    assert len(grow) == 1
    assert grow[0].insufficient is False
    assert grow[0].followers_delta == 20
    assert grow[0].followers_pct == 20.0
    assert grow[0].comparable_posts == 1
    assert grow[0].likes_delta == 4
    assert grow[0].comments_delta == 1

    overview = growth_overview(own_account_id=own.account_id, competitor_usernames=[])
    assert overview.tracking_since == date(2026, 10, 1)


def test_growth_insufficient_names_missing_day(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        own = ensure_default_comparison_accounts(conn)
        run = start_fetch_run(
            conn,
            trigger_source="test",
            measured_at="2026-10-14T10:00:00+00:00",
            period_start="2026-07-14T00:00:00+00:00",
            period_end="2026-10-14T23:59:59+00:00",
        )
        upsert_account_measurement(
            conn,
            account_id=own.account_id,
            fetch_run_id=run,
            measured_at="2026-10-14T10:00:00+00:00",
            followers_count=100,
            media_count=1,
        )
        finish_fetch_run(conn, run, status="completed")
        conn.commit()

    overview = growth_overview(own_account_id=own.account_id, competitor_usernames=[])
    assert overview.tracking_since == date(2026, 10, 14)
    assert overview.rows[0].insufficient is True
    assert overview.rows[0].followers_end is None
    assert overview.rows[0].followers_delta is None
    assert "Tweede meetdag ontbreekt" in (overview.rows[0].missing_note or "")


def test_fmt_pct_keeps_small_nonzero_visible() -> None:
    assert _fmt_pct(0.004) == "+0,0040%"
    assert _fmt_pct(0.04) == "+0,040%"
    assert _fmt_pct(1.5) == "+1,5%"


def test_missing_metrics_are_not_treated_as_zero(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        own = ensure_default_comparison_accounts(conn)
        run = start_fetch_run(
            conn,
            trigger_source="test",
            measured_at="2026-10-14T10:00:00+00:00",
            period_start="2026-07-14T00:00:00+00:00",
            period_end="2026-10-14T23:59:59+00:00",
        )
        upsert_account_measurement(
            conn,
            account_id=own.account_id,
            fetch_run_id=run,
            measured_at="2026-10-14T10:00:00+00:00",
            followers_count=50,
            media_count=None,
        )
        post_a = upsert_comparison_post(
            conn,
            account_id=own.account_id,
            external_id="a",
            text="a",
            permalink=None,
            media_type="IMAGE",
            media_product_type="FEED",
            published_at="2026-09-01T12:00:00+00:00",
            raw_json="{}",
        )
        post_b = upsert_comparison_post(
            conn,
            account_id=own.account_id,
            external_id="b",
            text="b",
            permalink=None,
            media_type="VIDEO",
            media_product_type="REELS",
            published_at="2026-09-02T12:00:00+00:00",
            raw_json="{}",
        )
        upsert_post_measurement(
            conn,
            post_id=post_a.row_id,
            fetch_run_id=run,
            measured_at="2026-10-14T10:00:00+00:00",
            like_count=10,
            comments_count=2,
        )
        upsert_post_measurement(
            conn,
            post_id=post_b.row_id,
            fetch_run_id=run,
            measured_at="2026-10-14T10:00:00+00:00",
            like_count=None,
            comments_count=0,
        )
        finish_fetch_run(conn, run, status="completed")
        save_preferences(conn, own_account_id=own.account_id, competitors=[])
        conn.commit()

    rows, _, incomplete = account_comparison_rows(
        own_account_id=own.account_id,
        competitor_usernames=[],
        period_start=date(2026, 7, 14),
        period_end=date(2026, 10, 14),
    )
    assert len(rows) == 1
    assert rows[0].likes_total == 10
    assert rows[0].comments_total == 2  # only posts with known comments: a=2, b=0 -> both known
    assert rows[0].posts_with_likes == 1
    assert rows[0].posts_with_comments == 2
    assert incomplete is True

    mix = content_mix_rows(
        own_account_id=own.account_id,
        competitor_usernames=[],
        period_start=date(2026, 7, 14),
        period_end=date(2026, 10, 14),
    )
    assert mix[0].images == 1
    assert mix[0].reels == 1

    ranked, top_incomplete, top_missing = top_posts(
        own_account_id=own.account_id,
        competitor_usernames=[],
        period_start=date(2026, 7, 14),
        period_end=date(2026, 10, 14),
    )
    # Post B lacks likes -> excluded from ranking but marks incomplete.
    assert len(ranked) == 1
    assert ranked[0].interaction == 12
    assert top_incomplete is True
    assert top_missing == []


def test_active_run_lock(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        ensure_default_comparison_accounts(conn)
        run = start_fetch_run(
            conn,
            trigger_source="test",
            measured_at=datetime.now(tz=timezone.utc).isoformat(),
            period_start="2026-07-14T00:00:00+00:00",
            period_end="2026-10-14T23:59:59+00:00",
        )
        active = find_active_fetch_run(conn)
        conn.commit()
    assert active == run


def test_preferences_persist(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        own = ensure_default_comparison_accounts(conn)
        save_preferences(
            conn,
            own_account_id=own.account_id,
            competitors=["hoog.design", "vtwonen"],
        )
        conn.commit()
    with get_connection() as conn:
        own_id, competitors = get_preferences(conn)
    assert own_id is not None
    assert competitors == ["hoog.design", "vtwonen"]
