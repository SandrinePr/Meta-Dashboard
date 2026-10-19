"""In-process daily fetch jobs (Europe/Amsterdam).

Runs in the web process so jobs write the same SQLite file the dashboard reads.
Render cron cannot share disk with the web service; it only wakes this process.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
MORNING_SOURCE = "schedule_morning_growth"
AFTERNOON_SOURCE = "schedule_afternoon_comparison"
EVENING_SOURCE = "schedule_evening_refresh"

_scheduler = None
_started = False
_start_lock = threading.Lock()
_job_lock = threading.Lock()


def _amsterdam_now() -> datetime:
    return datetime.now(tz=AMSTERDAM)


def scheduled_job_ran_today(trigger_source: str) -> bool:
    """True when this scheduler source already ran today (Amsterdam date)."""
    from db.database import get_connection, initialize_database

    initialize_database()
    today = _amsterdam_now().date()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT measured_at, started_at, status
            FROM comparison_fetch_runs
            WHERE trigger_source = ?
              AND status IN ('completed', 'completed_with_errors', 'running')
            """,
            (trigger_source,),
        ).fetchall()
        conn.commit()
    for row in rows:
        raw = row["measured_at"] or row["started_at"]
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed.astimezone(AMSTERDAM).date() == today:
            return True
    return False


def _run_locked(name: str, fn) -> None:
    if not _job_lock.acquire(blocking=False):
        logger.warning("Skip scheduled job %s; another job is still running.", name)
        return
    try:
        logger.info("Scheduled job start: %s", name)
        fn()
        logger.info("Scheduled job done: %s", name)
    except Exception:
        logger.exception("Scheduled job failed: %s", name)
    finally:
        _job_lock.release()


def run_morning_growth() -> None:
    from sync.comparison import run_comparison_sync

    def _job() -> None:
        if scheduled_job_ran_today(MORNING_SOURCE):
            logger.info("Morning growth already ran today; skip.")
            return
        result = run_comparison_sync(
            trigger_source=MORNING_SOURCE,
            mode="growth",
            force=False,
        )
        logger.info(
            "Morning growth status=%s run_id=%s",
            result.status,
            result.run_id,
        )

    _run_locked("morning_growth", _job)


def run_afternoon_comparison() -> None:
    from sync.comparison import run_comparison_sync

    def _job() -> None:
        if scheduled_job_ran_today(AFTERNOON_SOURCE):
            logger.info("Afternoon comparison already ran today; skip.")
            return
        result = run_comparison_sync(
            trigger_source=AFTERNOON_SOURCE,
            mode="full",
            force=False,
        )
        logger.info(
            "Afternoon comparison status=%s run_id=%s",
            result.status,
            result.run_id,
        )

    _run_locked("afternoon_comparison", _job)


def run_evening_refresh() -> None:
    from sync.comparison import run_comparison_sync
    from sync.orchestrator import run_sync
    from sync.thumbnails import cache_missing_thumbnails

    def _job() -> None:
        if scheduled_job_ran_today(EVENING_SOURCE):
            logger.info("Evening refresh already ran today; skip.")
            return
        run_sync(platform="all")
        result = run_comparison_sync(
            trigger_source=EVENING_SOURCE,
            mode="full",
            force=True,
        )
        saved, skipped, failed = cache_missing_thumbnails(recent_first=True, limit=250)
        logger.info(
            "Evening refresh status=%s run_id=%s thumbs saved=%s skipped=%s failed=%s",
            result.status,
            result.run_id,
            saved,
            skipped,
            failed,
        )

    _run_locked("evening_refresh", _job)


def catch_up_missed_jobs() -> None:
    """Run today's due slot(s) if the process started after the scheduled time."""
    now = _amsterdam_now()
    minutes = now.hour * 60 + now.minute
    morning_at = 7 * 60 + 30
    afternoon_at = 13 * 60 + 30
    evening_at = 20 * 60 + 30
    try:
        if minutes >= evening_at:
            if not scheduled_job_ran_today(EVENING_SOURCE):
                run_evening_refresh()
            return
        if minutes >= afternoon_at:
            if not scheduled_job_ran_today(AFTERNOON_SOURCE):
                run_afternoon_comparison()
            elif not scheduled_job_ran_today(MORNING_SOURCE):
                run_morning_growth()
            return
        if minutes >= morning_at and not scheduled_job_ran_today(MORNING_SOURCE):
            run_morning_growth()
    except Exception:
        logger.exception("Scheduled catch-up failed")


def start_scheduler() -> None:
    """Start APScheduler once per process. No-op in tests or when disabled."""
    global _scheduler, _started
    if os.environ.get("DISABLE_SCHEDULER") == "1":
        return
    if os.environ.get("DASHBOARD_SCHEDULER") == "0":
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    on_render = os.environ.get("RENDER", "").strip().lower() in {"true", "1"}
    if not on_render and os.environ.get("ENABLE_SCHEDULER") != "1":
        return
    with _start_lock:
        if _started:
            return
        _started = True
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            logger.warning("APScheduler not installed; daily fetch jobs disabled.")
            return

        scheduler = BackgroundScheduler(timezone=AMSTERDAM)
        scheduler.add_job(
            run_morning_growth,
            CronTrigger(hour=7, minute=30, timezone=AMSTERDAM),
            id="morning_growth",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_afternoon_comparison,
            CronTrigger(hour=13, minute=30, timezone=AMSTERDAM),
            id="afternoon_comparison",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_evening_refresh,
            CronTrigger(hour=20, minute=30, timezone=AMSTERDAM),
            id="evening_refresh",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info(
            "Daily fetch scheduler started (07:30 growth, 13:30 comparison, "
            "20:30 full refresh, Europe/Amsterdam)."
        )
        threading.Thread(
            target=catch_up_missed_jobs,
            name="schedule-catchup",
            daemon=True,
        ).start()
