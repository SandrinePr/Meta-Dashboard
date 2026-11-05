"""Tests for daily fetch scheduler helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from db.comparison_repository import finish_fetch_run, start_fetch_run
from db.database import get_connection, initialize_database
from sync.scheduler import AFTERNOON_SOURCE, scheduled_job_ran_today, start_scheduler

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
    assert scheduled_job_ran_today(AFTERNOON_SOURCE) is False

    now = datetime.now(tz=timezone.utc)
    with get_connection() as conn:
        run_id = start_fetch_run(
            conn,
            trigger_source=AFTERNOON_SOURCE,
            measured_at=now.isoformat(),
            period_start=now.isoformat(),
            period_end=now.isoformat(),
        )
        finish_fetch_run(conn, run_id, status="completed")
        conn.commit()

    assert scheduled_job_ran_today(AFTERNOON_SOURCE) is True


def test_scheduled_job_ran_today_ignores_yesterday(
    tmp_path: Path, monkeypatch
) -> None:
    _setup(tmp_path, monkeypatch)
    yesterday = datetime.now(tz=AMSTERDAM) - timedelta(days=1)
    measured = yesterday.replace(hour=8, minute=0).astimezone(timezone.utc).isoformat()
    with get_connection() as conn:
        run_id = start_fetch_run(
            conn,
            trigger_source=AFTERNOON_SOURCE,
            measured_at=measured,
            period_start=measured,
            period_end=measured,
        )
        finish_fetch_run(conn, run_id, status="completed")
        conn.commit()

    assert scheduled_job_ran_today(AFTERNOON_SOURCE) is False


def test_catch_up_ticks_growth_without_daily_growth_sync(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    calls: list[str] = []

    def _fake_tick() -> None:
        calls.append("growth")

    def _fake_sync(**kwargs) -> None:
        calls.append(str(kwargs.get("mode") or kwargs.get("trigger_source")))

    morning = datetime(2026, 11, 6, 8, 0, tzinfo=AMSTERDAM)
    monkeypatch.setattr("sync.scheduler._amsterdam_now", lambda: morning)
    monkeypatch.setattr("sync.scheduler.run_growth_tick", _fake_tick)
    monkeypatch.setattr("sync.scheduler.run_afternoon_comparison", lambda: calls.append("afternoon"))
    monkeypatch.setattr("sync.scheduler.run_evening_refresh", lambda: calls.append("evening"))
    monkeypatch.setattr("sync.comparison.run_comparison_sync", _fake_sync)

    from sync.scheduler import catch_up_missed_jobs

    catch_up_missed_jobs()
    assert calls == ["growth"]
    assert "growth" in calls
    assert "afternoon" not in calls

    calls.clear()
    afternoon = datetime(2026, 11, 6, 14, 0, tzinfo=AMSTERDAM)
    monkeypatch.setattr("sync.scheduler._amsterdam_now", lambda: afternoon)
    catch_up_missed_jobs()
    assert calls == ["growth", "afternoon"]

    calls.clear()
    evening = datetime(2026, 11, 6, 21, 0, tzinfo=AMSTERDAM)
    monkeypatch.setattr("sync.scheduler._amsterdam_now", lambda: evening)
    catch_up_missed_jobs()
    assert calls == ["growth", "evening"]


def test_growth_tick_stops_without_durable_storage(monkeypatch) -> None:
    from sync.scheduler import run_growth_tick_once

    called: list[str] = []
    monkeypatch.setattr("db.durable_sqlite.store_from_env", lambda: None)
    monkeypatch.setattr(
        "sync.growth_cycle.tick_growth_cycle",
        lambda **kwargs: called.append("tick"),
    )
    result = run_growth_tick_once()
    assert result.action == "storage_unavailable"
    assert called == []


def test_growth_tick_reports_when_another_job_holds_the_lock() -> None:
    from sync.scheduler import _job_lock, run_growth_tick_once

    assert _job_lock.acquire(blocking=False)
    try:
        result = run_growth_tick_once()
    finally:
        _job_lock.release()
    assert result.action == "deferred"
    assert result.detail == "Andere Meta-taak loopt."


def test_start_scheduler_does_not_keep_morning_growth(monkeypatch) -> None:
    import sync.scheduler as scheduler

    jobs: list[str] = []

    class _Scheduler:
        def __init__(self, timezone=None) -> None:
            self.timezone = timezone

        def add_job(self, fn, trigger=None, **kwargs) -> None:
            jobs.append(str(kwargs.get("id")))

        def start(self) -> None:
            return None

    monkeypatch.setattr(scheduler, "_started", False)
    monkeypatch.setattr(scheduler, "catch_up_missed_jobs", lambda: None)
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    monkeypatch.delenv("DASHBOARD_SCHEDULER", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        "apscheduler.schedulers.background.BackgroundScheduler",
        _Scheduler,
    )
    scheduler.start_scheduler()
    assert "growth_cycle_tick" in jobs
    assert "afternoon_comparison" in jobs
    assert "evening_refresh" in jobs
    assert "morning_growth" not in jobs
    assert "run_morning_growth" not in jobs


def test_start_scheduler_noop_without_render(monkeypatch) -> None:
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    start_scheduler()
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    start_scheduler()
