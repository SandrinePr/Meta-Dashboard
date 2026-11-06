"""One small step per account for the 13:30 and 20:30 comparison.

A step stores a follower measurement and at most one page. New posts stay
behind the original watermark. Posts from the last 7 days are measured again,
one page per wake. A Meta limit keeps the cursor. Older history stays put.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db.comparison_repository import (
    ROLE_OWN,
    discovery_requests_blocked,
    ensure_default_comparison_accounts,
    find_active_fetch_run,
    finish_fetch_run,
    get_account_by_username,
    get_discovery_host,
    get_preferences,
    normalize_username,
    set_discovery_rate_limit,
    start_fetch_run,
    upsert_account_measurement,
    upsert_account_result,
    upsert_comparison_account,
)
from db.database import get_connection, initialize_database
from meta.client import (
    RATE_LIMIT_USER_MESSAGE,
    MetaClient,
    MetaRequestError,
    is_reduce_data_error,
)
from meta.discovery import (
    fetch_business_discovery,
    fetch_own_account_profile,
    iter_discovery_media_pages,
    optional_int,
    parse_meta_timestamp,
    safe_error_text,
)
from meta.rate_limit import is_rate_limit_error
from sync.comparison import (
    AccountFetchSummary,
    ComparisonSyncResult,
    _ensure_competitor_account,
    _store_media_batch,
)

logger = logging.getLogger(__name__)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
DAILY_PAGE = 25
RECENT_DAYS = 7
_MIN_PAGE = 5
_RATE_WAIT = timedelta(minutes=15)


class _RateLimitStop(Exception):
    def __init__(self, exc: MetaRequestError) -> None:
        super().__init__(safe_error_text(exc))
        self.exc = exc


@dataclass(frozen=True, slots=True)
class MediaPage:
    posts: list[dict[str, Any]]
    cursor_after: str | None
    reached_older: bool


def _amsterdam_day(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(AMSTERDAM).date().isoformat()


def _account_order(conn) -> list[tuple[str, bool]]:
    host = get_discovery_host(conn)
    own_name = normalize_username((host.username if host else None) or "")
    _own_id, competitors = get_preferences(conn)
    order: list[tuple[str, bool]] = []
    if own_name:
        order.append((own_name, True))
    for name in competitors:
        if name and name != own_name:
            order.append((name, False))
    return order


def _step(conn, day: str, trigger_source: str, username: str):
    return conn.execute(
        """
        SELECT phase, cursor_after, watermark_at, posts_stored, error_message
        FROM daily_comparison_steps
        WHERE amsterdam_day = ? AND trigger_source = ? AND username = ?
        """,
        (day, trigger_source, username),
    ).fetchone()


def _save_step(
    conn,
    *,
    day: str,
    trigger_source: str,
    username: str,
    phase: str,
    cursor_after: str | None,
    watermark_at: str | None,
    posts_stored: int,
    error_message: str | None,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO daily_comparison_steps (
            amsterdam_day, trigger_source, username, phase, cursor_after,
            watermark_at, posts_stored, error_message, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(amsterdam_day, trigger_source, username) DO UPDATE SET
            phase = excluded.phase,
            cursor_after = excluded.cursor_after,
            watermark_at = excluded.watermark_at,
            posts_stored = excluded.posts_stored,
            error_message = excluded.error_message,
            updated_at = excluded.updated_at
        """,
        (
            day,
            trigger_source,
            username,
            phase,
            cursor_after,
            watermark_at,
            posts_stored,
            error_message,
            updated_at,
        ),
    )


def daily_slot_complete(trigger_source: str, *, now: datetime | None = None) -> bool:
    """True when every account of this slot is saved as done or object-error."""
    initialize_database()
    moment = now or datetime.now(tz=timezone.utc)
    day = _amsterdam_day(moment)
    with get_connection() as conn:
        order = _account_order(conn)
        if not order:
            return False
        for username, _own in order:
            row = _step(conn, day, trigger_source, username)
            if row is None or row["phase"] not in {"done", "error"}:
                return False
    return True


def _open_scan(conn, username: str, *, day: str, trigger_source: str):
    """Unfinished page cursor. The saved boundary stays with that cursor."""
    return conn.execute(
        """
        SELECT phase, cursor_after, watermark_at
        FROM daily_comparison_steps
        WHERE username = ?
          AND phase IN ('posts', 'recent')
          AND cursor_after IS NOT NULL
          AND NOT (amsterdam_day = ? AND trigger_source = ?)
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (username, day, trigger_source),
    ).fetchone()


def _watermark(conn, account_id: int | None) -> datetime | None:
    if account_id is None:
        return None
    row = conn.execute(
        """
        SELECT MAX(published_at) AS newest
        FROM posts
        WHERE account_id = ? AND platform = 'instagram' AND published_at IS NOT NULL
        """,
        (account_id,),
    ).fetchone()
    if row is None or not row["newest"]:
        return None
    return parse_meta_timestamp(str(row["newest"]))


def _published_at(post: dict[str, Any]) -> datetime | None:
    stamp = post.get("timestamp") if isinstance(post.get("timestamp"), str) else None
    return parse_meta_timestamp(stamp)


def _newer_posts(posts: list[dict[str, Any]], watermark: datetime | None) -> list[dict[str, Any]]:
    if watermark is None:
        return posts
    return [
        post
        for post in posts
        if (published := _published_at(post)) is not None and published > watermark
    ]


def _recent_cutoff(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc) - timedelta(days=RECENT_DAYS)


def _known_recent_posts(
    posts: list[dict[str, Any]],
    *,
    watermark: datetime | None,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Posts already inside the recent window, including ones on this page."""
    kept: list[dict[str, Any]] = []
    for post in posts:
        published = _published_at(post)
        if published is None or published < cutoff:
            continue
        if watermark is not None and published > watermark:
            continue
        kept.append(post)
    return kept


def _passed_cutoff(posts: list[dict[str, Any]], cutoff: datetime) -> bool:
    return any(
        (published := _published_at(post)) is not None and published < cutoff
        for post in posts
    )


def _is_rate_limit(exc: MetaRequestError) -> bool:
    return bool(exc.is_rate_limited) or is_rate_limit_error(
        error_code=exc.error_code,
        message=str(exc),
        status_code=exc.status_code,
    )


def _load_profile(client: MetaClient, host_id: str, username: str, *, own: bool) -> dict[str, Any]:
    if own:
        return fetch_own_account_profile(client, host_id)
    discovery, _meta = fetch_business_discovery(
        client,
        host_id,
        username,
        include_media=False,
        include_account=True,
    )
    return discovery


def _load_media_page(
    client: MetaClient,
    host_id: str,
    username: str,
    *,
    own: bool,
    cursor: str | None,
    watermark: datetime | None,
) -> MediaPage:
    """One page. Code 1 retries the same page with a smaller limit."""
    if own:
        # A follow-up page uses only the cursor. Sending `since` again would
        # restart at the newest post and skip everything after page one.
        since = None
        if cursor is None and watermark is not None:
            since = int(watermark.timestamp())
        posts, cursor_after = client.get_instagram_media_page(
            host_id,
            since=since,
            after=cursor,
            limit=DAILY_PAGE,
        )
        if watermark is None:
            return MediaPage(posts, None, True)
        reached = any(
            (published := parse_meta_timestamp(post.get("timestamp"))) is not None
            and published <= watermark
            for post in posts
            if isinstance(post.get("timestamp"), str)
        )
        return MediaPage(posts, cursor_after, bool(reached or not cursor_after))

    limit = DAILY_PAGE
    cutoff = watermark or datetime(1970, 1, 1, tzinfo=timezone.utc)
    while True:
        try:
            page = next(
                iter_discovery_media_pages(
                    client,
                    host_id,
                    username,
                    cutoff_utc=cutoff,
                    media_limit=limit,
                    max_pages=1,
                    resume_after=cursor,
                )
            )
            break
        except StopIteration:
            return MediaPage([], None, True)
        except MetaRequestError as exc:
            if is_reduce_data_error(exc) and limit > _MIN_PAGE:
                limit = max(_MIN_PAGE, limit // 2)
                logger.info("Meta code 1 bij @%s; kleinere pagina limit=%s", username, limit)
                continue
            raise
    if watermark is None:
        return MediaPage(page.posts, None, True)
    return MediaPage(
        page.posts,
        page.cursor_after,
        bool(page.reached_cutoff or not page.cursor_after),
    )


def _remember_rate_limit(conn, exc: MetaRequestError) -> str | None:
    blocked_until = exc.blocked_until
    if not blocked_until:
        wait = exc.retry_after_seconds if exc.retry_after_seconds else int(_RATE_WAIT.total_seconds())
        blocked_until = (datetime.now(tz=timezone.utc) + timedelta(seconds=wait)).isoformat()
    set_discovery_rate_limit(
        conn,
        error_code=exc.error_code,
        message=RATE_LIMIT_USER_MESSAGE,
        retry_after_seconds=exc.retry_after_seconds,
        blocked_until=blocked_until,
        usage=exc.usage_headers,
    )
    return blocked_until


def _fail_account(
    conn,
    *,
    day: str,
    trigger_source: str,
    username: str,
    account_id: int | None,
    fetch_run_id: int,
    measured_at: str,
    cursor_after: str | None,
    watermark_at: str | None,
    posts_stored: int,
    message: str,
) -> AccountFetchSummary:
    logger.warning(
        "Meta-object @%s: %s De overige accounts gaan door.",
        username,
        message,
    )
    _save_step(
        conn,
        day=day,
        trigger_source=trigger_source,
        username=username,
        phase="error",
        cursor_after=cursor_after,
        watermark_at=watermark_at,
        posts_stored=posts_stored,
        error_message=message,
        updated_at=measured_at,
    )
    summary = AccountFetchSummary(
        username=username,
        account_id=account_id,
        status="failed",
        posts_fetched=posts_stored,
        error_message=message,
    )
    if account_id is not None:
        upsert_account_result(
            conn,
            fetch_run_id=fetch_run_id,
            account_id=account_id,
            status="failed",
            posts_fetched=posts_stored,
            error_message=message,
        )
    return summary


def run_daily_update(
    *,
    trigger_source: str,
    client: MetaClient | None = None,
    now: datetime | None = None,
) -> ComparisonSyncResult:
    """Followers, one new-post page, then recent likes and comments."""
    initialize_database()
    moment = now or datetime.now(tz=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    measured_at = moment.astimezone(timezone.utc).isoformat()
    day = _amsterdam_day(moment)
    result = ComparisonSyncResult(measured_at=measured_at)
    api = client or MetaClient.from_settings()

    with get_connection() as conn:
        ensure_default_comparison_accounts(conn)
        blocked = discovery_requests_blocked(conn)
        if blocked is not None:
            result.status = "skipped"
            result.rate_limited = True
            result.rate_limit_blocked_until = blocked.blocked_until
            result.skipped_reason = RATE_LIMIT_USER_MESSAGE
            logger.info(
                "Dagelijkse vergelijking wacht op de Meta-limiet tot %s.",
                blocked.blocked_until,
            )
            conn.commit()
            return result

        active = find_active_fetch_run(conn)
        if active is not None:
            result.status = "skipped"
            result.run_id = active
            result.skipped_reason = f"Er loopt al een ophaalrun (id={active})."
            conn.commit()
            return result

        host = get_discovery_host(conn)
        if host is None or not host.external_id:
            result.status = "failed"
            result.error_message = "Geen discovery-hostaccount geconfigureerd."
            conn.commit()
            return result

        order = _account_order(conn)
        run_id = start_fetch_run(
            conn,
            trigger_source=trigger_source,
            measured_at=measured_at,
            period_start=measured_at,
            period_end=measured_at,
        )
        result.run_id = run_id
        conn.commit()

        rate_hit = False
        stopped_username = ""
        try:
            for username, own in order:
                row = _step(conn, day, trigger_source, username)
                phase = str(row["phase"]) if row is not None else "pending"
                if phase in {"done", "error"}:
                    continue
                cursor = str(row["cursor_after"]) if row is not None and row["cursor_after"] else None
                watermark_at = (
                    str(row["watermark_at"]) if row is not None and row["watermark_at"] else None
                )
                posts_stored = int(row["posts_stored"] or 0) if row is not None else 0
                known = get_account_by_username(conn, username)
                account_id = known.account_id if known is not None else None
                try:
                    if phase == "pending":
                        profile = _load_profile(api, host.external_id, username, own=own)
                        if own:
                            account_id = upsert_comparison_account(
                                conn,
                                external_id=str(profile.get("id") or host.external_id),
                                username=profile.get("username") or username,
                                name=profile.get("name"),
                                comparison_role=ROLE_OWN,
                                is_discovery_host=True,
                                access_status="ok",
                                last_access_error=None,
                            )
                        else:
                            account_id = _ensure_competitor_account(
                                conn,
                                username=username,
                                account_payload=profile,
                            )
                        followers = optional_int(profile.get("followers_count"))
                        media_count = optional_int(profile.get("media_count"))
                        upsert_account_measurement(
                            conn,
                            account_id=account_id,
                            fetch_run_id=run_id,
                            measured_at=measured_at,
                            followers_count=followers,
                            media_count=media_count,
                        )
                        carried = _open_scan(
                            conn, username, day=day, trigger_source=trigger_source
                        )
                        if carried is not None:
                            cursor = str(carried["cursor_after"])
                            watermark_at = (
                                str(carried["watermark_at"]) if carried["watermark_at"] else None
                            )
                            phase = str(carried["phase"] or "posts")
                            conn.execute(
                                """
                                UPDATE daily_comparison_steps
                                SET phase = 'done', cursor_after = NULL, updated_at = ?
                                WHERE username = ?
                                  AND phase IN ('posts', 'recent')
                                  AND cursor_after IS NOT NULL
                                  AND NOT (amsterdam_day = ? AND trigger_source = ?)
                                """,
                                (measured_at, username, day, trigger_source),
                            )
                        else:
                            watermark = _watermark(conn, account_id)
                            watermark_at = (
                                watermark.astimezone(timezone.utc).isoformat()
                                if watermark
                                else None
                            )
                            phase = "posts"
                        _save_step(
                            conn,
                            day=day,
                            trigger_source=trigger_source,
                            username=username,
                            phase=phase,
                            cursor_after=cursor,
                            watermark_at=watermark_at,
                            posts_stored=posts_stored,
                            error_message=None,
                            updated_at=measured_at,
                        )
                        conn.commit()
                        logger.info(
                            "Profielmeting @%s opgeslagen (followers=%s) om %s.",
                            username,
                            followers,
                            measured_at,
                        )

                    watermark = parse_meta_timestamp(watermark_at) if watermark_at else None
                    cutoff = _recent_cutoff(moment)
                    page = _load_media_page(
                        api,
                        host.external_id,
                        username,
                        own=own,
                        cursor=cursor,
                        watermark=watermark,
                    )
                    if phase == "recent":
                        fresh: list[dict[str, Any]] = []
                        recent = _known_recent_posts(page.posts, watermark=None, cutoff=cutoff)
                    else:
                        fresh = _newer_posts(page.posts, watermark)
                        fresh_ids = {str(post.get("id") or "") for post in fresh}
                        recent = [
                            post
                            for post in _known_recent_posts(
                                page.posts, watermark=watermark, cutoff=cutoff
                            )
                            if str(post.get("id") or "") not in fresh_ids
                        ]
                    if account_id is None:
                        raise MetaRequestError(f"Geen Instagram-ID voor @{username}.")
                    stored, _created = _store_media_batch(
                        conn,
                        account_id=account_id,
                        posts=fresh,
                        fetch_run_id=run_id,
                        measured_at=measured_at,
                    )
                    remeasured, _again = _store_media_batch(
                        conn,
                        account_id=account_id,
                        posts=recent,
                        fetch_run_id=run_id,
                        measured_at=measured_at,
                    )
                    posts_stored += stored
                    if phase == "recent":
                        keep_recent = bool(
                            page.cursor_after
                            and not page.reached_older
                            and not _passed_cutoff(page.posts, cutoff)
                        )
                        phase = "recent" if keep_recent else "done"
                        next_cursor = page.cursor_after if keep_recent else None
                    elif page.cursor_after and not page.reached_older:
                        phase = "posts"
                        next_cursor = page.cursor_after
                    elif page.cursor_after and not _passed_cutoff(page.posts, cutoff):
                        phase = "recent"
                        next_cursor = page.cursor_after
                        watermark_at = cutoff.astimezone(timezone.utc).isoformat()
                    else:
                        phase = "done"
                        next_cursor = None
                    _save_step(
                        conn,
                        day=day,
                        trigger_source=trigger_source,
                        username=username,
                        phase=phase,
                        cursor_after=next_cursor,
                        watermark_at=watermark_at,
                        posts_stored=posts_stored,
                        error_message=None,
                        updated_at=measured_at,
                    )
                    summary = AccountFetchSummary(
                        username=username,
                        account_id=account_id,
                        status="success" if phase == "done" else "partial",
                        posts_fetched=stored,
                        is_complete=phase == "done",
                    )
                    upsert_account_result(
                        conn,
                        fetch_run_id=run_id,
                        account_id=account_id,
                        status="success" if phase == "done" else "partial",
                        posts_fetched=stored,
                        is_complete=phase == "done",
                    )
                    result.accounts.append(summary)
                    conn.commit()
                    logger.info(
                        "Dagelijkse stap @%s posts_nieuw=%s opnieuw_gemeten=%s fase=%s.",
                        username,
                        stored,
                        remeasured,
                        phase,
                    )
                except MetaRequestError as exc:
                    if _is_rate_limit(exc):
                        _save_step(
                            conn,
                            day=day,
                            trigger_source=trigger_source,
                            username=username,
                            phase=phase,
                            cursor_after=cursor,
                            watermark_at=watermark_at,
                            posts_stored=posts_stored,
                            error_message=RATE_LIMIT_USER_MESSAGE,
                            updated_at=measured_at,
                        )
                        conn.commit()
                        stopped_username = username
                        raise _RateLimitStop(exc) from exc
                    message = safe_error_text(exc)
                    if exc.error_code == 100:
                        message = f"code 100: {message}"
                    elif is_reduce_data_error(exc):
                        message = f"code 1: {message}"
                    result.accounts.append(
                        _fail_account(
                            conn,
                            day=day,
                            trigger_source=trigger_source,
                            username=username,
                            account_id=account_id,
                            fetch_run_id=run_id,
                            measured_at=measured_at,
                            cursor_after=cursor,
                            watermark_at=watermark_at,
                            posts_stored=posts_stored,
                            message=message,
                        )
                    )
                    conn.commit()
        except _RateLimitStop as stopped:
            rate_hit = True
            result.rate_limited = True
            result.error_message = RATE_LIMIT_USER_MESSAGE
            result.rate_limit_blocked_until = _remember_rate_limit(conn, stopped.exc)
            logger.warning(
                "Meta-limiet bij de dagelijkse vergelijking. Volgende stap hervat bij @%s.",
                stopped_username,
            )

        failed = any(item.status == "failed" for item in result.accounts)
        result.status = "completed_with_errors" if rate_hit or failed else "completed"
        finish_fetch_run(
            conn,
            run_id,
            status=result.status,
            error_message=result.error_message,
        )
        conn.commit()

    updated = [item.username for item in result.accounts if item.status in {"success", "partial"}]
    logger.info(
        "Dagelijkse vergelijking trigger=%s status=%s run_id=%s bijgewerkt=%s",
        trigger_source,
        result.status,
        result.run_id,
        ",".join(updated) if updated else "-",
    )
    return result
