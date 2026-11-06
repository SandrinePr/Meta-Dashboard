"""Weekly comparison queue.

One stored step per due time: followers, then one page of posts from the last
7 days. The wait lives in ``next_run_at``; this module never sleeps. A long
page walk does not keep the other accounts waiting. A Meta rate limit waits
and resumes the same step.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from analytics.week_growth import WeekGrowth, WeekMeasurement, describe_week_growth
from db.comparison_repository import (
    ensure_competitor_placeholders,
    finish_fetch_run,
    get_account_by_id,
    get_discovery_host,
    get_discovery_rate_limit,
    get_preferences,
    set_discovery_rate_limit,
    start_fetch_run,
    upsert_comparison_post,
    utc_now_iso,
)
from db.database import get_connection, initialize_database
from meta.redact import redact_text
from meta.client import (
    RATE_LIMIT_USER_MESSAGE,
    TOKEN_EXPIRED_MESSAGE,
    MetaClient,
    MetaClientError,
    MetaRequestError,
)
from meta.discovery import (
    MEDIA_FIELDS,
    MEDIA_PAGE_LIMIT,
    fetch_business_discovery,
    fetch_own_account_profile,
    media_after_cursor,
    optional_int,
    safe_error_text,
)
from meta.endpoints import MEDIA_EDGE
from meta.rate_limit import is_rate_limit_error

logger = logging.getLogger(__name__)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
TRIGGER_SOURCE = "schedule_weekly_growth"
MIN_GAP = timedelta(hours=1)
WEEK_POST_WINDOW = timedelta(days=7)
MAX_ATTEMPTS = 3
STALE_RUNNING = timedelta(minutes=15)
# Permission / token / missing object. Retrying spends the shared app budget.
PERMANENT_ERROR_CODES = {10, 100, 190, 803}

STATUS_LABELS = {
    "waiting": "wachtend",
    "running": "bezig",
    "waiting_api": "wacht op API",
    "complete": "compleet",
    "error": "fout",
}


STEP_FOLLOWERS = "followers"
STEP_POSTS = "posts"
STEP_STORED = "stored"


@dataclass(frozen=True, slots=True)
class GrowthTarget:
    cycle_account_id: int
    account_id: int | None
    username: str
    role: str
    external_id: str | None
    host_external_id: str | None
    step: str = STEP_FOLLOWERS
    cursor_after: str | None = None


@dataclass(frozen=True, slots=True)
class GrowthProfile:
    followers_count: int | None
    media_count: int | None = None
    external_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class GrowthMediaItem:
    external_id: str
    like_count: int | None = None
    comments_count: int | None = None
    published_at: str | None = None
    media_type: str | None = None
    media_product_type: str | None = None
    permalink: str | None = None
    caption: str | None = None


@dataclass(frozen=True, slots=True)
class GrowthStepResult:
    followers_count: int | None = None
    media_count: int | None = None
    external_id: str | None = None
    name: str | None = None
    posts: tuple[GrowthMediaItem, ...] = ()
    cursor_after: str | None = None
    posts_done: bool = False


@dataclass(frozen=True, slots=True)
class TickResult:
    action: str
    cycle_id: int | None = None
    username: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class GrowthAccountProgress:
    username: str
    display_name: str
    role: str
    status: str
    status_label: str
    last_success_at: str | None
    last_followers: int | None
    next_run_at: str | None
    error_message: str | None
    week_growth: WeekGrowth


@dataclass(frozen=True, slots=True)
class GrowthProgress:
    week_start: str | None
    cycle_status: str
    complete_count: int
    total_count: int
    next_run_at: str | None
    stale: bool
    message: str
    accounts: tuple[GrowthAccountProgress, ...]


Fetcher = Callable[[GrowthTarget], "GrowthStepResult | GrowthProfile"]


def amsterdam_now() -> datetime:
    return datetime.now(tz=AMSTERDAM)


def week_friday(now: datetime) -> date:
    """Friday that opened the current weekly cycle in Europe/Amsterdam."""
    local = _as_amsterdam(now)
    days_since_friday = (local.weekday() - 4) % 7
    return (local - timedelta(days=days_since_friday)).date()


def friday_start(day: date) -> datetime:
    """Friday 01:00 Europe/Amsterdam, including the DST offset of that date."""
    return datetime(day.year, day.month, day.day, 1, tzinfo=AMSTERDAM)


def _as_amsterdam(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=AMSTERDAM)
    return now.astimezone(AMSTERDAM)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _due(value: str | None, now: datetime) -> bool:
    moment = _parse(value)
    return moment is not None and moment <= _as_amsterdam(now)


def fetch_growth_profile(target: GrowthTarget) -> GrowthProfile:
    """One current follower snapshot. No media pagination."""
    if not target.host_external_id or target.host_external_id.startswith("pending:"):
        raise MetaRequestError("Geen discovery-host voor de volgersmeting.")
    client = MetaClient.from_settings()
    if target.role == "own":
        if not target.external_id or target.external_id.startswith("pending:"):
            raise MetaRequestError(f"Geen Instagram-ID voor @{target.username}.")
        profile = fetch_own_account_profile(client, target.external_id)
        return GrowthProfile(
            followers_count=optional_int(profile.get("followers_count")),
            media_count=optional_int(profile.get("media_count")),
            external_id=str(profile.get("id") or target.external_id),
            name=profile.get("name"),
        )
    discovery, _meta = fetch_business_discovery(
        client,
        target.host_external_id,
        target.username,
        include_media=False,
        include_account=True,
    )
    return GrowthProfile(
        followers_count=optional_int(discovery.get("followers_count")),
        media_count=optional_int(discovery.get("media_count")),
        external_id=str(discovery.get("id") or "") or None,
        name=discovery.get("name"),
    )


def _media_item(payload: dict) -> GrowthMediaItem | None:
    external_id = str(payload.get("id") or "").strip()
    if not external_id:
        return None
    timestamp = payload.get("timestamp")
    return GrowthMediaItem(
        external_id=external_id,
        like_count=optional_int(payload.get("like_count")),
        comments_count=optional_int(payload.get("comments_count")),
        published_at=timestamp if isinstance(timestamp, str) else None,
        media_type=payload.get("media_type") if isinstance(payload.get("media_type"), str) else None,
        media_product_type=payload.get("media_product_type")
        if isinstance(payload.get("media_product_type"), str)
        else None,
        permalink=payload.get("permalink") if isinstance(payload.get("permalink"), str) else None,
        caption=payload.get("caption") if isinstance(payload.get("caption"), str) else None,
    )


def _fetch_one_media_page(target: GrowthTarget) -> GrowthStepResult:
    """One media page. Does not follow the next cursor."""
    if target.role == "own":
        if not target.external_id or target.external_id.startswith("pending:"):
            raise MetaRequestError(f"Geen Instagram-ID voor @{target.username}.")
        client = MetaClient.from_settings()
        params: dict = {
            "fields": ",".join(MEDIA_FIELDS),
            "limit": MEDIA_PAGE_LIMIT,
        }
        if target.cursor_after:
            params["after"] = target.cursor_after
        payload = client.get_json(
            f"{target.external_id}/{MEDIA_EDGE}",
            params=params,
            use_page_token=True,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        items = [item for item in (_media_item(row) for row in data or [] if isinstance(row, dict)) if item]
        cursor = redact_text(media_after_cursor(payload if isinstance(payload, dict) else None))
        return GrowthStepResult(posts=tuple(items), cursor_after=cursor, posts_done=cursor is None)

    if not target.host_external_id or target.host_external_id.startswith("pending:"):
        raise MetaRequestError("Geen discovery-host voor de postpagina.")
    client = MetaClient.from_settings()
    discovery, _meta = fetch_business_discovery(
        client,
        target.host_external_id,
        target.username,
        after=target.cursor_after,
        media_limit=MEDIA_PAGE_LIMIT,
        include_media=True,
        include_account=False,
    )
    media = discovery.get("media") if isinstance(discovery.get("media"), dict) else {}
    data = media.get("data") if isinstance(media, dict) else None
    items = [item for item in (_media_item(row) for row in data or [] if isinstance(row, dict)) if item]
    cursor = redact_text(media_after_cursor(media if isinstance(media, dict) else None))
    return GrowthStepResult(posts=tuple(items), cursor_after=cursor, posts_done=cursor is None)


def fetch_growth_step(target: GrowthTarget) -> GrowthStepResult:
    """One followers call, or one media page. Never both."""
    if _step_name(target.step) == STEP_FOLLOWERS:
        profile = fetch_growth_profile(target)
        return GrowthStepResult(
            followers_count=profile.followers_count,
            media_count=profile.media_count,
            external_id=profile.external_id,
            name=profile.name,
        )
    return _fetch_one_media_page(target)


def tick_growth_cycle(
    *,
    now: datetime | None = None,
    fetcher: Fetcher | None = None,
) -> TickResult:
    """Run at most one due step. Returns immediately when nothing is due."""
    moment = _as_amsterdam(now or amsterdam_now())
    initialize_database()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = _tick_locked(conn, moment, fetcher or fetch_growth_step)
        conn.commit()
        return result


def load_growth_progress(*, now: datetime | None = None) -> GrowthProgress:
    moment = _as_amsterdam(now or amsterdam_now())
    initialize_database()
    with get_connection() as conn:
        return _progress(conn, moment)


def _tick_locked(conn: sqlite3.Connection, now: datetime, fetcher: Fetcher) -> TickResult:
    _release_stale_running(conn, now)
    if _fresh_running(conn, now):
        return TickResult(action="duplicate")

    _ensure_current_friday(conn, now)
    closed = _close_one_finished_walk(conn, now)
    if closed is not None:
        return closed

    cycle = _preferred_cycle(conn, now)
    if cycle is None:
        return TickResult(action="idle")

    _refresh_cycle_status(conn, int(cycle["id"]), now)
    cycle = _cycle_row(conn, int(cycle["id"]))
    if cycle is None:
        return TickResult(action="idle")
    if not _due(cycle["next_run_at"], now):
        return TickResult(action="wait", cycle_id=int(cycle["id"]))
    blocked = _rate_limit_until(conn, now)
    if blocked is not None and blocked > now:
        return TickResult(
            action="wait",
            cycle_id=int(cycle["id"]),
            detail=RATE_LIMIT_USER_MESSAGE,
        )

    accounts = _account_rows(conn, int(cycle["id"]))
    repaired = _repair_stored(conn, accounts, now)
    if repaired is not None:
        # Saved work was found locally. Do not call the API in this same tick.
        _pause_account(
            conn,
            repaired,
            step=STEP_POSTS,
            cursor=repaired["cursor_after"],
            attempts=0,
            when=now + MIN_GAP,
            now=now,
        )
        return TickResult(action="local", cycle_id=int(cycle["id"]), username=str(repaired["username"]))
    accounts = _account_rows(conn, int(cycle["id"]))
    # One call. A page walk yields to an account that still needs its first step.
    target = _next_fetch_target(conn, accounts, now)
    if target is None:
        _refresh_cycle_status(conn, int(cycle["id"]), now)
        return TickResult(action="idle", cycle_id=int(cycle["id"]))
    if target["next_run_at"] is not None and not _due(target["next_run_at"], now):
        return TickResult(action="wait", cycle_id=int(cycle["id"]), username=target["username"])

    if not _claim(conn, int(target["id"]), now):
        return TickResult(action="duplicate", cycle_id=int(cycle["id"]))
    fresh = conn.execute(
        "SELECT * FROM growth_cycle_accounts WHERE id = ?",
        (int(target["id"]),),
    ).fetchone()
    return _fetch_account(conn, cycle, fresh, now, fetcher)


def _ensure_current_friday(conn, now: datetime) -> None:
    """Open today's Friday cycle even when an older cycle is still incomplete."""
    friday = week_friday(now)
    if now < friday_start(friday):
        return
    if _cycle_for_week(conn, friday) is not None:
        return
    cycle_id = _create_cycle(conn, friday, now)
    older = conn.execute(
        """
        SELECT next_run_at
        FROM growth_cycles
        WHERE week_start < ?
          AND status IN ('open', 'incomplete')
        ORDER BY week_start DESC, id DESC
        LIMIT 1
        """,
        (friday.isoformat(),),
    ).fetchone()
    if older is None or not older["next_run_at"]:
        return
    if _due(str(older["next_run_at"]), now):
        return
    hold = str(older["next_run_at"])
    conn.execute(
        """
        UPDATE growth_cycles
        SET next_run_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (hold, _iso(now), cycle_id),
    )
    conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET next_run_at = ?, updated_at = ?
        WHERE cycle_id = ? AND position = 0 AND status = 'waiting'
        """,
        (hold, _iso(now), cycle_id),
    )


def _preferred_cycle(conn, now: datetime) -> sqlite3.Row | None:
    friday = week_friday(now)
    if now >= friday_start(friday):
        current = _cycle_for_week(conn, friday)
        if current is not None and _cycle_has_open_work(conn, int(current["id"])):
            return current
    return _runnable_cycle(conn)


def _cycle_has_open_work(conn, cycle_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM growth_cycle_accounts
        WHERE cycle_id = ?
          AND status IN ('waiting', 'waiting_api', 'running')
        LIMIT 1
        """,
        (cycle_id,),
    ).fetchone()
    return row is not None


def _close_one_finished_walk(conn, now: datetime) -> TickResult | None:
    """Stop a page walk that is already older than the week window. No API call."""
    rows = conn.execute(
        """
        SELECT a.*
        FROM growth_cycle_accounts a
        JOIN growth_cycles c ON c.id = a.cycle_id
        WHERE a.status IN ('waiting', 'waiting_api')
          AND a.step IN ('posts', 'engagement')
          AND a.cursor_after IS NOT NULL
          AND a.cursor_after != ''
        ORDER BY c.week_start ASC, a.position ASC, a.id ASC
        """
    ).fetchall()
    for account in rows:
        if not _posts_already_past_window(conn, account, now):
            continue
        cycle_id = int(account["cycle_id"])
        _finish_account(
            conn,
            account,
            cycle_id,
            now,
            pause=timedelta(0),
            schedule=not _newer_week_not_started(conn, account, now),
        )
        logger.info(
            "Postpagina's ouder dan 7 dagen, @%s stopt zonder nieuwe aanvraag.",
            account["username"],
        )
        return TickResult(action="local", cycle_id=cycle_id, username=str(account["username"]))
    return None


def _step_name(value: str | None) -> str:
    if value in {STEP_POSTS, "engagement"}:
        return STEP_POSTS
    if value == STEP_STORED:
        return STEP_STORED
    return STEP_FOLLOWERS


def _as_step_result(value: GrowthStepResult | GrowthProfile) -> GrowthStepResult:
    if isinstance(value, GrowthStepResult):
        return value
    return GrowthStepResult(
        followers_count=value.followers_count,
        media_count=value.media_count,
        external_id=value.external_id,
        name=value.name,
    )


def _fetch_account(conn, cycle, account, now: datetime, fetcher: Fetcher) -> TickResult:
    cycle_id = int(cycle["id"])
    username = str(account["username"])
    blocked = _rate_limit_until(conn, now)
    if blocked is not None and blocked > now:
        _defer_api(conn, account, cycle_id, blocked, now)
        return TickResult(action="rate_limit", cycle_id=cycle_id, username=username)

    if _foreign_fetch_running(conn):
        _defer_api(
            conn,
            account,
            cycle_id,
            now + MIN_GAP,
            now,
            status="waiting",
            message="Andere Meta-taak loopt.",
        )
        return TickResult(
            action="deferred",
            cycle_id=cycle_id,
            username=username,
            detail="Andere Meta-taak loopt.",
        )

    target = _target_for(conn, account)
    try:
        outcome = _as_step_result(fetcher(target))
    except MetaClientError as exc:
        return _handle_failure(conn, account, cycle_id, now, exc)

    if _step_name(account["step"]) == STEP_FOLLOWERS:
        return _store_followers(conn, account, cycle_id, now, outcome)
    return _store_posts(conn, account, cycle_id, now, outcome)


def _store_followers(conn, account, cycle_id: int, now: datetime, outcome: GrowthStepResult) -> TickResult:
    username = str(account["username"])
    if outcome.followers_count is None:
        return _handle_missing(conn, account, cycle_id, now)
    profile = GrowthProfile(
        followers_count=outcome.followers_count,
        media_count=outcome.media_count,
        external_id=outcome.external_id,
        name=outcome.name,
    )
    _apply_profile_identity(conn, account, profile)
    measurement_id, measured_at = _insert_measurement_once(
        conn,
        account,
        followers_count=outcome.followers_count,
        media_count=outcome.media_count,
        measured_at=now,
    )
    _pause_account(
        conn,
        account,
        step=STEP_POSTS,
        cursor=None,
        attempts=0,
        when=now + MIN_GAP,
        now=now,
        measurement_id=measurement_id,
        followers_count=outcome.followers_count,
        follower_measured_at=measured_at,
    )
    if account["account_id"] is not None:
        conn.execute(
            """
            UPDATE accounts
            SET access_status = 'ok', last_access_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_iso(now), int(account["account_id"])),
        )
    logger.info("Volgers opgeslagen @%s followers=%s", username, outcome.followers_count)
    return TickResult(action="stored", cycle_id=cycle_id, username=username)


def _store_posts(conn, account, cycle_id: int, now: datetime, outcome: GrowthStepResult) -> TickResult:
    username = str(account["username"])
    if account["account_id"] is None:
        return _fail_attempt(conn, account, cycle_id, now, "Geen accountrij voor de posts.", permanent=True)
    run_id = _cycle_run_id(conn, account)
    if run_id is None:
        return _fail_attempt(conn, account, cycle_id, now, "Volgersmeting van deze cyclus ontbreekt.", permanent=False)
    _persist_posts(conn, int(account["account_id"]), run_id, outcome.posts, now)
    fresh = conn.execute(
        "SELECT * FROM growth_cycle_accounts WHERE id = ?",
        (int(account["id"]),),
    ).fetchone()
    if _posts_missing_engagement(conn, run_id):
        return _retry_same_page(conn, fresh, cycle_id, now, "Likes of reacties ontbreken nog.")
    if outcome.cursor_after and outcome.posts and not _page_crossed_window(outcome.posts, now):
        _pause_account(
            conn,
            fresh,
            step=STEP_POSTS,
            cursor=outcome.cursor_after,
            attempts=0,
            when=now + MIN_GAP,
            now=now,
        )
        logger.info("Postpagina opgeslagen @%s, volgende pagina volgt.", username)
        return TickResult(action="stored", cycle_id=cycle_id, username=username)
    _finish_account(conn, fresh, cycle_id, now)
    logger.info("Weekmeting compleet @%s", username)
    return TickResult(action="stored", cycle_id=cycle_id, username=username)


def _handle_missing(conn, account, cycle_id: int, now: datetime) -> TickResult:
    message = "Volgersaantal ontbreekt in het antwoord."
    return _fail_attempt(conn, account, cycle_id, now, message, permanent=False)


def _handle_failure(
    conn,
    account,
    cycle_id: int,
    now: datetime,
    exc: MetaClientError,
) -> TickResult:
    if isinstance(exc, MetaRequestError) and (
        exc.is_rate_limited
        or is_rate_limit_error(error_code=exc.error_code, message=str(exc), status_code=exc.status_code)
    ):
        until = _parse(exc.blocked_until)
        if until is None and exc.retry_after_seconds:
            until = now + timedelta(seconds=int(exc.retry_after_seconds))
        if until is None or until < now + MIN_GAP:
            until = now + MIN_GAP
        set_discovery_rate_limit(
            conn,
            error_code=exc.error_code,
            message=RATE_LIMIT_USER_MESSAGE,
            retry_after_seconds=exc.retry_after_seconds,
            blocked_until=_iso(until),
            usage=exc.usage_headers,
        )
        _defer_api(conn, account, cycle_id, until, now, status="waiting_api")
        logger.info("Weekmeting wacht op API tot %s (@%s)", _iso(until), account["username"])
        return TickResult(
            action="rate_limit",
            cycle_id=cycle_id,
            username=str(account["username"]),
            detail=RATE_LIMIT_USER_MESSAGE,
        )

    permanent = isinstance(exc, MetaRequestError) and exc.error_code in PERMANENT_ERROR_CODES
    if isinstance(exc, MetaRequestError) and exc.error_code == 190:
        message = TOKEN_EXPIRED_MESSAGE
    else:
        message = safe_error_text(exc) or "Volgersmeting mislukt."
    return _fail_attempt(conn, account, cycle_id, now, message, permanent=permanent)


def _fail_attempt(
    conn,
    account,
    cycle_id: int,
    now: datetime,
    message: str,
    *,
    permanent: bool,
) -> TickResult:
    attempts = int(account["attempts"] or 0) + 1
    give_up = permanent or attempts >= MAX_ATTEMPTS
    status = "error" if give_up else "waiting"
    conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET status = ?,
            step = ?,
            attempts = ?,
            error_message = ?,
            next_run_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            _step_name(account["step"]),
            attempts,
            message[:500],
            None if give_up else _iso(now + MIN_GAP),
            _iso(now),
            int(account["id"]),
        ),
    )
    if account["account_id"] is not None:
        conn.execute(
            """
            UPDATE accounts
            SET last_access_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (message[:500], _iso(now), int(account["account_id"])),
        )
    if give_up:
        _schedule_after(conn, cycle_id, int(account["position"]), now + MIN_GAP, now)
    else:
        conn.execute(
            """
            UPDATE growth_cycles
            SET next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (_iso(now + MIN_GAP), _iso(now), cycle_id),
        )
        _refresh_cycle_status(conn, cycle_id, now)
    logger.info(
        "Weekmeting @%s status=%s attempts=%s",
        account["username"],
        status,
        attempts,
    )
    return TickResult(
        action="error" if give_up else "retry",
        cycle_id=cycle_id,
        username=str(account["username"]),
        detail=message,
    )


def _defer_api(
    conn,
    account,
    cycle_id: int,
    until: datetime,
    now: datetime,
    status: str = "waiting_api",
    message: str | None = None,
) -> None:
    if message is None and status == "waiting_api":
        message = RATE_LIMIT_USER_MESSAGE
    conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET status = ?,
            next_run_at = ?,
            error_message = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            _iso(until),
            message,
            _iso(now),
            int(account["id"]),
        ),
    )
    conn.execute(
        """
        UPDATE growth_cycles
        SET next_run_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (_iso(until), _iso(now), cycle_id),
    )


def _pause_account(
    conn,
    account,
    *,
    step: str,
    cursor: str | None,
    attempts: int,
    when: datetime,
    now: datetime,
    measurement_id: int | None = None,
    followers_count: int | None = None,
    follower_measured_at: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET status = 'waiting',
            step = ?,
            cursor_after = ?,
            attempts = ?,
            error_message = NULL,
            next_run_at = ?,
            measurement_id = COALESCE(measurement_id, ?),
            followers_count = COALESCE(followers_count, ?),
            measured_at = COALESCE(measured_at, ?),
            last_success_measured_at = COALESCE(last_success_measured_at, ?),
            updated_at = ?
        WHERE id = ?
        """,
        (
            step,
            cursor,
            attempts,
            _iso(when),
            measurement_id,
            followers_count,
            follower_measured_at,
            follower_measured_at,
            _iso(now),
            int(account["id"]),
        ),
    )
    conn.execute(
        """
        UPDATE growth_cycles
        SET next_run_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (_iso(when), _iso(now), int(account["cycle_id"])),
    )
    _refresh_cycle_status(conn, int(account["cycle_id"]), now)


def _finish_account(
    conn,
    account,
    cycle_id: int,
    now: datetime,
    *,
    pause: timedelta = MIN_GAP,
    schedule: bool = True,
) -> None:
    conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET status = 'complete',
            step = 'stored',
            error_message = NULL,
            attempts = 0,
            next_run_at = NULL,
            cursor_after = NULL,
            measured_at = ?,
            last_success_measured_at = COALESCE(last_success_measured_at, ?),
            updated_at = ?
        WHERE id = ?
        """,
        (_iso(now), _iso(now), _iso(now), int(account["id"])),
    )
    if account["account_id"] is not None:
        conn.execute(
            """
            UPDATE accounts
            SET access_status = 'ok', last_access_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_iso(now), int(account["account_id"])),
        )
    if schedule:
        when = now + pause
        if pause == timedelta(0):
            cycle = _cycle_row(conn, cycle_id)
            current = _parse(cycle["next_run_at"]) if cycle is not None and cycle["next_run_at"] else None
            if current is not None and current > when:
                when = current
        _schedule_after(conn, cycle_id, int(account["position"]), when, now)
    else:
        _refresh_cycle_status(conn, cycle_id, now)


def _retry_same_page(conn, account, cycle_id: int, now: datetime, message: str) -> TickResult:
    attempts = int(account["attempts"] or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        return _fail_attempt(conn, account, cycle_id, now, message, permanent=True)
    _pause_account(
        conn,
        account,
        step=STEP_POSTS,
        cursor=account["cursor_after"],
        attempts=attempts,
        when=now + MIN_GAP,
        now=now,
    )
    conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET error_message = ?
        WHERE id = ?
        """,
        (message[:500], int(account["id"])),
    )
    return TickResult(
        action="retry",
        cycle_id=cycle_id,
        username=str(account["username"]),
        detail=message,
    )


def _cycle_run_id(conn, account) -> int | None:
    if account["measurement_id"] is None:
        return None
    row = conn.execute(
        "SELECT fetch_run_id FROM account_measurements WHERE id = ?",
        (int(account["measurement_id"]),),
    ).fetchone()
    if row is None:
        return None
    return int(row["fetch_run_id"])


def _persist_posts(conn, account_id: int, fetch_run_id: int, posts: tuple[GrowthMediaItem, ...], now: datetime) -> None:
    stamped = _iso(now)
    for post in posts:
        if not post.external_id:
            continue
        saved = upsert_comparison_post(
            conn,
            account_id=account_id,
            external_id=post.external_id,
            text=post.caption,
            permalink=post.permalink,
            media_type=post.media_type,
            media_product_type=post.media_product_type,
            published_at=post.published_at or stamped,
            raw_json=None,
        )
        conn.execute(
            """
            INSERT INTO post_measurements (
                post_id, fetch_run_id, measured_at, like_count, comments_count
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(fetch_run_id, post_id) DO UPDATE SET
                like_count = COALESCE(post_measurements.like_count, excluded.like_count),
                comments_count = COALESCE(post_measurements.comments_count, excluded.comments_count)
            """,
            (saved.row_id, fetch_run_id, stamped, post.like_count, post.comments_count),
        )


def _posts_missing_engagement(conn, fetch_run_id: int) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM post_measurements
        WHERE fetch_run_id = ?
          AND (like_count IS NULL OR comments_count IS NULL)
        """,
        (fetch_run_id,),
    ).fetchone()
    return int(row["n"] or 0) > 0


def _schedule_after(conn, cycle_id: int, position: int, when: datetime, now: datetime) -> None:
    conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET next_run_at = ?, updated_at = ?
        WHERE cycle_id = ?
          AND position = ?
          AND status IN ('waiting', 'waiting_api')
          AND (next_run_at IS NULL OR next_run_at < ?)
        """,
        (_iso(when), _iso(now), cycle_id, position + 1, _iso(when)),
    )
    conn.execute(
        """
        UPDATE growth_cycles
        SET next_run_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (_iso(when), _iso(now), cycle_id),
    )
    _refresh_cycle_status(conn, cycle_id, now)


def _mark_complete(conn, account, now: datetime) -> None:
    measured = account["measured_at"]
    followers = _measurement_followers(conn, account["measurement_id"])
    conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET status = 'complete',
            step = 'stored',
            error_message = NULL,
            followers_count = COALESCE(followers_count, ?),
            measured_at = COALESCE(measured_at, ?),
            last_success_measured_at = COALESCE(last_success_measured_at, ?),
            updated_at = ?
        WHERE id = ?
        """,
        (followers, measured, measured, _iso(now), int(account["id"])),
    )


def _insert_measurement_once(
    conn,
    account,
    *,
    followers_count: int,
    media_count: int | None,
    measured_at: datetime,
) -> tuple[int, str]:
    """Insert one row. A later retry must not move the original timestamp."""
    existing_id = account["measurement_id"]
    if existing_id is not None:
        row = conn.execute(
            """
            SELECT id, measured_at, followers_count
            FROM account_measurements
            WHERE id = ?
            """,
            (int(existing_id),),
        ).fetchone()
        if row is not None and row["followers_count"] is not None:
            return int(row["id"]), str(row["measured_at"])

    account_id = account["account_id"]
    if account_id is None:
        raise MetaRequestError(f"Geen accountrij voor @{account['username']}.")
    stamped = _iso(measured_at)
    run_id = start_fetch_run(
        conn,
        trigger_source=TRIGGER_SOURCE,
        measured_at=stamped,
        period_start=stamped,
        period_end=stamped,
    )
    try:
        cursor = conn.execute(
            """
            INSERT INTO account_measurements (
                account_id, fetch_run_id, measured_at, followers_count, media_count
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(account_id), run_id, stamped, int(followers_count), media_count),
        )
        finish_fetch_run(conn, run_id, status="completed")
    except Exception:
        finish_fetch_run(conn, run_id, status="failed", error_message="Meting niet opgeslagen.")
        raise
    return int(cursor.lastrowid), stamped


def _apply_profile_identity(conn, account, profile: GrowthProfile) -> None:
    if account["account_id"] is None or not profile.external_id:
        return
    current = get_account_by_id(conn, int(account["account_id"]))
    if current is None:
        return
    if current.external_id.startswith("pending:") or current.external_id != profile.external_id:
        conn.execute(
            """
            UPDATE accounts
            SET external_id = ?,
                name = COALESCE(?, name),
                username = COALESCE(username, ?),
                updated_at = ?
            WHERE id = ?
            """,
            (
                profile.external_id,
                profile.name,
                account["username"],
                utc_now_iso(),
                int(account["account_id"]),
            ),
        )


def _target_for(conn, account) -> GrowthTarget:
    host = get_discovery_host(conn)
    external_id = None
    if account["account_id"] is not None:
        row = get_account_by_id(conn, int(account["account_id"]))
        if row is not None:
            external_id = row.external_id
    return GrowthTarget(
        cycle_account_id=int(account["id"]),
        account_id=int(account["account_id"]) if account["account_id"] is not None else None,
        username=str(account["username"]),
        role=str(account["role"]),
        external_id=external_id,
        host_external_id=host.external_id if host is not None else None,
        step=_step_name(account["step"]),
        cursor_after=account["cursor_after"],
    )


def _create_cycle(conn, friday: date, now: datetime) -> int:
    own_id, competitors = get_preferences(conn)
    if own_id is None:
        host = get_discovery_host(conn)
        own_id = host.account_id if host is not None else None
    own = get_account_by_id(conn, own_id) if own_id is not None else None
    if own is None or not own.username:
        raise MetaRequestError("Geen eigen account voor de weekcyclus.")
    ensure_competitor_placeholders(conn, competitors)
    start = friday_start(friday)
    try:
        cursor = conn.execute(
            """
            INSERT INTO growth_cycles (
                week_start, status, created_at, updated_at, next_run_at
            )
            VALUES (?, 'open', ?, ?, ?)
            """,
            (friday.isoformat(), _iso(now), _iso(now), _iso(start)),
        )
    except sqlite3.IntegrityError:
        existing = _cycle_for_week(conn, friday)
        if existing is None:
            raise
        return int(existing["id"])
    cycle_id = int(cursor.lastrowid)
    queue: list[tuple[str, str, int | None]] = [("own", own.username, own.account_id)]
    seen = {own.username.casefold()}
    for username in competitors:
        if not username or username.casefold() in seen:
            continue
        seen.add(username.casefold())
        account = conn.execute(
            """
            SELECT id FROM accounts
            WHERE platform = 'instagram' AND lower(username) = lower(?)
            ORDER BY id
            LIMIT 1
            """,
            (username,),
        ).fetchone()
        queue.append(("competitor", username, int(account["id"]) if account else None))

    for position, (role, username, account_id) in enumerate(queue):
        last_success = _latest_success(conn, account_id)
        conn.execute(
            """
            INSERT INTO growth_cycle_accounts (
                cycle_id, account_id, username, role, position, status, step,
                attempts, next_run_at, last_success_measured_at, updated_at
            )
                VALUES (?, ?, ?, ?, ?, 'waiting', 'followers', 0, ?, ?, ?)
            """,
            (
                cycle_id,
                account_id,
                username,
                role,
                position,
                _iso(start) if position == 0 else None,
                last_success,
                _iso(now),
            ),
        )
    _refresh_cycle_status(conn, cycle_id, now)
    logger.info("Weekcyclus %s gestart met %s accounts.", friday.isoformat(), len(queue))
    return cycle_id


def _runnable_cycle(conn) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT c.*
        FROM growth_cycles c
        WHERE EXISTS (
            SELECT 1 FROM growth_cycle_accounts a
            WHERE a.cycle_id = c.id
              AND a.status IN ('waiting', 'waiting_api', 'running')
        )
        ORDER BY c.week_start ASC, c.id ASC
        LIMIT 1
        """
    ).fetchone()


def _cycle_for_week(conn, friday: date) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM growth_cycles WHERE week_start = ?",
        (friday.isoformat(),),
    ).fetchone()


def _cycle_row(conn, cycle_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM growth_cycles WHERE id = ?",
        (cycle_id,),
    ).fetchone()


def _account_rows(conn, cycle_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT * FROM growth_cycle_accounts
            WHERE cycle_id = ?
            ORDER BY position ASC, id ASC
            """,
            (cycle_id,),
        ).fetchall()
    )


def _next_fetch_target(conn, accounts: list[sqlite3.Row], now: datetime) -> sqlite3.Row | None:
    """Next account for one API step.

    A page that still has a cursor yields to an account that has not started,
    so one long walk cannot hold the other accounts all day. A permanently
    failed account does not block the rest.
    """
    pending: list[sqlite3.Row] = []
    for account in accounts:
        if account["status"] in {"error", "complete"}:
            continue
        if _step_name(account["step"]) == STEP_STORED:
            continue
        if _covered_by_newer_cycle(conn, account):
            continue
        if _held_for_new_friday(conn, account, now):
            continue
        pending.append(account)
    if not pending:
        return None
    due = [account for account in pending if _account_due(account, now)]
    pool = due or pending
    if due:
        starters = [account for account in due if not _is_paging(account)]
        if starters:
            return starters[0]
    return pool[0]


def _account_due(account, now: datetime) -> bool:
    if account["next_run_at"] is None:
        return True
    return _due(str(account["next_run_at"]), now)


def _is_paging(account) -> bool:
    return _step_name(account["step"]) == STEP_POSTS and bool(account["cursor_after"])


def _covered_by_newer_cycle(conn, account) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM growth_cycle_accounts newer
        JOIN growth_cycles newer_cycle ON newer_cycle.id = newer.cycle_id
        JOIN growth_cycles older_cycle ON older_cycle.id = ?
        WHERE lower(newer.username) = lower(?)
          AND newer_cycle.week_start > older_cycle.week_start
        LIMIT 1
        """,
        (int(account["cycle_id"]), account["username"]),
    ).fetchone()
    return row is not None


def _held_for_new_friday(conn, account, now: datetime) -> bool:
    """Do not start a new old-cycle account in the hour before Friday 01:00."""
    friday = week_friday(now)
    if now >= friday_start(friday):
        return False
    cycle = _cycle_row(conn, int(account["cycle_id"]))
    if cycle is None:
        return False
    if date.fromisoformat(str(cycle["week_start"])) >= friday:
        return False
    if account["measurement_id"] is not None or account["cursor_after"]:
        return False
    return True


def _week_cutoff(now: datetime) -> datetime:
    return _as_amsterdam(now) - WEEK_POST_WINDOW


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if len(text) >= 5 and text[-5] in "+-" and ":" not in text[-5:]:
        text = f"{text[:-2]}:{text[-2:]}"
    return _parse(text)


def _page_crossed_window(posts: tuple[GrowthMediaItem, ...], now: datetime) -> bool:
    cutoff = _week_cutoff(now)
    for post in posts:
        published = _published(post.published_at)
        if published is not None and published < cutoff:
            return True
    return False


def _posts_already_past_window(conn, account, now: datetime) -> bool:
    run_id = _cycle_run_id(conn, account)
    if run_id is None:
        return False
    latest = conn.execute(
        """
        SELECT MAX(measured_at) AS measured_at
        FROM post_measurements
        WHERE fetch_run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if latest is None or not latest["measured_at"]:
        return False
    rows = conn.execute(
        """
        SELECT p.published_at
        FROM post_measurements pm
        JOIN posts p ON p.id = pm.post_id
        WHERE pm.fetch_run_id = ?
          AND pm.measured_at = ?
        """,
        (run_id, latest["measured_at"]),
    ).fetchall()
    cutoff = _week_cutoff(now)
    newest: datetime | None = None
    for row in rows:
        published = _published(row["published_at"])
        if published is None:
            continue
        if newest is None or published > newest:
            newest = published
    return newest is not None and newest < cutoff


def _newer_week_not_started(conn, account, now: datetime) -> bool:
    cycle = _cycle_row(conn, int(account["cycle_id"]))
    if cycle is None:
        return False
    current = week_friday(now)
    week = date.fromisoformat(str(cycle["week_start"]))
    if current <= week:
        return False
    return _cycle_for_week(conn, current) is None


def _remaining_api_steps(accounts: list[sqlite3.Row]) -> int:
    steps = 0
    for account in accounts:
        if str(account["status"]) in {"complete", "error"}:
            continue
        if _step_name(account["step"]) == STEP_FOLLOWERS:
            steps += 2
        elif _step_name(account["step"]) == STEP_POSTS:
            steps += 1
    return steps


def _claim(conn, account_row_id: int, now: datetime) -> bool:
    cursor = conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET status = 'running', updated_at = ?
        WHERE id = ?
          AND status IN ('waiting', 'waiting_api')
        """,
        (_iso(now), account_row_id),
    )
    return cursor.rowcount == 1


def _release_stale_running(conn, now: datetime) -> None:
    cutoff = _iso(now - STALE_RUNNING)
    conn.execute(
        """
        UPDATE growth_cycle_accounts
        SET status = 'waiting', updated_at = ?
        WHERE status = 'running'
          AND updated_at < ?
        """,
        (_iso(now), cutoff),
    )


def _fresh_running(conn, now: datetime) -> bool:
    cutoff = _iso(now - STALE_RUNNING)
    row = conn.execute(
        """
        SELECT id FROM growth_cycle_accounts
        WHERE status = 'running' AND updated_at >= ?
        LIMIT 1
        """,
        (cutoff,),
    ).fetchone()
    return row is not None


def _repair_stored(conn, accounts: list[sqlite3.Row], now: datetime) -> sqlite3.Row | None:
    """Advance an account whose followers are already stored. No API call."""
    _ = now
    for account in accounts:
        if account["status"] in {"complete", "error", "running"}:
            continue
        if _step_name(account["step"]) != STEP_FOLLOWERS:
            continue
        if _measurement_followers(conn, account["measurement_id"]) is None:
            continue
        return account
    return None


def _measurement_followers(conn, measurement_id) -> int | None:
    if measurement_id is None:
        return None
    row = conn.execute(
        "SELECT followers_count FROM account_measurements WHERE id = ?",
        (int(measurement_id),),
    ).fetchone()
    if row is None or row["followers_count"] is None:
        return None
    return int(row["followers_count"])


def _refresh_cycle_status(conn, cycle_id: int, now: datetime) -> None:
    cycle = _cycle_row(conn, cycle_id)
    if cycle is None:
        return
    rows = _account_rows(conn, cycle_id)
    if not rows:
        return
    statuses = [str(row["status"]) for row in rows]
    runnable = any(status in {"waiting", "waiting_api", "running"} for status in statuses)
    if all(status == "complete" for status in statuses):
        status = "complete"
        finished = cycle["finished_at"] or _iso(now)
    elif runnable:
        week = date.fromisoformat(str(cycle["week_start"]))
        saturday = friday_start(week) + timedelta(days=1)
        status = "incomplete" if now >= saturday else "open"
        finished = None
    else:
        status = "incomplete"
        finished = cycle["finished_at"] or _iso(now)
    if runnable:
        conn.execute(
            """
            UPDATE growth_cycles
            SET status = ?, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, finished, _iso(now), cycle_id),
        )
    else:
        conn.execute(
            """
            UPDATE growth_cycles
            SET status = ?, finished_at = ?, next_run_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (status, finished, _iso(now), cycle_id),
        )


def _rate_limit_until(conn, now: datetime) -> datetime | None:
    state = get_discovery_rate_limit(conn)
    if state is None or not state.blocked_until:
        return None
    until = _parse(state.blocked_until)
    if until is None or until <= now:
        return None
    return until


def _foreign_fetch_running(conn) -> bool:
    row = conn.execute(
        """
        SELECT id FROM comparison_fetch_runs
        WHERE status = 'running'
          AND trigger_source != ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (TRIGGER_SOURCE,),
    ).fetchone()
    return row is not None


def _latest_success(conn, account_id: int | None) -> str | None:
    if account_id is None:
        return None
    row = conn.execute(
        """
        SELECT measured_at FROM account_measurements
        WHERE account_id = ? AND followers_count IS NOT NULL
        ORDER BY measured_at DESC, id DESC
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    return str(row["measured_at"]) if row else None


def _progress(conn, now: datetime) -> GrowthProgress:
    cycle = conn.execute(
        "SELECT * FROM growth_cycles ORDER BY week_start DESC, id DESC LIMIT 1"
    ).fetchone()
    if cycle is None:
        friday = week_friday(now)
        upcoming = friday_start(friday)
        if now < upcoming:
            next_run = _iso(upcoming)
            message = (
                "Er is nog geen weekcyclus. De volgende start is vrijdag om 01:00 "
                "(Europa/Amsterdam)."
            )
        else:
            next_run = None
            message = (
                "Er is nog geen weekcyclus voor deze week. "
                "De achterstand start bij de volgende plannerbeurt. "
                "Ontbrekende weken blijven een datagat."
            )
        accounts = _accounts_without_cycle(conn)
        newest = _newest_success(accounts)
        stale = newest is None or (now - newest) > timedelta(days=8)
        if stale and newest is not None:
            shown = newest.astimezone(AMSTERDAM).strftime("%d-%m-%Y %H:%M")
            message = (
                f"De gegevens zijn verouderd. Laatste geslaagde meting: {shown}. "
                "Er zijn geen volgersaantallen bijgeschat. "
                + message
            )
        return GrowthProgress(
            week_start=None,
            cycle_status="none",
            complete_count=0,
            total_count=len(accounts),
            next_run_at=next_run,
            stale=stale,
            message=message,
            accounts=tuple(accounts),
        )

    accounts = _account_rows(conn, int(cycle["id"]))
    expected = [
        str(row["week_start"])
        for row in conn.execute("SELECT week_start FROM growth_cycles ORDER BY week_start").fetchall()
    ]
    progress_rows: list[GrowthAccountProgress] = []
    newest: datetime | None = None
    for account in accounts:
        points = _points_for_week_growth(conn, account["account_id"])
        growth = describe_week_growth(points, expected_weeks=expected)
        last_at = None
        last_followers = None
        if account["account_id"] is not None:
            latest = conn.execute(
                """
                SELECT measured_at, followers_count
                FROM account_measurements
                WHERE account_id = ? AND followers_count IS NOT NULL
                ORDER BY measured_at DESC, id DESC
                LIMIT 1
                """,
                (int(account["account_id"]),),
            ).fetchone()
            if latest is not None:
                last_at = str(latest["measured_at"])
                last_followers = int(latest["followers_count"])
                parsed = _parse(last_at)
                if parsed is not None and (newest is None or parsed > newest):
                    newest = parsed
        name_row = None
        if account["account_id"] is not None:
            name_row = conn.execute(
                "SELECT name FROM accounts WHERE id = ?",
                (int(account["account_id"]),),
            ).fetchone()
        display = (name_row["name"] if name_row and name_row["name"] else None) or str(account["username"])
        progress_rows.append(
            GrowthAccountProgress(
                username=str(account["username"]),
                display_name=str(display),
                role=str(account["role"]),
                status=str(account["status"]),
                status_label=STATUS_LABELS.get(str(account["status"]), str(account["status"])),
                last_success_at=last_at,
                last_followers=last_followers,
                next_run_at=account["next_run_at"],
                error_message=account["error_message"],
                week_growth=growth,
            )
        )

    complete = sum(1 for row in progress_rows if row.status == "complete")
    stale = newest is None or (now - newest) > timedelta(days=8)
    remaining = _remaining_api_steps(accounts)
    message = _progress_message(
        week_start=str(cycle["week_start"]),
        cycle_status=str(cycle["status"]),
        complete=complete,
        total=len(progress_rows),
        next_run_at=cycle["next_run_at"],
        stale=stale,
        newest=newest,
        remaining_steps=remaining,
    )
    return GrowthProgress(
        week_start=str(cycle["week_start"]),
        cycle_status=str(cycle["status"]),
        complete_count=complete,
        total_count=len(progress_rows),
        next_run_at=cycle["next_run_at"],
        stale=stale,
        message=message,
        accounts=tuple(progress_rows),
    )


def _accounts_without_cycle(conn) -> list[GrowthAccountProgress]:
    own_id, competitors = get_preferences(conn)
    if own_id is None:
        host = get_discovery_host(conn)
        own_id = host.account_id if host is not None else None
    rows: list[tuple[str, int | None, str]] = []
    if own_id is not None:
        own = get_account_by_id(conn, own_id)
        if own is not None and own.username:
            rows.append(("own", own.account_id, own.username))
    for username in competitors:
        found = conn.execute(
            """
            SELECT id FROM accounts
            WHERE platform = 'instagram' AND lower(username) = lower(?)
            ORDER BY id LIMIT 1
            """,
            (username,),
        ).fetchone()
        rows.append(("competitor", int(found["id"]) if found else None, username))
    progress: list[GrowthAccountProgress] = []
    for role, account_id, username in rows:
        last_at = None
        last_followers = None
        display = username
        if account_id is not None:
            latest = conn.execute(
                """
                SELECT measured_at, followers_count FROM account_measurements
                WHERE account_id = ? AND followers_count IS NOT NULL
                ORDER BY measured_at DESC, id DESC LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if latest is not None:
                last_at = str(latest["measured_at"])
                last_followers = int(latest["followers_count"])
            name_row = conn.execute(
                "SELECT name FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if name_row is not None and name_row["name"]:
                display = str(name_row["name"])
        progress.append(
            GrowthAccountProgress(
                username=username,
                display_name=display,
                role=role,
                status="waiting",
                status_label=STATUS_LABELS["waiting"],
                last_success_at=last_at,
                last_followers=last_followers,
                next_run_at=None,
                error_message=None,
                week_growth=describe_week_growth([]),
            )
        )
    return progress


def _newest_success(accounts: list[GrowthAccountProgress]) -> datetime | None:
    newest: datetime | None = None
    for account in accounts:
        parsed = _parse(account.last_success_at)
        if parsed is not None and (newest is None or parsed > newest):
            newest = parsed
    return newest


def _points_for_week_growth(conn, account_id) -> list[WeekMeasurement]:
    """Week points, plus one older anchor when the first week has no predecessor.

    Daily history from before the weekly cycle is not turned into week points.
    """
    if account_id is None:
        return []
    rows = conn.execute(
        """
        SELECT m.measured_at, m.followers_count, c.week_start
        FROM account_measurements m
        LEFT JOIN growth_cycle_accounts a ON a.measurement_id = m.id
        LEFT JOIN growth_cycles c ON c.id = a.cycle_id
        WHERE m.account_id = ? AND m.followers_count IS NOT NULL
        ORDER BY m.measured_at ASC, m.id ASC
        """,
        (int(account_id),),
    ).fetchall()
    week_points = [
        WeekMeasurement(
            measured_at=str(row["measured_at"]),
            followers=int(row["followers_count"]),
            week_start=str(row["week_start"]) if row["week_start"] else None,
        )
        for row in rows
        if row["week_start"]
    ]
    if not week_points:
        return []
    historical = [row for row in rows if not row["week_start"]]
    if not historical:
        return week_points
    anchor = historical[-1]
    anchor_at = _parse(str(anchor["measured_at"]))
    first_at = _parse(week_points[0].measured_at)
    if anchor_at is None or first_at is None or anchor_at >= first_at:
        return week_points
    return [
        WeekMeasurement(
            measured_at=str(anchor["measured_at"]),
            followers=int(anchor["followers_count"]),
            week_start=None,
        ),
        *week_points,
    ]


def _progress_message(
    *,
    week_start: str,
    cycle_status: str,
    complete: int,
    total: int,
    next_run_at: str | None,
    stale: bool,
    newest: datetime | None,
    remaining_steps: int = 0,
) -> str:
    parts: list[str] = []
    if stale:
        if newest is None:
            parts.append("Er is nog geen geslaagde volgersmeting.")
        else:
            shown = newest.astimezone(AMSTERDAM).strftime("%d-%m-%Y %H:%M")
            parts.append(
                f"De gegevens zijn verouderd. Laatste geslaagde meting: {shown}. "
                "Ontbrekende weken blijven een datagat; er zijn geen volgersaantallen bijgeschat."
            )
    if cycle_status == "incomplete":
        parts.append(
            f"De weekcyclus van {week_start} is nog niet volledig "
            f"({complete} van {total} compleet). De voortgang blijft bewaard en wordt hervat."
        )
    elif cycle_status == "open":
        parts.append(f"Weekcyclus {week_start}: {complete} van {total} accounts compleet.")
    elif cycle_status == "complete":
        parts.append(f"Weekcyclus {week_start} is compleet ({complete} van {total}).")
    if remaining_steps and cycle_status in {"open", "incomplete"}:
        parts.append(f"Nog {remaining_steps} stappen, één per uur.")
        moment = _parse(next_run_at) if next_run_at else None
        if moment is not None and _finish_after_evening(moment, remaining_steps):
            parts.append(
                "Afronding voor 20:30 lukt zo niet. "
                "De ophaling wacht op de volgende poging; gemiste uren worden niet ingehaald."
            )
    if next_run_at and cycle_status in {"open", "incomplete"}:
        moment = _parse(next_run_at)
        if moment is not None:
            parts.append(
                "Volgende uitvoering: "
                + moment.astimezone(AMSTERDAM).strftime("%d-%m-%Y %H:%M")
                + "."
            )
    return " ".join(parts)


def _finish_after_evening(start: datetime, steps: int) -> bool:
    local = start.astimezone(AMSTERDAM)
    last = local + timedelta(hours=max(steps - 1, 0))
    evening = local.replace(hour=20, minute=30, second=0, microsecond=0)
    return last > evening
