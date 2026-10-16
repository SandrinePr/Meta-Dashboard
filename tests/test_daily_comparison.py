"""Daily measurement comparison — SQLite only, no Meta calls."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from analytics.daily_comparison import (
    INSUFFICIENT_DAILY_HISTORY,
    MISSING_START_MEASUREMENT,
    NO_SECOND_MEASUREMENT,
    NOT_MEASURED,
    PUBLISHED_COVERAGE_EXACT,
    PUBLISHED_COVERAGE_MINIMUM,
    PUBLISHED_COVERAGE_UNKNOWN,
    PUBLISHED_POSTS_UNKNOWN,
    build_daily_comparison,
    build_intraday_comparison,
    default_day_pair,
    format_published_posts_cell,
    list_measurement_days,
    summarize_daily_account_deltas,
)
from db.comparison_repository import (
    ROLE_COMPETITOR,
    ROLE_OWN,
    start_fetch_run,
    upsert_account_measurement,
    upsert_account_result,
    upsert_comparison_account,
    upsert_comparison_post,
    upsert_post_measurement,
)
from db.database import get_connection, initialize_database
from ui.comparison import DAILY_NOT_MEASURED

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _setup(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "daily.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    return db_path


def _utc_on_amsterdam_day(day: date, hour: int = 12) -> str:
    local = datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=AMSTERDAM)
    return local.astimezone(timezone.utc).isoformat()


def test_copy_constants() -> None:
    assert NOT_MEASURED == "Niet gemeten"
    assert DAILY_NOT_MEASURED == "Niet gemeten"
    assert PUBLISHED_POSTS_UNKNOWN == "Niet bekend"
    assert "volgende meting" in INSUFFICIENT_DAILY_HISTORY
    assert format_published_posts_cell(0, PUBLISHED_COVERAGE_EXACT) == "0"
    assert format_published_posts_cell(3, PUBLISHED_COVERAGE_MINIMUM) == "Minimaal 3"
    assert format_published_posts_cell(None, PUBLISHED_COVERAGE_UNKNOWN) == "Niet bekend"
    assert NO_SECOND_MEASUREMENT == "Nog geen tweede meting"


def test_insufficient_days_default_pair(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        own = upsert_comparison_account(
            conn,
            external_id="own-1",
            username="own.account",
            comparison_role=ROLE_OWN,
        )
        run = start_fetch_run(
            conn,
            trigger_source="test",
            measured_at=_utc_on_amsterdam_day(date(2026, 10, 14)),
            period_start=_utc_on_amsterdam_day(date(2026, 7, 14)),
            period_end=_utc_on_amsterdam_day(date(2026, 10, 14)),
        )
        upsert_account_measurement(
            conn,
            account_id=own,
            fetch_run_id=run,
            measured_at=_utc_on_amsterdam_day(date(2026, 10, 14)),
            followers_count=100,
            media_count=None,
        )
        conn.commit()
    days = list_measurement_days()
    assert days == [date(2026, 10, 14)]
    assert default_day_pair(days) == (None, None)


def test_daily_followers_and_same_post_deltas(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    day1 = date(2026, 10, 13)
    day2 = date(2026, 10, 14)
    with get_connection() as conn:
        own = upsert_comparison_account(
            conn,
            external_id="own-1",
            username="own.account",
            comparison_role=ROLE_OWN,
        )
        competitor = upsert_comparison_account(
            conn,
            external_id="comp-1",
            username="hoog.design",
            comparison_role=ROLE_COMPETITOR,
        )
        run1 = start_fetch_run(
            conn,
            trigger_source="test",
            measured_at=_utc_on_amsterdam_day(day1, hour=9),
            period_start=_utc_on_amsterdam_day(date(2026, 7, 14)),
            period_end=_utc_on_amsterdam_day(day2),
        )
        run2 = start_fetch_run(
            conn,
            trigger_source="test",
            measured_at=_utc_on_amsterdam_day(day1, hour=18),
            period_start=_utc_on_amsterdam_day(date(2026, 7, 14)),
            period_end=_utc_on_amsterdam_day(day2),
        )
        run3 = start_fetch_run(
            conn,
            trigger_source="test",
            measured_at=_utc_on_amsterdam_day(day2, hour=10),
            period_start=_utc_on_amsterdam_day(date(2026, 7, 14)),
            period_end=_utc_on_amsterdam_day(day2),
        )
        # Two measurements same Amsterdam day → last wins.
        upsert_account_measurement(
            conn,
            account_id=own,
            fetch_run_id=run1,
            measured_at=_utc_on_amsterdam_day(day1, hour=9),
            followers_count=100,
            media_count=None,
        )
        upsert_account_measurement(
            conn,
            account_id=own,
            fetch_run_id=run2,
            measured_at=_utc_on_amsterdam_day(day1, hour=18),
            followers_count=110,
            media_count=None,
        )
        upsert_account_measurement(
            conn,
            account_id=own,
            fetch_run_id=run3,
            measured_at=_utc_on_amsterdam_day(day2, hour=10),
            followers_count=120,
            media_count=None,
        )
        # Competitor only measured on day2 → Niet gemeten on day1.
        upsert_account_measurement(
            conn,
            account_id=competitor,
            fetch_run_id=run3,
            measured_at=_utc_on_amsterdam_day(day2, hour=11),
            followers_count=80000,
            media_count=None,
        )

        shared = upsert_comparison_post(
            conn,
            account_id=own,
            external_id="media-shared",
            text=None,
            permalink="https://instagram.com/p/shared",
            media_type="IMAGE",
            media_product_type="FEED",
            published_at=_utc_on_amsterdam_day(date(2026, 10, 10)),
            raw_json="{}",
        )
        upsert_post_measurement(
            conn,
            post_id=shared.row_id,
            fetch_run_id=run1,
            measured_at=_utc_on_amsterdam_day(day1, hour=9),
            like_count=10,
            comments_count=1,
        )
        upsert_post_measurement(
            conn,
            post_id=shared.row_id,
            fetch_run_id=run2,
            measured_at=_utc_on_amsterdam_day(day1, hour=20),
            like_count=12,
            comments_count=2,
        )
        upsert_post_measurement(
            conn,
            post_id=shared.row_id,
            fetch_run_id=run3,
            measured_at=_utc_on_amsterdam_day(day2, hour=10),
            like_count=20,
            comments_count=5,
        )

        new_post = upsert_comparison_post(
            conn,
            account_id=own,
            external_id="media-new",
            text=None,
            permalink=None,
            media_type="IMAGE",
            media_product_type="FEED",
            published_at=_utc_on_amsterdam_day(day2, hour=8),
            raw_json="{}",
        )
        upsert_post_measurement(
            conn,
            post_id=new_post.row_id,
            fetch_run_id=run3,
            measured_at=_utc_on_amsterdam_day(day2, hour=12),
            like_count=3,
            comments_count=0,
        )
        conn.commit()
        own_id = own

    days = list_measurement_days()
    assert days == [day1, day2]
    assert default_day_pair(days) == (day1, day2)

    result = build_daily_comparison(
        own_account_id=own_id,
        competitor_usernames=["hoog.design"],
        day1=day1,
        day2=day2,
    )
    by_name = {row.display_name: row for row in result.account_rows}
    assert by_name["@own.account"].followers_day1 == 110  # last on day1
    assert by_name["@own.account"].followers_day2 == 120
    assert by_name["@own.account"].followers_delta == 10
    assert by_name["@hoog.design"].followers_day1 is None
    assert by_name["@hoog.design"].followers_day2 == 80000
    assert by_name["@hoog.design"].followers_delta is None
    assert by_name["@hoog.design"].incomplete is True

    assert len(result.comparable_posts) == 1
    post = result.comparable_posts[0]
    assert post.likes_day1 == 12  # last on day1
    assert post.likes_day2 == 20
    assert post.likes_delta == 8
    assert post.comments_delta == 3

    assert len(result.new_posts) == 1
    assert result.new_posts[0].likes == 3
    assert result.incomplete is True
    assert result.warnings

    own_row = by_name["@own.account"]
    assert own_row.comparable_posts == 1
    assert own_row.likes_delta == 8
    assert own_row.comments_delta == 3
    summaries = summarize_daily_account_deltas(result)
    assert summaries[0].missing_note == NO_SECOND_MEASUREMENT or summaries[0].followers_delta is not None
    hoog_summary = next(s for s in summaries if "hoog" in s.display_name)
    assert hoog_summary.insufficient is True
    assert hoog_summary.missing_note == MISSING_START_MEASUREMENT
    own_summary = next(s for s in summaries if "own.account" in s.display_name)
    assert own_summary.published_posts_count == 1
    assert own_summary.published_posts_coverage == PUBLISHED_COVERAGE_MINIMUM
    assert format_published_posts_cell(
        own_summary.published_posts_count, own_summary.published_posts_coverage
    ) == "Minimaal 1"
    assert hoog_summary.published_posts_coverage == PUBLISHED_COVERAGE_UNKNOWN
    assert format_published_posts_cell(
        hoog_summary.published_posts_count, hoog_summary.published_posts_coverage
    ) == PUBLISHED_POSTS_UNKNOWN


def test_intraday_first_vs_last_measurement(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    day = date(2026, 10, 15)
    with get_connection() as conn:
        own = upsert_comparison_account(
            conn,
            external_id="ig-own-intra",
            username="own.account",
            comparison_role=ROLE_OWN,
            is_discovery_host=True,
        )
        run1 = start_fetch_run(
            conn,
            trigger_source="t1",
            measured_at=_utc_on_amsterdam_day(day, hour=9),
            period_start="2026-07-15T00:00:00+00:00",
            period_end="2026-10-15T23:59:59+00:00",
        )
        run2 = start_fetch_run(
            conn,
            trigger_source="t2",
            measured_at=_utc_on_amsterdam_day(day, hour=15),
            period_start="2026-07-15T00:00:00+00:00",
            period_end="2026-10-15T23:59:59+00:00",
        )
        upsert_account_measurement(
            conn,
            account_id=own,
            fetch_run_id=run1,
            measured_at=_utc_on_amsterdam_day(day, hour=9),
            followers_count=100,
            media_count=1,
        )
        upsert_account_measurement(
            conn,
            account_id=own,
            fetch_run_id=run2,
            measured_at=_utc_on_amsterdam_day(day, hour=15),
            followers_count=105,
            media_count=1,
        )
        post = upsert_comparison_post(
            conn,
            account_id=own,
            external_id="p-intra",
            text="x",
            permalink=None,
            media_type="IMAGE",
            media_product_type="FEED",
            published_at="2026-09-01T12:00:00+00:00",
            raw_json="{}",
        )
        upsert_post_measurement(
            conn,
            post_id=post.row_id,
            fetch_run_id=run1,
            measured_at=_utc_on_amsterdam_day(day, hour=9),
            like_count=10,
            comments_count=1,
        )
        upsert_post_measurement(
            conn,
            post_id=post.row_id,
            fetch_run_id=run2,
            measured_at=_utc_on_amsterdam_day(day, hour=15),
            like_count=14,
            comments_count=2,
        )
        # Competitor with only one moment.
        other = upsert_comparison_account(
            conn,
            external_id="ig-other",
            username="other.brand",
            comparison_role=ROLE_COMPETITOR,
        )
        upsert_account_measurement(
            conn,
            account_id=other,
            fetch_run_id=run1,
            measured_at=_utc_on_amsterdam_day(day, hour=9),
            followers_count=50,
            media_count=1,
        )
        conn.commit()
        own_id = own

    result = build_intraday_comparison(
        own_account_id=own_id,
        competitor_usernames=["other.brand"],
        day=day,
    )
    by_name = {row.display_name: row for row in result.account_rows}
    own_row = by_name["@own.account"]
    assert own_row.insufficient is False
    assert own_row.followers_delta == 5
    assert own_row.comparable_posts == 1
    assert own_row.likes_delta == 4
    assert own_row.comments_delta == 1
    assert "09:00" in (own_row.start_measured_at or "") or own_row.start_measured_at
    other_row = by_name["@other.brand"]
    assert other_row.insufficient is True
    assert other_row.missing_note == NO_SECOND_MEASUREMENT
    assert other_row.followers_delta is None
    assert other_row.published_posts_coverage == PUBLISHED_COVERAGE_UNKNOWN
    # Old post published before both moments does not count as geplaatst.
    assert own_row.published_posts_count == 0
    assert own_row.published_posts_coverage == PUBLISHED_COVERAGE_MINIMUM


def test_published_posts_exact_zero_and_excludes_old_discovery(
    tmp_path: Path, monkeypatch
) -> None:
    """published_at between moments; old posts fetched later do not count."""
    _setup(tmp_path, monkeypatch)
    day1 = date(2026, 10, 14)
    day2 = date(2026, 10, 15)
    with get_connection() as conn:
        own = upsert_comparison_account(
            conn,
            external_id="ig-own-pub",
            username="own.pub",
            comparison_role=ROLE_OWN,
            is_discovery_host=True,
        )
        run1 = start_fetch_run(
            conn,
            trigger_source="t1",
            measured_at=_utc_on_amsterdam_day(day1, hour=9),
            period_start="2026-07-14T00:00:00+00:00",
            period_end="2026-10-14T23:59:59+00:00",
        )
        run2 = start_fetch_run(
            conn,
            trigger_source="t2",
            measured_at=_utc_on_amsterdam_day(day2, hour=9),
            period_start="2026-07-15T00:00:00+00:00",
            period_end="2026-10-15T23:59:59+00:00",
        )
        for run, day, hour, complete in (
            (run1, day1, 9, True),
            (run2, day2, 9, True),
        ):
            upsert_account_measurement(
                conn,
                account_id=own,
                fetch_run_id=run,
                measured_at=_utc_on_amsterdam_day(day, hour=hour),
                followers_count=100,
                media_count=2,
            )
            upsert_account_result(
                conn,
                fetch_run_id=run,
                account_id=own,
                status="success",
                posts_fetched=2,
                is_complete=complete,
            )

        # Published long before the window — still discovered on day2.
        old = upsert_comparison_post(
            conn,
            account_id=own,
            external_id="old-before-window",
            text=None,
            permalink=None,
            media_type="IMAGE",
            media_product_type="FEED",
            published_at=_utc_on_amsterdam_day(date(2026, 9, 1)),
            raw_json="{}",
        )
        upsert_post_measurement(
            conn,
            post_id=old.row_id,
            fetch_run_id=run2,
            measured_at=_utc_on_amsterdam_day(day2, hour=9),
            like_count=1,
            comments_count=0,
        )
        conn.commit()
        own_id = own

    result = build_daily_comparison(
        own_account_id=own_id,
        competitor_usernames=[],
        day1=day1,
        day2=day2,
    )
    row = result.account_rows[0]
    assert row.published_posts_count == 0
    assert row.published_posts_coverage == PUBLISHED_COVERAGE_EXACT
    assert format_published_posts_cell(
        row.published_posts_count, row.published_posts_coverage
    ) == "0"
    assert result.new_posts == []

    # Intraday: post published between 09:00 and 15:00 on the same day.
    day = date(2026, 10, 16)
    with get_connection() as conn:
        run_a = start_fetch_run(
            conn,
            trigger_source="intra-a",
            measured_at=_utc_on_amsterdam_day(day, hour=9),
            period_start="2026-07-16T00:00:00+00:00",
            period_end="2026-10-16T23:59:59+00:00",
        )
        run_b = start_fetch_run(
            conn,
            trigger_source="intra-b",
            measured_at=_utc_on_amsterdam_day(day, hour=15),
            period_start="2026-07-16T00:00:00+00:00",
            period_end="2026-10-16T23:59:59+00:00",
        )
        for run, hour in ((run_a, 9), (run_b, 15)):
            upsert_account_measurement(
                conn,
                account_id=own_id,
                fetch_run_id=run,
                measured_at=_utc_on_amsterdam_day(day, hour=hour),
                followers_count=110,
                media_count=3,
            )
            upsert_account_result(
                conn,
                fetch_run_id=run,
                account_id=own_id,
                status="success",
                posts_fetched=3,
                is_complete=True,
            )
        mid = upsert_comparison_post(
            conn,
            account_id=own_id,
            external_id="intra-new",
            text=None,
            permalink=None,
            media_type="IMAGE",
            media_product_type="FEED",
            published_at=_utc_on_amsterdam_day(day, hour=12),
            raw_json="{}",
        )
        upsert_post_measurement(
            conn,
            post_id=mid.row_id,
            fetch_run_id=run_b,
            measured_at=_utc_on_amsterdam_day(day, hour=15),
            like_count=2,
            comments_count=0,
        )
        conn.commit()

    intra = build_intraday_comparison(
        own_account_id=own_id,
        competitor_usernames=[],
        day=day,
    )
    intra_row = intra.account_rows[0]
    assert intra_row.published_posts_count == 1
    assert intra_row.published_posts_coverage == PUBLISHED_COVERAGE_EXACT
    assert intra_row.comparable_posts == 0  # likes/reacties window is separate
