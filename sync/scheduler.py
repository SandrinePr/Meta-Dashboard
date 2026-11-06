"""In-process fetch jobs (Europe/Amsterdam).

Runs in the web process so jobs write the same SQLite file the dashboard reads.
Render cron cannot share disk with the web service; it only wakes this process.
Growth does not sleep inside this process: each wake runs one queue step when
``next_run_at`` is due.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
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


def run_growth_tick_once():
    """Run one due growth step and return it, including when another job holds the lock."""
    from db.durable_sqlite import (
        DurableConflict,
        acquire_lease,
        holder_id,
        store_from_env,
    )
    from sync.growth_cycle import TickResult, tick_growth_cycle

    if not _job_lock.acquire(blocking=False):
        logger.warning("Skip scheduled job growth_tick; another job is still running.")
        return TickResult(action="deferred", detail="Andere Meta-taak loopt.")
    try:
        logger.info("Scheduled job start: growth_tick")
        snapshot = store_from_env()
        if snapshot is None:
            logger.warning("Growth tick stopped; durable storage is not configured.")
            return TickResult(
                action="storage_unavailable",
                detail="Duurzame opslag is niet geconfigureerd. Er is geen stap uitgevoerd.",
            )
        try:
            snapshot.ensure_private()
        except DurableConflict:
            logger.warning("Growth tick stopped; snapshot storage is not private.")
            return TickResult(
                action="storage_unavailable",
                detail="Duurzame opslag is niet privé. Er is geen stap uitgevoerd.",
            )
        if not acquire_lease(
            snapshot,
            holder_id(),
            datetime.now(tz=timezone.utc),
        ):
            logger.warning("Skip growth tick; another instance holds the snapshot lease.")
            return TickResult(
                action="deferred",
                detail="Andere instantie bewaart de database.",
            )
        from config import get_settings
        from db.database import initialize_database

        database_path = get_settings().database_path
        initialize_database(database_path)
        try:
            from db.durable_sqlite import publish_database

            publish_database(database_path, snapshot)
        except DurableConflict:
            logger.exception("SQLite snapshot was not published")
            return TickResult(
                action="publish_failed",
                detail="De externe kopie is niet bijgewerkt. Er is geen nieuwe stap uitgevoerd.",
            )
        result = tick_growth_cycle()
        logger.info(
            "Growth tick action=%s cycle=%s account=%s",
            result.action,
            result.cycle_id,
            result.username,
        )
        try:
            publish_database(database_path, snapshot)
        except DurableConflict:
            logger.exception("SQLite snapshot was not published")
            return TickResult(
                action="publish_failed",
                cycle_id=result.cycle_id,
                username=result.username,
                detail="De stap staat lokaal, maar de externe kopie is niet bijgewerkt.",
            )
        logger.info("Scheduled job done: growth_tick")
        return result
    except Exception as exc:
        logger.exception("Scheduled job failed: growth_tick")
        return TickResult(action="error", detail=type(exc).__name__)
    finally:
        _job_lock.release()


def run_growth_tick() -> None:
    """One weekly-growth step, or a no-op when the queue is not due."""
    run_growth_tick_once()


def _updated_accounts(result) -> str:
    names = [
        item.username
        for item in getattr(result, "accounts", [])
        if getattr(item, "status", "") in {"success", "partial"}
    ]
    return ",".join(names) if names else "-"


def run_afternoon_comparison() -> None:
    from sync.daily_update import daily_slot_complete, run_daily_update

    def _job() -> None:
        if daily_slot_complete(AFTERNOON_SOURCE):
            logger.info("Afternoon comparison already finished today; skip.")
            return
        result = run_daily_update(trigger_source=AFTERNOON_SOURCE)
        logger.info(
            "Afternoon comparison status=%s run_id=%s started=%s accounts=%s",
            result.status,
            result.run_id,
            result.measured_at,
            _updated_accounts(result),
        )

    _run_locked("afternoon_comparison", _job)


def run_evening_refresh() -> None:
    from sync.daily_update import daily_slot_complete, run_daily_update
    from sync.thumbnails import cache_missing_thumbnails

    def _job() -> None:
        if daily_slot_complete(EVENING_SOURCE):
            logger.info("Evening comparison already finished today; skip.")
            return
        result = run_daily_update(trigger_source=EVENING_SOURCE)
        saved = skipped = failed = 0
        if not result.rate_limited:
            saved, skipped, failed = cache_missing_thumbnails(recent_first=True, limit=20)
        logger.info(
            "Evening comparison status=%s run_id=%s started=%s accounts=%s thumbs saved=%s skipped=%s failed=%s",
            result.status,
            result.run_id,
            result.measured_at,
            _updated_accounts(result),
            saved,
            skipped,
            failed,
        )

    _run_locked("evening_refresh", _job)


def resume_due_comparison() -> None:
    """Continue today's open 13:30 or 20:30 slot. Does not open a slot early."""
    now = _amsterdam_now()
    minutes = now.hour * 60 + now.minute
    afternoon_at = 13 * 60 + 30
    evening_at = 20 * 60 + 30
    if minutes >= evening_at:
        run_evening_refresh()
    elif minutes >= afternoon_at:
        run_afternoon_comparison()


def catch_up_missed_jobs() -> None:
    """One due growth step first, then today's comparison slot if it is still open.

    Afternoon and evening still share the Meta budget. They start only after
    the growth step has claimed or deferred its own queue item.
    """
    try:
        run_growth_tick()
    except Exception:
        logger.exception("Growth tick failed")
    try:
        resume_due_comparison()
    except Exception:
        logger.exception("Scheduled catch-up failed")


def run_interval_wake() -> None:
    """Growth step first. An open daily slot resumes afterwards, one saved step at a time."""
    run_growth_tick()
    try:
        resume_due_comparison()
    except Exception:
        logger.exception("Daily comparison resume failed")


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
        # The old 07:30 morning_growth job is intentionally absent. Weekly
        # growth is one due step per wake. Afternoon and evening stay.
        scheduler.add_job(
            run_interval_wake,
            "interval",
            minutes=5,
            id="growth_cycle_tick",
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
            "Fetch scheduler started (growth queue every 5 min, 13:30 comparison, "
            "20:30 comparison, Europe/Amsterdam). Weekly growth opens Friday 01:00."
        )
        threading.Thread(
            target=catch_up_missed_jobs,
            name="schedule-catchup",
            daemon=True,
        ).start()
