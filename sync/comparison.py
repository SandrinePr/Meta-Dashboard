"""Fetch and store Instagram comparison data via Business Discovery."""

from __future__ import annotations

import json
import logging
import time as time_module
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from config import get_settings
from db.comparison_repository import (
    ROLE_COMPETITOR,
    ROLE_OWN,
    account_result_exists,
    clear_discovery_checkpoint,
    clear_discovery_rate_limit,
    discovery_requests_blocked,
    ensure_default_comparison_accounts,
    find_active_fetch_run,
    finish_fetch_run,
    get_account_by_id,
    get_account_by_username,
    get_discovery_checkpoint,
    get_discovery_host,
    get_discovery_rate_limit,
    get_latest_success_measured_at,
    get_preferences,
    list_known_post_external_ids,
    log_discovery_request,
    normalize_username,
    save_discovery_checkpoint,
    set_discovery_rate_limit,
    start_fetch_run,
    upsert_account_measurement,
    upsert_account_result,
    upsert_comparison_account,
    upsert_comparison_post,
    upsert_post_measurement,
    utc_now_iso,
)
from db.database import get_connection, initialize_database
from meta.client import (
    RATE_LIMIT_USER_MESSAGE,
    MetaClient,
    MetaClientError,
    MetaRequestError,
)
from meta.discovery import (
    configured_media_page_limit,
    fetch_business_discovery,
    fetch_own_account_profile,
    iter_discovery_media_pages,
    optional_int,
    parse_meta_timestamp,
    safe_error_text,
    to_utc_iso,
)
from meta.rate_limit import is_rate_limit_error
from sync.mappers import parse_published_at

logger = logging.getLogger(__name__)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
DEFAULT_FALLBACK_USERNAME = "theartofliving.online"
ProgressCallback = Callable[[str, float | None], None]


@dataclass
class AccountFetchSummary:
    username: str
    account_id: int | None = None
    status: str = "pending"
    posts_fetched: int = 0
    is_complete: bool = False
    oldest_post_at: str | None = None
    newest_post_at: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    error_message: str | None = None
    followers_count: int | None = None
    media_count: int | None = None
    rate_limited: bool = False
    retry_after_seconds: int | None = None
    blocked_until: str | None = None
    usage_headers: dict[str, Any] = field(default_factory=dict)
    error_code: int | None = None
    # True when this run continued an open checkpoint (older pages keep prior measured_at).
    resumed: bool = False


@dataclass
class ComparisonSyncResult:
    run_id: int | None = None
    status: str = "failed"
    measured_at: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    accounts: list[AccountFetchSummary] = field(default_factory=list)
    error_message: str | None = None
    skipped_reason: str | None = None
    rate_limited: bool = False
    rate_limit_blocked_until: str | None = None
    last_success_measured_at: str | None = None


def amsterdam_today() -> date:
    return datetime.now(tz=AMSTERDAM).date()


def three_calendar_months_window(
    today: date | None = None,
) -> tuple[datetime, datetime]:
    """Inclusive publication window: today minus 3 calendar months → end of today (Amsterdam)."""
    day = today or amsterdam_today()
    # Reuse month subtraction with day clamp.
    month_index = day.month - 1 - 3
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    import calendar

    last_day = calendar.monthrange(year, month)[1]
    start_day = date(year, month, min(day.day, last_day))
    start_local = datetime.combine(start_day, time.min, tzinfo=AMSTERDAM)
    end_local = datetime.combine(day, time.max.replace(microsecond=0), tzinfo=AMSTERDAM)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _emit(progress: ProgressCallback | None, message: str, fraction: float | None) -> None:
    if progress is not None:
        progress(message, fraction)
    logger.info(message)


def _store_media_batch(
    conn,
    *,
    account_id: int,
    posts: list[dict[str, Any]],
    fetch_run_id: int,
    measured_at: str,
) -> tuple[int, int]:
    """Upsert posts/measurements. Returns (touched, newly_created)."""
    touched = 0
    created = 0
    for payload in posts:
        external_id = str(payload.get("id") or "").strip()
        if not external_id:
            continue
        result = upsert_comparison_post(
            conn,
            account_id=account_id,
            external_id=external_id,
            text=payload.get("caption") if isinstance(payload.get("caption"), str) else None,
            permalink=payload.get("permalink")
            if isinstance(payload.get("permalink"), str)
            else None,
            media_type=payload.get("media_type")
            if isinstance(payload.get("media_type"), str)
            else None,
            media_product_type=payload.get("media_product_type")
            if isinstance(payload.get("media_product_type"), str)
            else None,
            published_at=parse_published_at(
                payload.get("timestamp") if isinstance(payload.get("timestamp"), str) else None
            ),
            raw_json=json.dumps(payload, ensure_ascii=False),
        )
        upsert_post_measurement(
            conn,
            post_id=result.row_id,
            fetch_run_id=fetch_run_id,
            measured_at=measured_at,
            like_count=optional_int(payload.get("like_count")),
            comments_count=optional_int(payload.get("comments_count")),
        )
        touched += 1
        if result.created:
            created += 1
    return touched, created


def _count_unique_instagram_posts(conn, account_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM posts
        WHERE account_id = ? AND platform = 'instagram'
        """,
        (account_id,),
    ).fetchone()
    return int(row["c"] or 0)


def _fetch_own_account(
    client: MetaClient,
    conn,
    *,
    host,
    fetch_run_id: int,
    measured_at: str,
    period_start: datetime,
    period_end: datetime,
    include_media: bool = True,
) -> AccountFetchSummary:
    summary = AccountFetchSummary(
        username=host.username or DEFAULT_FALLBACK_USERNAME,
        account_id=host.account_id,
        period_start=to_utc_iso(period_start),
        period_end=to_utc_iso(period_end),
    )
    try:
        profile = fetch_own_account_profile(client, host.external_id)
        account_id = upsert_comparison_account(
            conn,
            external_id=str(profile.get("id") or host.external_id),
            username=profile.get("username") or host.username,
            name=profile.get("name") or host.name,
            comparison_role=ROLE_OWN,
            is_discovery_host=True,
            access_status="ok",
            last_access_error=None,
        )
        summary.account_id = account_id
        summary.username = normalize_username(str(profile.get("username") or host.username or ""))
        summary.followers_count = optional_int(profile.get("followers_count"))
        summary.media_count = optional_int(profile.get("media_count"))
        upsert_account_measurement(
            conn,
            account_id=account_id,
            fetch_run_id=fetch_run_id,
            measured_at=measured_at,
            followers_count=summary.followers_count,
            media_count=summary.media_count,
        )

        if not include_media:
            summary.status = "success"
            summary.is_complete = True
            upsert_account_result(
                conn,
                fetch_run_id=fetch_run_id,
                account_id=account_id,
                status="success",
                posts_fetched=0,
                period_start=summary.period_start,
                period_end=summary.period_end,
                is_complete=True,
            )
            logger.info(
                "Profielmeting eigen account @%s opgeslagen (followers=%s).",
                summary.username,
                summary.followers_count,
            )
            return summary

        since_ts = int(period_start.timestamp())
        media_items = client.get_instagram_media(
            host.external_id,
            since=since_ts,
            limit=100,
        )
        # Keep only items within inclusive window (API since is approximate).
        filtered: list[dict[str, Any]] = []
        oldest = None
        newest = None
        for item in media_items:
            if not isinstance(item, dict):
                continue
            ts = parse_meta_timestamp(
                item.get("timestamp") if isinstance(item.get("timestamp"), str) else None
            )
            if ts is None:
                continue
            if ts < period_start or ts > period_end:
                continue
            filtered.append(item)
            if oldest is None or ts < oldest:
                oldest = ts
            if newest is None or ts > newest:
                newest = ts

        _store_media_batch(
            conn,
            account_id=account_id,
            posts=filtered,
            fetch_run_id=fetch_run_id,
            measured_at=measured_at,
        )
        # Own media pagination via get_paginated already completes; treat as complete
        # when API returned data or account has zero posts in window.
        complete = True
        summary.posts_fetched = _count_unique_instagram_posts(conn, account_id)
        summary.is_complete = complete
        summary.oldest_post_at = to_utc_iso(oldest) if oldest else None
        summary.newest_post_at = to_utc_iso(newest) if newest else None
        summary.status = "success"
        upsert_account_result(
            conn,
            fetch_run_id=fetch_run_id,
            account_id=account_id,
            status="success",
            posts_fetched=summary.posts_fetched,
            period_start=summary.period_start,
            period_end=summary.period_end,
            is_complete=complete,
            oldest_post_at=summary.oldest_post_at,
            newest_post_at=summary.newest_post_at,
        )
    except MetaClientError as exc:
        summary.status = "failed"
        if isinstance(exc, MetaRequestError) and exc.is_rate_limited:
            summary.rate_limited = True
            summary.error_message = RATE_LIMIT_USER_MESSAGE
            summary.retry_after_seconds = exc.retry_after_seconds
            summary.blocked_until = exc.blocked_until
            summary.usage_headers = dict(exc.usage_headers or {})
            summary.error_code = exc.error_code
            _record_rate_limit(conn, exc)
        else:
            summary.error_message = safe_error_text(exc)
        if summary.account_id is not None:
            upsert_account_result(
                conn,
                fetch_run_id=fetch_run_id,
                account_id=summary.account_id,
                status="failed",
                period_start=summary.period_start,
                period_end=summary.period_end,
                error_message=summary.error_message,
            )
        logger.warning("Own account fetch failed: %s", summary.error_message)
    return summary


def _ensure_competitor_account(
    conn,
    *,
    username: str,
    account_payload: dict[str, Any],
) -> int:
    """Resolve/create competitor account row; replace pending: placeholders."""
    external_id = str(account_payload.get("id") or "").strip()
    if not external_id:
        raise MetaRequestError(f"Geen Instagram-ID voor @{username}.")
    pending = get_account_by_username(conn, username)
    if (
        pending is not None
        and pending.external_id.startswith("pending:")
        and pending.external_id != external_id
    ):
        conn.execute(
            """
            UPDATE accounts
            SET external_id = ?,
                name = COALESCE(?, name),
                username = COALESCE(?, username),
                comparison_role = ?,
                access_status = 'ok',
                last_access_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                external_id,
                account_payload.get("name"),
                account_payload.get("username") or username,
                ROLE_COMPETITOR,
                utc_now_iso(),
                pending.account_id,
            ),
        )
        return pending.account_id
    return upsert_comparison_account(
        conn,
        external_id=external_id,
        username=account_payload.get("username") or username,
        name=account_payload.get("name"),
        comparison_role=ROLE_COMPETITOR,
        is_discovery_host=False,
        access_status="ok",
        last_access_error=None,
    )


def _fetch_competitor_profile(
    client: MetaClient,
    conn,
    *,
    host,
    username: str,
    fetch_run_id: int,
    measured_at: str,
) -> AccountFetchSummary:
    """Cheap Business Discovery profile call; persist followers immediately.

    Success here does not mean post metrics were refreshed.
    """
    summary = AccountFetchSummary(username=normalize_username(username))
    existing = get_account_by_username(conn, summary.username)
    if existing:
        summary.account_id = existing.account_id
    try:
        discovery, profile_meta = fetch_business_discovery(
            client,
            host.external_id,
            summary.username,
            include_media=False,
            include_account=True,
        )
        account_payload = {
            key: discovery.get(key)
            for key in ("id", "username", "name", "followers_count", "media_count")
            if discovery.get(key) is not None or key in {"id", "username", "name"}
        }
        if not account_payload.get("id"):
            raise MetaRequestError(f"Geen Instagram-ID voor @{summary.username}.")
        summary.account_id = _ensure_competitor_account(
            conn,
            username=summary.username,
            account_payload=account_payload,
        )
        summary.username = normalize_username(
            str(account_payload.get("username") or summary.username)
        )
        summary.followers_count = optional_int(account_payload.get("followers_count"))
        summary.media_count = optional_int(account_payload.get("media_count"))
        log_discovery_request(
            conn,
            fetch_run_id=fetch_run_id,
            account_id=summary.account_id,
            username=summary.username,
            page_index=0,
            page_size=0,
            posts_received=0,
            duration_ms=profile_meta.duration_ms,
            usage_headers=dict(profile_meta.usage_headers or {}),
            cursor_before=None,
            cursor_after=None,
            status="ok",
            measured_at=measured_at,
        )
        if summary.followers_count is not None or summary.media_count is not None:
            upsert_account_measurement(
                conn,
                account_id=summary.account_id,
                fetch_run_id=fetch_run_id,
                measured_at=measured_at,
                followers_count=summary.followers_count,
                media_count=summary.media_count,
            )
        summary.status = "success"
        conn.commit()
        logger.info(
            "Profielmeting @%s opgeslagen (followers=%s).",
            summary.username,
            summary.followers_count,
        )
    except MetaClientError as exc:
        duration_ms = getattr(exc, "duration_ms", None)
        if isinstance(exc, MetaRequestError) and exc.is_rate_limited:
            summary.rate_limited = True
            summary.error_message = RATE_LIMIT_USER_MESSAGE
            summary.retry_after_seconds = exc.retry_after_seconds
            summary.blocked_until = exc.blocked_until
            summary.usage_headers = dict(exc.usage_headers or {})
            summary.error_code = exc.error_code
            _record_rate_limit(conn, exc)
            log_status = "rate_limited"
        else:
            summary.error_message = safe_error_text(exc)
            log_status = "error"
        summary.status = "failed"
        log_discovery_request(
            conn,
            fetch_run_id=fetch_run_id,
            account_id=summary.account_id,
            username=summary.username,
            page_index=0,
            page_size=0,
            posts_received=0,
            duration_ms=float(duration_ms) if duration_ms is not None else None,
            usage_headers=dict(summary.usage_headers or {}),
            cursor_before=None,
            cursor_after=None,
            status=log_status,
            measured_at=measured_at,
            error_message=summary.error_message,
        )
        conn.commit()
    return summary


def _fetch_competitor(
    client: MetaClient,
    conn,
    *,
    host,
    username: str,
    fetch_run_id: int,
    measured_at: str,
    period_start: datetime,
    period_end: datetime,
) -> AccountFetchSummary:
    """Fetch competitor media page-by-page; persist progress on interruption."""
    summary = AccountFetchSummary(
        username=normalize_username(username),
        period_start=to_utc_iso(period_start),
        period_end=to_utc_iso(period_end),
    )
    existing = get_account_by_username(conn, summary.username)
    if existing:
        summary.account_id = existing.account_id

    period_start_iso = summary.period_start or to_utc_iso(period_start)
    period_end_iso = summary.period_end or to_utc_iso(period_end)
    media_limit = configured_media_page_limit()

    resume_after: str | None = None
    known_account: dict[str, Any] = {}
    pages_done = 0
    posts_stored_total = 0
    oldest_f: datetime | None = None
    newest_f: datetime | None = None
    seen_ids: set[str] = set()
    last_cursor: str | None = None
    account_payload: dict[str, Any] = {}
    # When resuming, do not re-label existing follower/post snapshots as measured "today".
    # Fresh refresh re-measures known posts in the window with a new measured_at.
    # Also skip if a profile-first phase already stored today's followers.
    record_follower_measurement = not _has_follower_measurement_on_day(
        conn, summary.account_id
    )

    if existing is not None:
        checkpoint = get_discovery_checkpoint(conn, existing.account_id)
        # Resume from cursor; never page older than today's rolling 3-calendar-month cutoff.
        if checkpoint is not None and not checkpoint.is_complete:
            current_start, current_end = three_calendar_months_window()
            try:
                cp_start = datetime.fromisoformat(
                    str(checkpoint.period_start).replace("Z", "+00:00")
                )
                cp_end = datetime.fromisoformat(
                    str(checkpoint.period_end).replace("Z", "+00:00")
                )
                if cp_start.tzinfo is None:
                    cp_start = cp_start.replace(tzinfo=timezone.utc)
                if cp_end.tzinfo is None:
                    cp_end = cp_end.replace(tzinfo=timezone.utc)
            except ValueError:
                logger.warning(
                    "Checkpointperiode voor @%s ongeldig; gebruik actueel venster.",
                    summary.username,
                )
            else:
                # Cutoff = later of checkpoint start and current 3-month start (don't go further back).
                period_start = max(cp_start, current_start)
                period_end = max(cp_end, current_end)
                period_start_iso = to_utc_iso(period_start)
                period_end_iso = to_utc_iso(period_end)
                summary.period_start = period_start_iso
                summary.period_end = period_end_iso
                summary.resumed = True
                resume_after = checkpoint.cursor_after
                last_cursor = checkpoint.cursor_after
                pages_done = checkpoint.pages_fetched
                posts_stored_total = checkpoint.posts_fetched
                record_follower_measurement = False
                # Only while resuming: skip IDs already imported on earlier pages
                # so previously stored cijfers keep their original measured_at.
                seen_ids |= list_known_post_external_ids(conn, existing.account_id)
                if checkpoint.account_payload_json:
                    try:
                        known_account = json.loads(checkpoint.account_payload_json)
                        account_payload = dict(known_account)
                    except json.JSONDecodeError:
                        known_account = {}
                logger.info(
                    "Hervatten @%s vanaf cursor na %s pagina's (%s posts, cutoff %s .. %s)",
                    summary.username,
                    pages_done,
                    posts_stored_total,
                    period_start_iso,
                    period_end_iso,
                )
        else:
            logger.info(
                "Verse meting @%s: bestaande posts in venster opnieuw meten "
                "(geen open checkpoint).",
                summary.username,
            )

    try:
        complete = False
        stopped_reason = "voltooid"

        # Profile-only fallback when today's followers are still missing (e.g. resume
        # after a partial media run). Prefer the batch profile phase in
        # run_comparison_sync so #4 during media cannot block other accounts.
        if not _has_follower_measurement_on_day(conn, summary.account_id):
            profile_summary = _fetch_competitor_profile(
                client,
                conn,
                host=host,
                username=summary.username,
                fetch_run_id=fetch_run_id,
                measured_at=measured_at,
            )
            if profile_summary.rate_limited:
                summary.rate_limited = True
                summary.status = "failed"
                summary.error_message = RATE_LIMIT_USER_MESSAGE
                summary.retry_after_seconds = profile_summary.retry_after_seconds
                summary.blocked_until = profile_summary.blocked_until
                summary.usage_headers = dict(profile_summary.usage_headers or {})
                summary.error_code = profile_summary.error_code
                summary.account_id = profile_summary.account_id or summary.account_id
                return summary
            if profile_summary.status == "failed":
                summary.status = "failed"
                summary.error_message = profile_summary.error_message
                summary.account_id = profile_summary.account_id or summary.account_id
                return summary
            summary.account_id = profile_summary.account_id or summary.account_id
            summary.username = profile_summary.username or summary.username
            summary.followers_count = profile_summary.followers_count
            summary.media_count = profile_summary.media_count
            existing_acc = get_account_by_username(conn, summary.username)
            if existing_acc is not None and existing_acc.external_id:
                known_account = {
                    "id": existing_acc.external_id,
                    "username": summary.username,
                    "name": existing_acc.name,
                    "followers_count": summary.followers_count,
                    "media_count": summary.media_count,
                }
                account_payload = dict(known_account)
            record_follower_measurement = False

        for page in iter_discovery_media_pages(
            client,
            host.external_id,
            summary.username,
            cutoff_utc=period_start,
            period_end=period_end,
            media_limit=media_limit,
            resume_after=resume_after,
            known_account=known_account or None,
            known_seen_ids=seen_ids,
        ):
            account_payload = page.account_payload or account_payload
            # Always refresh pending→real IG id when discovery returns an account id,
            # even if a placeholder account_id was already selected.
            if account_payload.get("id"):
                summary.account_id = _ensure_competitor_account(
                    conn,
                    username=summary.username,
                    account_payload=account_payload,
                )
                summary.username = normalize_username(
                    str(account_payload.get("username") or summary.username)
                )
                summary.followers_count = optional_int(account_payload.get("followers_count"))
                summary.media_count = optional_int(account_payload.get("media_count"))
                if record_follower_measurement and (
                    summary.followers_count is not None or summary.media_count is not None
                ):
                    upsert_account_measurement(
                        conn,
                        account_id=summary.account_id,
                        fetch_run_id=fetch_run_id,
                        measured_at=measured_at,
                        followers_count=summary.followers_count,
                        media_count=summary.media_count,
                    )
                    # Only once per fresh fetch; later pages must not re-stamp the day.
                    record_follower_measurement = False

            pages_done += 1
            log_discovery_request(
                conn,
                fetch_run_id=fetch_run_id,
                account_id=summary.account_id,
                username=summary.username,
                page_index=pages_done,
                page_size=page.page_size,
                posts_received=len(page.posts),
                duration_ms=page.request_meta.duration_ms,
                usage_headers=dict(page.request_meta.usage_headers or {}),
                cursor_before=page.cursor_before,
                cursor_after=page.cursor_after,
                status="ok",
                measured_at=measured_at,
            )

            if summary.account_id is None:
                raise MetaRequestError(f"Geen Instagram-ID voor @{summary.username}.")

            _touched, _created = _store_media_batch(
                conn,
                account_id=summary.account_id,
                posts=page.posts_in_period,
                fetch_run_id=fetch_run_id,
                measured_at=measured_at,
            )
            # Unique Instagram IDs in DB — never inflate by re-upserts/overlaps.
            posts_stored_total = _count_unique_instagram_posts(conn, summary.account_id)
            for post in page.posts_in_period:
                external_id = str(post.get("id") or "").strip()
                if external_id:
                    seen_ids.add(external_id)
                ts = parse_meta_timestamp(
                    post.get("timestamp") if isinstance(post.get("timestamp"), str) else None
                )
                if ts is None:
                    continue
                if oldest_f is None or ts < oldest_f:
                    oldest_f = ts
                if newest_f is None or ts > newest_f:
                    newest_f = ts

            last_cursor = page.cursor_after
            # Completeness uses session oldest; cutoff page also marks complete.
            complete = bool(
                page.reached_cutoff
                or page.stopped_reason in {"cutoff bereikt", "lege media-pagina"}
                or (
                    page.stopped_reason == "einde feed"
                    and (oldest_f is None or oldest_f <= period_start)
                )
            )
            if page.stopped_reason == "geen media-blok":
                complete = False
            stopped_reason = page.stopped_reason or stopped_reason

            save_discovery_checkpoint(
                conn,
                account_id=summary.account_id,
                username=summary.username,
                period_start=period_start_iso,
                period_end=period_end_iso,
                cursor_after=last_cursor,
                pages_fetched=pages_done,
                posts_fetched=posts_stored_total,
                is_complete=complete,
                last_fetch_run_id=fetch_run_id,
                account_external_id=str(account_payload.get("id") or "") or None,
                account_payload=account_payload or None,
            )
            # Persist page progress immediately so rate-limits keep prior pages.
            conn.commit()

            if complete or page.stopped_reason or not page.cursor_after:
                break

        if summary.account_id is None and account_payload.get("id"):
            summary.account_id = _ensure_competitor_account(
                conn,
                username=summary.username,
                account_payload=account_payload,
            )

        if summary.account_id is None:
            raise MetaRequestError(f"Geen Instagram-ID voor @{summary.username}.")

        posts_stored_total = _count_unique_instagram_posts(conn, summary.account_id)
        bounds = conn.execute(
            """
            SELECT MIN(published_at) AS mn, MAX(published_at) AS mx
            FROM posts
            WHERE account_id = ? AND platform = 'instagram'
            """,
            (summary.account_id,),
        ).fetchone()
        summary.posts_fetched = posts_stored_total
        summary.is_complete = complete
        summary.oldest_post_at = (
            str(bounds["mn"]) if bounds and bounds["mn"] else (to_utc_iso(oldest_f) if oldest_f else None)
        )
        summary.newest_post_at = (
            str(bounds["mx"]) if bounds and bounds["mx"] else (to_utc_iso(newest_f) if newest_f else None)
        )
        summary.followers_count = optional_int(account_payload.get("followers_count"))
        summary.media_count = optional_int(account_payload.get("media_count"))
        summary.status = "success" if complete else "partial"
        if not complete and stopped_reason == "max pagina's":
            summary.error_message = "Onvolledig: maximum aantal pagina's bereikt."

        upsert_account_result(
            conn,
            fetch_run_id=fetch_run_id,
            account_id=summary.account_id,
            status=summary.status,
            posts_fetched=posts_stored_total,
            period_start=summary.period_start,
            period_end=summary.period_end,
            is_complete=complete,
            oldest_post_at=summary.oldest_post_at,
            newest_post_at=summary.newest_post_at,
            error_message=summary.error_message,
        )
        if complete:
            clear_discovery_checkpoint(conn, summary.account_id)
        else:
            # Incomplete period stays checkpointed for resume; keep stored posts.
            save_discovery_checkpoint(
                conn,
                account_id=summary.account_id,
                username=summary.username,
                period_start=period_start_iso,
                period_end=period_end_iso,
                cursor_after=last_cursor,
                pages_fetched=pages_done,
                posts_fetched=posts_stored_total,
                is_complete=False,
                last_fetch_run_id=fetch_run_id,
                account_external_id=str(account_payload.get("id") or "") or None,
                account_payload=account_payload or None,
            )
    except MetaClientError as exc:
        duration_ms = getattr(exc, "duration_ms", None)
        if isinstance(exc, MetaRequestError) and exc.is_rate_limited:
            summary.rate_limited = True
            summary.error_message = RATE_LIMIT_USER_MESSAGE
            summary.retry_after_seconds = exc.retry_after_seconds
            summary.blocked_until = exc.blocked_until
            summary.usage_headers = dict(exc.usage_headers or {})
            summary.error_code = exc.error_code
            _record_rate_limit(conn, exc)
        else:
            summary.error_message = safe_error_text(exc)

        log_discovery_request(
            conn,
            fetch_run_id=fetch_run_id,
            account_id=summary.account_id,
            username=summary.username,
            page_index=pages_done + 1,
            page_size=media_limit,
            posts_received=0,
            duration_ms=float(duration_ms) if duration_ms is not None else None,
            usage_headers=dict(summary.usage_headers or {}),
            cursor_before=last_cursor,
            cursor_after=None,
            status="rate_limited" if summary.rate_limited else "error",
            measured_at=measured_at,
            error_message=summary.error_message,
        )

        # Keep already stored pages; mark period incomplete for later resume.
        if summary.account_id is not None and posts_stored_total > 0:
            summary.status = "partial"
            summary.posts_fetched = posts_stored_total
            summary.is_complete = False
            summary.oldest_post_at = to_utc_iso(oldest_f) if oldest_f else None
            summary.newest_post_at = to_utc_iso(newest_f) if newest_f else None
            save_discovery_checkpoint(
                conn,
                account_id=summary.account_id,
                username=summary.username,
                period_start=period_start_iso,
                period_end=period_end_iso,
                cursor_after=last_cursor,
                pages_fetched=pages_done,
                posts_fetched=posts_stored_total,
                is_complete=False,
                last_fetch_run_id=fetch_run_id,
                account_payload=account_payload or known_account or None,
            )
            upsert_account_result(
                conn,
                fetch_run_id=fetch_run_id,
                account_id=summary.account_id,
                status="partial",
                posts_fetched=posts_stored_total,
                period_start=summary.period_start,
                period_end=summary.period_end,
                is_complete=False,
                oldest_post_at=summary.oldest_post_at,
                newest_post_at=summary.newest_post_at,
                error_message=summary.error_message,
            )
        elif summary.account_id is not None:
            summary.status = "failed"
            conn.execute(
                """
                UPDATE accounts
                SET last_access_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (summary.error_message, utc_now_iso(), summary.account_id),
            )
            upsert_account_result(
                conn,
                fetch_run_id=fetch_run_id,
                account_id=summary.account_id,
                status="failed",
                period_start=summary.period_start,
                period_end=summary.period_end,
                error_message=summary.error_message,
            )
        else:
            summary.status = "failed"
        logger.warning("Competitor @%s failed: %s", summary.username, summary.error_message)
    return summary


# Presentation / growth priority: missing same-day measurements first.
_COMPETITOR_FETCH_PRIORITY = (
    "excellent.magazine",
    "vtwonen",
    "bestinteriors_official",
    "hoog.design",
)


def _order_competitors(usernames: list[str]) -> list[str]:
    """Order competitors: missing-day priority first, then remaining."""
    cleaned = [normalize_username(u) for u in usernames if normalize_username(u)]
    seen: set[str] = set()
    unique: list[str] = []
    for name in cleaned:
        if name in seen:
            continue
        seen.add(name)
        unique.append(name)
    ordered: list[str] = []
    for preferred in _COMPETITOR_FETCH_PRIORITY:
        if preferred in seen:
            ordered.append(preferred)
    for name in unique:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _amsterdam_day_key(measured_at: str | None) -> str | None:
    if not measured_at:
        return None
    try:
        dt = datetime.fromisoformat(str(measured_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(AMSTERDAM).date().isoformat()
    except ValueError:
        return None


def _has_follower_measurement_on_day(
    conn,
    account_id: int | None,
    *,
    day: date | None = None,
) -> bool:
    """True when account_measurements already has a row on the Amsterdam day."""
    if account_id is None:
        return False
    target = (day or amsterdam_today()).isoformat()
    rows = conn.execute(
        """
        SELECT measured_at FROM account_measurements
        WHERE account_id = ?
        ORDER BY measured_at DESC, id DESC
        LIMIT 20
        """,
        (account_id,),
    ).fetchall()
    return any(_amsterdam_day_key(row["measured_at"]) == target for row in rows)


def _record_rate_limit(conn, exc: MetaRequestError) -> None:
    set_discovery_rate_limit(
        conn,
        error_code=exc.error_code,
        message=RATE_LIMIT_USER_MESSAGE,
        retry_after_seconds=exc.retry_after_seconds,
        blocked_until=exc.blocked_until,
        usage=exc.usage_headers,
    )


def run_comparison_sync(
    *,
    trigger_source: str = "manual",
    progress: ProgressCallback | None = None,
    force: bool = False,
    only_usernames: list[str] | None = None,
    include_own: bool = True,
    mode: str = "full",
) -> ComparisonSyncResult:
    """Refresh own account + persisted competitors for the last 3 calendar months.

    API calls happen only in this explicit run. Screen rendering must use SQLite.
    ``mode='growth'`` stores follower/profile snapshots only.
    ``mode='full'`` also refreshes post/engagement data.
    """
    initialize_database()
    result = ComparisonSyncResult()
    mode_normalized = (mode or "full").strip().lower()
    if mode_normalized not in {"growth", "full"}:
        mode_normalized = "full"
    period_start, period_end = three_calendar_months_window()
    measured_at = utc_now_iso()
    result.measured_at = measured_at
    result.period_start = to_utc_iso(period_start)
    result.period_end = to_utc_iso(period_end)

    with get_connection() as conn:
        ensure_default_comparison_accounts(conn)
        result.last_success_measured_at = get_latest_success_measured_at(conn)

        rate_state = discovery_requests_blocked(conn)
        if rate_state is not None:
            # Only a Meta-provided Retry-After window blocks requests.
            # --force must not bypass that active wait.
            result.status = "skipped"
            result.rate_limited = True
            result.rate_limit_blocked_until = rate_state.blocked_until
            result.last_success_measured_at = (
                rate_state.last_success_measured_at or result.last_success_measured_at
            )
            result.error_message = RATE_LIMIT_USER_MESSAGE
            result.skipped_reason = (
                f"{RATE_LIMIT_USER_MESSAGE}. Wacht tot {rate_state.blocked_until} (UTC)."
            )
            conn.commit()
            return result

        active = find_active_fetch_run(conn)
        if active is not None and not force:
            result.status = "skipped"
            result.skipped_reason = f"Er loopt al een ophaalrun (id={active})."
            result.run_id = active
            conn.commit()
            return result

        host = get_discovery_host(conn)
        if host is None:
            result.status = "failed"
            result.error_message = "Geen discovery-hostaccount geconfigureerd."
            conn.commit()
            return result

        own_id, competitors = get_preferences(conn)
        if own_id is None:
            own_id = host.account_id
        own_account = get_account_by_id(conn, own_id) or host

        run_id = start_fetch_run(
            conn,
            trigger_source=trigger_source,
            measured_at=measured_at,
            period_start=result.period_start,
            period_end=result.period_end,
        )
        result.run_id = run_id
        conn.commit()

    try:
        settings = get_settings()
        if not settings.meta_page_access_token:
            raise MetaRequestError("META_PAGE_ACCESS_TOKEN ontbreekt.")
        client = MetaClient.from_settings()
    except MetaClientError as exc:
        with get_connection() as conn:
            finish_fetch_run(
                conn,
                run_id,
                status="failed",
                error_message=safe_error_text(exc),
            )
            conn.commit()
        result.status = "failed"
        result.error_message = safe_error_text(exc)
        return result

    try:
        return _execute_comparison_sync(
            client=client,
            result=result,
            run_id=run_id,
            measured_at=measured_at,
            period_start=period_start,
            period_end=period_end,
            own_account=own_account,
            competitors=competitors,
            only_usernames=only_usernames,
            include_own=include_own,
            progress=progress,
            mode=mode_normalized,
        )
    except Exception as exc:  # noqa: BLE001 — never leave status=running
        logger.exception("Vergelijkingssync afgebroken (run_id=%s)", run_id)
        message = safe_error_text(exc) if isinstance(exc, MetaClientError) else str(exc)
        with get_connection() as conn:
            finish_fetch_run(
                conn,
                run_id,
                status="failed",
                error_message=message or "Onbekende fout tijdens vernieuwen.",
            )
            conn.commit()
        result.status = "failed"
        result.error_message = message or "Onbekende fout tijdens vernieuwen."
        _emit(progress, f"Vernieuwen mislukt: {result.error_message}", 1.0)
        return result


def _execute_comparison_sync(
    *,
    client: MetaClient,
    result: ComparisonSyncResult,
    run_id: int,
    measured_at: str,
    period_start: datetime,
    period_end: datetime,
    own_account,
    competitors: list[str],
    only_usernames: list[str] | None,
    include_own: bool,
    progress: ProgressCallback | None,
    mode: str = "full",
) -> ComparisonSyncResult:
    """Inner sync body (profile-first, then media). Caller owns run lifecycle."""
    if only_usernames:
        filter_set = {normalize_username(u) for u in only_usernames}
        competitor_targets = [
            u for u in competitors if normalize_username(u) in filter_set
        ]
        include_own_flag = include_own and (
            normalize_username(own_account.username or DEFAULT_FALLBACK_USERNAME)
            in filter_set
            or any(u in filter_set for u in ("own", "eigen"))
        )
    else:
        competitor_targets = list(competitors)
        include_own_flag = include_own

    # Prefer accounts still missing today's follower snapshot. Skip TAOL/HOOG
    # when they already have a real Amsterdam-day measurement (presentation priority).
    skip_usernames: set[str] = set()
    with get_connection() as conn:
        if include_own_flag and own_account is not None:
            already_in_run = account_result_exists(
                conn,
                fetch_run_id=run_id,
                account_id=own_account.account_id,
                status="success",
            )
            already_today = _has_follower_measurement_on_day(
                conn, own_account.account_id
            )
            # Full runs still need own post/engagement data even if growth
            # already stored today's follower snapshot.
            if already_in_run or (mode == "growth" and already_today):
                include_own_flag = False
                _emit(
                    progress,
                    "Eigen account overgeslagen (vandaag al gemeten).",
                    0.0,
                )
        for username in list(competitor_targets):
            key = normalize_username(username)
            if key != "hoog.design":
                continue
            account = get_account_by_username(conn, key)
            if account is not None and _has_follower_measurement_on_day(
                conn, account.account_id
            ):
                skip_usernames.add(key)
                _emit(
                    progress,
                    f"@{key} overgeslagen (vandaag al gemeten).",
                    0.0,
                )
        conn.commit()

    competitor_targets = _order_competitors(
        [u for u in competitor_targets if normalize_username(u) not in skip_usernames]
    )

    targets: list[tuple[str, str]] = []
    if include_own_flag:
        targets.append(
            (
                "own",
                normalize_username(own_account.username or DEFAULT_FALLBACK_USERNAME),
            )
        )
    for username in competitor_targets:
        targets.append(("competitor", username))

    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for role, username in targets:
        key = username.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append((role, username))

    total = max(len(ordered), 1)
    summaries: list[AccountFetchSummary] = []
    profile_summaries: list[AccountFetchSummary] = []
    discovery_blocked = False

    # Phase 1: profile-only follower snapshots for competitors still missing today.
    # Saves each success immediately so a later #4 during media cannot wipe them.
    profile_targets = [u for role, u in ordered if role == "competitor"]
    for p_index, username in enumerate(profile_targets):
        with get_connection() as conn:
            account = get_account_by_username(conn, username)
            if account is not None and _has_follower_measurement_on_day(
                conn, account.account_id
            ):
                continue
            host = get_discovery_host(conn)
            if host is None:
                discovery_blocked = True
                result.status = "failed"
                result.error_message = "Discovery-host ontbreekt."
                conn.commit()
                break
            _emit(
                progress,
                f"Profielmeting @{username}…",
                min(0.15, (p_index / max(len(profile_targets), 1)) * 0.15),
            )
            if p_index > 0:
                time_module.sleep(2)
            profile_summary = _fetch_competitor_profile(
                client,
                conn,
                host=host,
                username=username,
                fetch_run_id=run_id,
                measured_at=measured_at,
            )
            profile_summaries.append(profile_summary)
            if profile_summary.rate_limited:
                discovery_blocked = True
                result.rate_limited = True
                result.rate_limit_blocked_until = profile_summary.blocked_until
                result.error_message = RATE_LIMIT_USER_MESSAGE
                result.last_success_measured_at = (
                    get_latest_success_measured_at(conn)
                    or result.last_success_measured_at
                )
                state = get_discovery_rate_limit(conn)
                if state is not None:
                    result.rate_limit_blocked_until = (
                        state.blocked_until or result.rate_limit_blocked_until
                    )
                _emit(progress, RATE_LIMIT_USER_MESSAGE, 0.15)
                conn.commit()
                break
            if profile_summary.status == "failed":
                # Non-rate failures: continue other profiles; media phase may retry.
                logger.warning(
                    "Profielmeting @%s mislukt: %s",
                    username,
                    profile_summary.error_message,
                )
            conn.commit()

    if mode == "growth":
        if include_own_flag and not discovery_blocked:
            _emit(progress, "Profielmeting eigen account…", 0.2)
            with get_connection() as conn:
                host = get_discovery_host(conn)
                if host is None:
                    result.status = "failed"
                    result.error_message = "Discovery-host ontbreekt."
                    conn.commit()
                else:
                    own_summary = _fetch_own_account(
                        client,
                        conn,
                        host=host,
                        fetch_run_id=run_id,
                        measured_at=measured_at,
                        period_start=period_start,
                        period_end=period_end,
                        include_media=False,
                    )
                    summaries.append(own_summary)
                    if own_summary.rate_limited:
                        discovery_blocked = True
                        result.rate_limited = True
                        result.rate_limit_blocked_until = own_summary.blocked_until
                        result.error_message = RATE_LIMIT_USER_MESSAGE
                    conn.commit()
        summaries.extend(profile_summaries)
        # Skip media/engagement; follower snapshots are the morning job.
    if mode != "growth":
        # Phase 2: media / engagement, one account at a time (priority order).
        for index, (role, username) in enumerate(ordered):
            fraction = 0.15 + (index / total) * 0.85
            if discovery_blocked and role == "competitor":
                summary = AccountFetchSummary(
                    username=username,
                    status="skipped",
                    error_message=RATE_LIMIT_USER_MESSAGE,
                )
                summaries.append(summary)
                continue

            if index > 0 and not discovery_blocked:
                # Mild pacing only between successful schedule slots; no retry loops.
                time_module.sleep(3)

            _emit(progress, f"Ophalen posts @{username}…", fraction)
            with get_connection() as conn:
                host = get_discovery_host(conn)
                if host is None:
                    summary = AccountFetchSummary(
                        username=username,
                        status="failed",
                        error_message="Discovery-host ontbreekt.",
                    )
                elif role == "own":
                    if account_result_exists(
                        conn,
                        fetch_run_id=run_id,
                        account_id=host.account_id
                        if own_account is None
                        else own_account.account_id,
                        status="success",
                    ):
                        summary = AccountFetchSummary(
                            username=username,
                            account_id=own_account.account_id if own_account else None,
                            status="skipped",
                            error_message="Eigen account al geslaagd in deze run.",
                        )
                    else:
                        summary = _fetch_own_account(
                            client,
                            conn,
                            host=host,
                            fetch_run_id=run_id,
                            measured_at=measured_at,
                            period_start=period_start,
                            period_end=period_end,
                        )
                else:
                    summary = _fetch_competitor(
                        client,
                        conn,
                        host=host,
                        username=username,
                        fetch_run_id=run_id,
                        measured_at=measured_at,
                        period_start=period_start,
                        period_end=period_end,
                    )

                if summary.rate_limited or (
                    summary.status == "failed"
                    and summary.error_message
                    and (
                        summary.error_message == RATE_LIMIT_USER_MESSAGE
                        or is_rate_limit_error(message=summary.error_message)
                    )
                ):
                    discovery_blocked = True
                    result.rate_limited = True
                    result.rate_limit_blocked_until = summary.blocked_until
                    result.error_message = RATE_LIMIT_USER_MESSAGE
                    result.last_success_measured_at = (
                        get_latest_success_measured_at(conn)
                        or result.last_success_measured_at
                    )
                    summary.error_message = RATE_LIMIT_USER_MESSAGE
                    state = get_discovery_rate_limit(conn)
                    if state is not None:
                        result.rate_limit_blocked_until = (
                            state.blocked_until or result.rate_limit_blocked_until
                        )
                    _emit(progress, RATE_LIMIT_USER_MESSAGE, fraction)
                elif (
                    summary.status == "success"
                    and summary.is_complete
                    and role == "competitor"
                ):
                    clear_discovery_rate_limit(conn)

                conn.commit()
            summaries.append(summary)

            if discovery_blocked:
                break

    # Mark remaining competitors as skipped without API calls.
    if discovery_blocked:
        done = {item.username for item in summaries}
        for role, username in ordered:
            if role != "competitor" or username in done:
                continue
            summaries.append(
                AccountFetchSummary(
                    username=username,
                    status="skipped",
                    error_message=RATE_LIMIT_USER_MESSAGE,
                )
            )

    failures = [item for item in summaries if item.status == "failed"]
    partials = [item for item in summaries if item.status == "partial"]
    skipped = [item for item in summaries if item.status == "skipped"]
    if not summaries:
        # Growth with every profile already measured today is a successful no-op.
        status = "completed" if mode == "growth" else "failed"
    elif result.rate_limited and (failures or partials):
        status = "completed_with_errors"
    elif failures and len(failures) == len(summaries):
        status = "failed"
    elif failures or partials or (result.rate_limited and skipped):
        status = "completed_with_errors"
    else:
        status = "completed"

    with get_connection() as conn:
        if result.rate_limited:
            state = get_discovery_rate_limit(conn)
            if state is not None:
                result.rate_limit_blocked_until = state.blocked_until
                result.last_success_measured_at = (
                    state.last_success_measured_at or result.last_success_measured_at
                )
        finish_fetch_run(
            conn,
            run_id,
            status=status,
            error_message=(
                RATE_LIMIT_USER_MESSAGE
                if result.rate_limited
                else (None if not failures else f"{len(failures)} account(s) mislukt")
            ),
        )
        conn.commit()

    result.status = status
    result.accounts = summaries
    if result.rate_limited:
        _emit(progress, RATE_LIMIT_USER_MESSAGE, 1.0)
    else:
        _emit(progress, "Vergelijkingsgegevens bijgewerkt.", 1.0)
    return result


def resume_account_fetch(
    username: str,
    *,
    trigger_source: str | None = None,
    progress: ProgressCallback | None = None,
) -> ComparisonSyncResult:
    """Controlled single-account resume after a rate limit or partial fetch.

    One Business Discovery pull for the given competitor username. No
    own-account sync, no automatic retries, no concurrent runs. Does nothing
    unless called explicitly (UI button / CLI).
    """
    cleaned = normalize_username(username)
    if not cleaned:
        result = ComparisonSyncResult(status="failed")
        result.error_message = "Geen accountnaam om te hervatten."
        return result
    source = trigger_source or f"manual_resume_{cleaned}"
    return run_comparison_sync(
        trigger_source=source,
        progress=progress,
        force=False,
        only_usernames=[cleaned],
        include_own=False,
    )


def resume_hoog_design_fetch(
    *,
    trigger_source: str = "manual_resume_hoog",
    progress: ProgressCallback | None = None,
) -> ComparisonSyncResult:
    """Backward-compatible alias for resume_account_fetch('hoog.design')."""
    return resume_account_fetch(
        "hoog.design",
        trigger_source=trigger_source,
        progress=progress,
    )
