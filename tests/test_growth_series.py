"""Growth series from stored measurements — no Meta calls."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from analytics.daily_comparison import MISSING_START_MEASUREMENT, NO_SECOND_MEASUREMENT
from analytics.growth_series import build_growth_series
from db.comparison_repository import (
    ROLE_OWN,
    start_fetch_run,
    upsert_account_measurement,
    upsert_comparison_account,
)
from db.database import get_connection, initialize_database

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _setup(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "growth_series.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    return db_path


def _utc(day: date, hour: int = 12) -> str:
    local = datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=AMSTERDAM)
    return local.astimezone(timezone.utc).isoformat()


def test_daily_series_requires_real_start_no_shift(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    day1 = date(2026, 10, 13)
    day2 = date(2026, 10, 15)
    with get_connection() as conn:
        own = upsert_comparison_account(
            conn,
            external_id="ig-gs",
            username="own.gs",
            comparison_role=ROLE_OWN,
            is_discovery_host=True,
        )
        for day, hour, followers in (
            (date(2026, 10, 14), 9, 100),
            (date(2026, 10, 14), 18, 105),
            (date(2026, 10, 15), 10, 110),
        ):
            run = start_fetch_run(
                conn,
                trigger_source="t",
                measured_at=_utc(day, hour),
                period_start="2026-07-01T00:00:00+00:00",
                period_end="2026-10-15T23:59:59+00:00",
            )
            upsert_account_measurement(
                conn,
                account_id=own,
                fetch_run_id=run,
                measured_at=_utc(day, hour),
                followers_count=followers,
                media_count=1,
            )
        conn.commit()
        own_id = own

    # Period starts 13th but first measurement is 14th → not chartable (no silent shift).
    result = build_growth_series(
        own_account_id=own_id,
        competitor_usernames=[],
        mode="daily",
        day1=day1,
        day2=day2,
    )
    assert len(result.series) == 1
    series = result.series[0]
    assert series.chartable is False
    assert series.missing_start is True
    assert series.points == []
    assert result.notes == []

    # With real begin+end days → growth from begin (abs + pct on points).
    ok = build_growth_series(
        own_account_id=own_id,
        competitor_usernames=[],
        mode="daily",
        day1=date(2026, 10, 14),
        day2=date(2026, 10, 15),
    )
    assert ok.series[0].chartable is True
    assert [p.followers for p in ok.series[0].points] == [105, 110]
    assert ok.series[0].points[0].growth_pct == 0.0
    assert abs(ok.series[0].points[1].growth_pct - (5 / 105 * 100)) < 0.01
    assert ok.series[0].points[1].growth_abs == 5.0


def test_missing_start_constant() -> None:
    assert MISSING_START_MEASUREMENT == "Beginmeting ontbreekt"
    assert NO_SECOND_MEASUREMENT == "Nog geen tweede meting"
