"""Tests for daily fetch scheduler helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from db.comparison_repository import finish_fetch_run, start_fetch_run
from db.database import get_connection, initialize_database
from sync.scheduler import MORNING_SOURCE, scheduled_job_ran_today, start_scheduler

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _setup(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "scheduler.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    return db_path


def test_scheduled_job_ran_today_detects_amsterdam_date(
    tmp_path: Path, monkeypatch
) -> None:
    _setup(tmp_path, monkeypatch)
    assert scheduled_job_ran_today(MORNING_SOURCE) is False

    now = datetime.now(tz=timezone.utc)
    with get_connection() as conn:
        run_id = start_fetch_run(
            conn,
            trigger_source=MORNING_SOURCE,
            measured_at=now.isoformat(),
            period_start=now.isoformat(),
            period_end=now.isoformat(),
        )
        finish_fetch_run(conn, run_id, status="completed")
        conn.commit()

    assert scheduled_job_ran_today(MORNING_SOURCE) is True


def test_scheduled_job_ran_today_ignores_yesterday(
    tmp_path: Path, monkeypatch
) -> None:
    _setup(tmp_path, monkeypatch)
    yesterday = datetime.now(tz=AMSTERDAM) - timedelta(days=1)
    measured = yesterday.replace(hour=8, minute=0).astimezone(timezone.utc).isoformat()
    with get_connection() as conn:
        run_id = start_fetch_run(
            conn,
            trigger_source=MORNING_SOURCE,
            measured_at=measured,
            period_start=measured,
            period_end=measured,
        )
        finish_fetch_run(conn, run_id, status="completed")
        conn.commit()

    assert scheduled_job_ran_today(MORNING_SOURCE) is False


def test_start_scheduler_noop_without_render(monkeypatch) -> None:
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    start_scheduler()
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    start_scheduler()
