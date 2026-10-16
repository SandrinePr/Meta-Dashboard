"""PDF report builder — SQLite only, no Meta calls."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
from reports.comparison_pdf import ReportSelection, build_comparison_pdf

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _setup(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "pdf.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    return db_path


def _utc(day: date, hour: int = 12) -> str:
    local = datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=AMSTERDAM)
    return local.astimezone(timezone.utc).isoformat()


def _seed(conn) -> tuple[int, str]:
    own = upsert_comparison_account(
        conn,
        external_id="ig-pdf-own",
        username="own.pdf",
        comparison_role=ROLE_OWN,
        is_discovery_host=True,
    )
    comp = upsert_comparison_account(
        conn,
        external_id="ig-pdf-comp",
        username="comp.pdf",
        comparison_role=ROLE_COMPETITOR,
    )
    day1 = date(2026, 10, 14)
    day2 = date(2026, 10, 15)
    for day, hour, account_id, followers in (
        (day1, 10, own, 100),
        (day2, 10, own, 110),
        (day1, 11, comp, 50),
        (day2, 11, comp, 55),
    ):
        run = start_fetch_run(
            conn,
            trigger_source="pdf",
            measured_at=_utc(day, hour),
            period_start="2026-07-01T00:00:00+00:00",
            period_end="2026-10-15T23:59:59+00:00",
        )
        upsert_account_measurement(
            conn,
            account_id=account_id,
            fetch_run_id=run,
            measured_at=_utc(day, hour),
            followers_count=followers,
            media_count=1,
        )
        upsert_account_result(
            conn,
            fetch_run_id=run,
            account_id=account_id,
            status="success",
            posts_fetched=1,
            is_complete=True,
        )
    post = upsert_comparison_post(
        conn,
        account_id=own,
        external_id="media-pdf",
        text="hello",
        permalink="https://instagram.com/p/pdf",
        media_type="IMAGE",
        media_product_type="FEED",
        published_at=_utc(date(2026, 9, 1)),
        raw_json="{}",
    )
    run_last = start_fetch_run(
        conn,
        trigger_source="pdf-post",
        measured_at=_utc(day2, 12),
        period_start="2026-07-01T00:00:00+00:00",
        period_end="2026-10-15T23:59:59+00:00",
    )
    upsert_post_measurement(
        conn,
        post_id=post.row_id,
        fetch_run_id=run_last,
        measured_at=_utc(day2, 12),
        like_count=20,
        comments_count=3,
    )
    conn.commit()
    return own, "comp.pdf"


def test_pdf_score_list_only_and_fingerprint(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        own_id, comp = _seed(conn)

    base = dict(
        include_own=True,
        competitor_usernames=(comp,),
        metrics=("likes", "comments", "interaction"),
        period_start=date(2026, 7, 15),
        period_end=date(2026, 10, 15),
        growth_mode="Per dag",
        growth_day1=date(2026, 10, 14),
        growth_day2=date(2026, 10, 15),
        growth_intraday_day=None,
        include_growth_chart=False,
        chart_metric="followers",
        own_account_id=own_id,
        tracking_since=date(2026, 10, 14),
    )
    score_only = ReportSelection(sections=("score_list",), **base)
    pdf = build_comparison_pdf(score_only)
    assert pdf[:4] == b"%PDF"
    assert score_only.fingerprint() != ReportSelection(
        sections=("performance", "growth"), **base
    ).fingerprint()

    one_account = ReportSelection(
        sections=("performance",),
        include_own=True,
        competitor_usernames=(),
        metrics=("followers", "posts", "likes"),
        period_start=date(2026, 7, 15),
        period_end=date(2026, 10, 15),
        growth_mode="Per dag",
        growth_day1=date(2026, 10, 14),
        growth_day2=date(2026, 10, 15),
        growth_intraday_day=None,
        include_growth_chart=False,
        chart_metric="followers",
        own_account_id=own_id,
        tracking_since=date(2026, 10, 14),
    )
    pdf2 = build_comparison_pdf(one_account)
    assert pdf2[:4] == b"%PDF"

    perf_growth = ReportSelection(sections=("performance", "growth"), **base)
    pdf3 = build_comparison_pdf(perf_growth)
    assert pdf3[:4] == b"%PDF"
    assert len(pdf3) > len(pdf)
