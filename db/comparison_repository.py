"""Persistence helpers for social comparison accounts, posts and measurements."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from db.repository import UpsertResult, merge_post_raw_json

DEFAULT_OWN_USERNAME = "theartofliving.online"
DEFAULT_OWN_EXTERNAL_ID = "17841400191455972"
DEFAULT_OWN_NAME = "The Art of Living Online"
DEFAULT_COMPETITORS = (
    "hoog.design",
    "bestinteriors_official",
    "excellent.magazine",
    "vtwonen",
)
INACCESSIBLE_EXTERNAL_IDS = ("17841433048165203",)

ROLE_OWN = "own"
ROLE_COMPETITOR = "competitor"


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def normalize_username(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if cleaned.startswith("@"):
        cleaned = cleaned[1:]
    return cleaned.casefold()


@dataclass(frozen=True, slots=True)
class ComparisonAccount:
    account_id: int
    platform: str
    external_id: str
    name: str | None
    username: str | None
    comparison_role: str | None
    is_discovery_host: bool
    access_status: str


def mark_inaccessible_accounts(conn: sqlite3.Connection) -> int:
    """Flag known-bad IG IDs without deleting posts or history."""
    updated = 0
    for external_id in INACCESSIBLE_EXTERNAL_IDS:
        cursor = conn.execute(
            """
            UPDATE accounts
            SET access_status = 'inaccessible',
                last_access_error = COALESCE(
                    last_access_error,
                    'Object unavailable or missing permissions'
                ),
                updated_at = ?
            WHERE platform = 'instagram'
              AND external_id = ?
              AND COALESCE(access_status, 'ok') != 'inaccessible'
            """,
            (utc_now_iso(), external_id),
        )
        updated += cursor.rowcount
    return updated


def upsert_comparison_account(
    conn: sqlite3.Connection,
    *,
    external_id: str,
    username: str | None = None,
    name: str | None = None,
    comparison_role: str | None = None,
    is_discovery_host: bool = False,
    access_status: str = "ok",
    last_access_error: str | None = None,
    page_id: str | None = None,
) -> int:
    """Insert/update an Instagram account used for comparison."""
    username_norm = normalize_username(username) if username else None
    existing = conn.execute(
        "SELECT id FROM accounts WHERE platform = ? AND external_id = ?",
        ("instagram", external_id),
    ).fetchone()
    now = utc_now_iso()
    if existing:
        conn.execute(
            """
            UPDATE accounts
            SET name = COALESCE(?, name),
                username = COALESCE(?, username),
                page_id = COALESCE(?, page_id),
                comparison_role = COALESCE(?, comparison_role),
                is_discovery_host = CASE
                    WHEN ? = 1 THEN 1
                    ELSE COALESCE(is_discovery_host, 0)
                END,
                access_status = ?,
                last_access_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                username_norm,
                page_id,
                comparison_role,
                1 if is_discovery_host else 0,
                access_status,
                last_access_error,
                now,
                int(existing["id"]),
            ),
        )
        return int(existing["id"])

    # Prefer matching by username when external_id is new but username exists.
    if username_norm:
        by_username = conn.execute(
            """
            SELECT id, external_id FROM accounts
            WHERE platform = 'instagram'
              AND lower(username) = lower(?)
            """,
            (username_norm,),
        ).fetchone()
        if by_username and by_username["external_id"] != external_id:
            # Keep the existing row (old inaccessible id) and insert the new one
            # only when external IDs differ — do not delete the old row.
            pass

    cursor = conn.execute(
        """
        INSERT INTO accounts (
            platform, external_id, name, username, page_id,
            comparison_role, is_discovery_host, access_status,
            last_access_error, updated_at
        )
        VALUES ('instagram', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            external_id,
            name,
            username_norm,
            page_id,
            comparison_role,
            1 if is_discovery_host else 0,
            access_status,
            last_access_error,
            now,
        ),
    )
    return int(cursor.lastrowid)


def ensure_competitor_placeholders(
    conn: sqlite3.Connection,
    usernames: Iterable[str],
) -> None:
    """Ensure competitor usernames exist as rows before their IG id is known."""
    for username in usernames:
        cleaned = normalize_username(username)
        if not cleaned:
            continue
        existing = get_account_by_username(conn, cleaned)
        if existing is not None:
            if existing.comparison_role != ROLE_COMPETITOR and not existing.is_discovery_host:
                conn.execute(
                    """
                    UPDATE accounts
                    SET comparison_role = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (ROLE_COMPETITOR, utc_now_iso(), existing.account_id),
                )
            continue
        upsert_comparison_account(
            conn,
            external_id=f"pending:{cleaned}",
            username=cleaned,
            name=None,
            comparison_role=ROLE_COMPETITOR,
            is_discovery_host=False,
            access_status="pending",
        )


def ensure_default_comparison_accounts(conn: sqlite3.Connection) -> ComparisonAccount:
    """Seed discovery host / own account and default competitor usernames."""
    mark_inaccessible_accounts(conn)
    own_id = upsert_comparison_account(
        conn,
        external_id=DEFAULT_OWN_EXTERNAL_ID,
        username=DEFAULT_OWN_USERNAME,
        name=DEFAULT_OWN_NAME,
        comparison_role=ROLE_OWN,
        is_discovery_host=True,
        access_status="ok",
    )
    # Clear own/competitor role from other accessible IG accounts that were
    # incorrectly treated as own, without deleting them.
    conn.execute(
        """
        UPDATE accounts
        SET comparison_role = NULL,
            updated_at = ?
        WHERE platform = 'instagram'
          AND id != ?
          AND comparison_role = ?
          AND COALESCE(is_discovery_host, 0) = 0
        """,
        (utc_now_iso(), own_id, ROLE_OWN),
    )
    ensure_preferences(conn, own_account_id=own_id, competitors=DEFAULT_COMPETITORS)
    _, competitors = get_preferences(conn)
    ensure_competitor_placeholders(conn, competitors or DEFAULT_COMPETITORS)
    row = conn.execute(
        """
        SELECT id, platform, external_id, name, username,
               comparison_role, is_discovery_host, access_status
        FROM accounts WHERE id = ?
        """,
        (own_id,),
    ).fetchone()
    return _row_to_account(row)


def ensure_preferences(
    conn: sqlite3.Connection,
    *,
    own_account_id: int | None = None,
    competitors: Iterable[str] | None = None,
) -> None:
    existing = conn.execute(
        "SELECT own_account_id, competitor_usernames_json FROM comparison_preferences WHERE id = 1"
    ).fetchone()
    if existing is None:
        usernames = [normalize_username(u) for u in (competitors or DEFAULT_COMPETITORS)]
        conn.execute(
            """
            INSERT INTO comparison_preferences (id, own_account_id, competitor_usernames_json, updated_at)
            VALUES (1, ?, ?, ?)
            """,
            (own_account_id, json.dumps(usernames), utc_now_iso()),
        )
        return

    current = list(json.loads(existing["competitor_usernames_json"] or "[]"))
    if competitors is not None and not current:
        current = [normalize_username(u) for u in competitors]
    own_id = own_account_id if own_account_id is not None else existing["own_account_id"]
    conn.execute(
        """
        UPDATE comparison_preferences
        SET own_account_id = ?,
            competitor_usernames_json = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (own_id, json.dumps(current), utc_now_iso()),
    )


def get_preferences(conn: sqlite3.Connection) -> tuple[int | None, list[str]]:
    row = conn.execute(
        "SELECT own_account_id, competitor_usernames_json FROM comparison_preferences WHERE id = 1"
    ).fetchone()
    if not row:
        return None, []
    try:
        competitors = json.loads(row["competitor_usernames_json"] or "[]")
    except json.JSONDecodeError:
        competitors = []
    if not isinstance(competitors, list):
        competitors = []
    return (
        int(row["own_account_id"]) if row["own_account_id"] is not None else None,
        [normalize_username(str(item)) for item in competitors if str(item).strip()],
    )


def save_preferences(
    conn: sqlite3.Connection,
    *,
    own_account_id: int | None,
    competitors: list[str],
    max_competitors: int | None = 10,
) -> None:
    """Persist competitor usernames.

    ``max_competitors`` caps newly saved lists. Lists that already exceed the
    cap are kept intact so oversized legacy selections are not auto-trimmed.
    """
    cleaned = []
    seen: set[str] = set()
    for item in competitors:
        name = normalize_username(item)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
    if max_competitors is not None and len(cleaned) > max_competitors:
        # Only truncate when growing past the cap from a short previous list.
        previous = get_preferences(conn)[1]
        if len(previous) <= max_competitors:
            cleaned = cleaned[:max_competitors]
        # else: preserve oversized legacy list as-is
    conn.execute(
        """
        INSERT INTO comparison_preferences (id, own_account_id, competitor_usernames_json, updated_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            own_account_id = excluded.own_account_id,
            competitor_usernames_json = excluded.competitor_usernames_json,
            updated_at = excluded.updated_at
        """,
        (own_account_id, json.dumps(cleaned), utc_now_iso()),
    )
    ensure_competitor_placeholders(conn, cleaned)


def list_comparison_accounts(
    conn: sqlite3.Connection,
    *,
    roles: Iterable[str] | None = None,
) -> list[ComparisonAccount]:
    params: list[Any] = []
    sql = """
        SELECT id, platform, external_id, name, username,
               comparison_role, is_discovery_host, access_status
        FROM accounts
        WHERE platform = 'instagram'
    """
    role_list = list(roles or [])
    if role_list:
        placeholders = ",".join("?" for _ in role_list)
        sql += f" AND comparison_role IN ({placeholders})"
        params.extend(role_list)
    sql += " ORDER BY COALESCE(username, name, external_id)"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_account(row) for row in rows]


def get_discovery_host(conn: sqlite3.Connection) -> ComparisonAccount | None:
    row = conn.execute(
        """
        SELECT id, platform, external_id, name, username,
               comparison_role, is_discovery_host, access_status
        FROM accounts
        WHERE platform = 'instagram' AND COALESCE(is_discovery_host, 0) = 1
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    return _row_to_account(row) if row else None


def get_account_by_id(conn: sqlite3.Connection, account_id: int) -> ComparisonAccount | None:
    row = conn.execute(
        """
        SELECT id, platform, external_id, name, username,
               comparison_role, is_discovery_host, access_status
        FROM accounts WHERE id = ?
        """,
        (account_id,),
    ).fetchone()
    return _row_to_account(row) if row else None


def get_account_by_username(
    conn: sqlite3.Connection,
    username: str,
) -> ComparisonAccount | None:
    row = conn.execute(
        """
        SELECT id, platform, external_id, name, username,
               comparison_role, is_discovery_host, access_status
        FROM accounts
        WHERE platform = 'instagram' AND lower(username) = lower(?)
        ORDER BY CASE WHEN access_status = 'ok' THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        (normalize_username(username),),
    ).fetchone()
    return _row_to_account(row) if row else None


def upsert_comparison_post(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    external_id: str,
    text: str | None,
    permalink: str | None,
    media_type: str | None,
    media_product_type: str | None,
    published_at: str,
    raw_json: str | None,
) -> UpsertResult:
    content_type = classify_content_type(media_type, media_product_type)
    existing = conn.execute(
        "SELECT id, raw_json FROM posts WHERE platform = ? AND external_id = ?",
        ("instagram", external_id),
    ).fetchone()
    incoming_media_url: str | None = None
    incoming_thumbnail_url: str | None = None
    if raw_json:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            media_val = payload.get("media_url")
            thumb_val = payload.get("thumbnail_url")
            if isinstance(media_val, str) and media_val.strip():
                incoming_media_url = media_val.strip()
            if isinstance(thumb_val, str) and thumb_val.strip():
                incoming_thumbnail_url = thumb_val.strip()

    if existing:
        merged = merge_post_raw_json(existing["raw_json"], raw_json, clear_unavailable=True)
        # Preserve existing caption/text and media URLs when a lean payload omits them.
        conn.execute(
            """
            UPDATE posts
            SET account_id = ?,
                content_type = ?,
                text = COALESCE(?, text),
                permalink = COALESCE(?, permalink),
                media_url = COALESCE(?, media_url),
                thumbnail_url = COALESCE(?, thumbnail_url),
                media_type = COALESCE(?, media_type),
                media_product_type = COALESCE(?, media_product_type),
                published_at = ?,
                raw_json = ?,
                last_synced_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                account_id,
                content_type,
                text,
                permalink,
                incoming_media_url,
                incoming_thumbnail_url,
                media_type,
                media_product_type,
                published_at,
                merged,
                int(existing["id"]),
            ),
        )
        return UpsertResult(row_id=int(existing["id"]), created=False)

    cursor = conn.execute(
        """
        INSERT INTO posts (
            platform, external_id, account_id, content_type, text, permalink,
            media_url, thumbnail_url, media_type, media_product_type, published_at, raw_json
        )
        VALUES ('instagram', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            external_id,
            account_id,
            content_type,
            text,
            permalink,
            incoming_media_url,
            incoming_thumbnail_url,
            media_type,
            media_product_type,
            published_at,
            raw_json,
        ),
    )
    return UpsertResult(row_id=int(cursor.lastrowid), created=True)


def classify_content_type(media_type: str | None, media_product_type: str | None) -> str:
    product = (media_product_type or "").upper()
    media = (media_type or "").upper()
    if product == "REELS":
        return "reel"
    if media == "CAROUSEL_ALBUM":
        return "carousel"
    if media == "IMAGE":
        return "image"
    if media == "VIDEO":
        return "video"
    if media or product:
        return "unknown"
    return "unknown"


def start_fetch_run(
    conn: sqlite3.Connection,
    *,
    trigger_source: str,
    measured_at: str,
    period_start: str,
    period_end: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO comparison_fetch_runs (
            started_at, status, trigger_source, measured_at, period_start, period_end
        )
        VALUES (?, 'running', ?, ?, ?, ?)
        """,
        (utc_now_iso(), trigger_source, measured_at, period_start, period_end),
    )
    return int(cursor.lastrowid)


def finish_fetch_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE comparison_fetch_runs
        SET finished_at = ?, status = ?, error_message = ?
        WHERE id = ?
        """,
        (utc_now_iso(), status, error_message, run_id),
    )


def find_active_fetch_run(conn: sqlite3.Connection, *, max_age_seconds: int = 3600) -> int | None:
    row = conn.execute(
        """
        SELECT id, started_at FROM comparison_fetch_runs
        WHERE status = 'running'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    try:
        started = datetime.fromisoformat(str(row["started_at"]))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (datetime.now(tz=timezone.utc) - started).total_seconds()
        if age > max_age_seconds:
            finish_fetch_run(
                conn,
                int(row["id"]),
                status="failed",
                error_message="Run verlopen (timeout).",
            )
            return None
    except ValueError:
        pass
    return int(row["id"])


def upsert_account_result(
    conn: sqlite3.Connection,
    *,
    fetch_run_id: int,
    account_id: int,
    status: str,
    posts_fetched: int = 0,
    period_start: str | None = None,
    period_end: str | None = None,
    is_complete: bool = False,
    oldest_post_at: str | None = None,
    newest_post_at: str | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO comparison_fetch_account_results (
            fetch_run_id, account_id, status, posts_fetched, period_start, period_end,
            is_complete, oldest_post_at, newest_post_at, error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fetch_run_id, account_id) DO UPDATE SET
            status = excluded.status,
            posts_fetched = excluded.posts_fetched,
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            is_complete = excluded.is_complete,
            oldest_post_at = excluded.oldest_post_at,
            newest_post_at = excluded.newest_post_at,
            error_message = excluded.error_message
        """,
        (
            fetch_run_id,
            account_id,
            status,
            posts_fetched,
            period_start,
            period_end,
            1 if is_complete else 0,
            oldest_post_at,
            newest_post_at,
            error_message,
        ),
    )


def upsert_account_measurement(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    fetch_run_id: int,
    measured_at: str,
    followers_count: int | None,
    media_count: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO account_measurements (
            account_id, fetch_run_id, measured_at, followers_count, media_count
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(fetch_run_id, account_id) DO UPDATE SET
            measured_at = excluded.measured_at,
            followers_count = excluded.followers_count,
            media_count = excluded.media_count
        """,
        (account_id, fetch_run_id, measured_at, followers_count, media_count),
    )


def upsert_post_measurement(
    conn: sqlite3.Connection,
    *,
    post_id: int,
    fetch_run_id: int,
    measured_at: str,
    like_count: int | None,
    comments_count: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO post_measurements (
            post_id, fetch_run_id, measured_at, like_count, comments_count
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(fetch_run_id, post_id) DO UPDATE SET
            measured_at = excluded.measured_at,
            like_count = excluded.like_count,
            comments_count = excluded.comments_count
        """,
        (post_id, fetch_run_id, measured_at, like_count, comments_count),
    )


def get_latest_completed_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM comparison_fetch_runs
        WHERE status IN ('completed', 'completed_with_errors')
        ORDER BY measured_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()


def get_latest_success_measured_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT measured_at FROM account_measurements
        ORDER BY measured_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["measured_at"]) if row else None


DISCOVERY_RATE_LIMIT_ROUTE = "business_discovery"


@dataclass(frozen=True, slots=True)
class RateLimitState:
    route: str
    hit_at: str
    error_code: int | None
    message: str | None
    retry_after_seconds: int | None
    blocked_until: str | None
    usage_json: str | None
    last_success_measured_at: str | None
    active: bool
    reset_moment_known: bool


def set_discovery_rate_limit(
    conn: sqlite3.Connection,
    *,
    error_code: int | None,
    message: str,
    retry_after_seconds: int | None,
    blocked_until: str | None,
    usage: dict[str, Any] | None = None,
) -> None:
    last_success = get_latest_success_measured_at(conn)
    conn.execute(
        """
        INSERT INTO comparison_rate_limits (
            route, hit_at, error_code, message, retry_after_seconds,
            blocked_until, usage_json, last_success_measured_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(route) DO UPDATE SET
            hit_at = excluded.hit_at,
            error_code = excluded.error_code,
            message = excluded.message,
            retry_after_seconds = excluded.retry_after_seconds,
            blocked_until = excluded.blocked_until,
            usage_json = excluded.usage_json,
            last_success_measured_at = COALESCE(
                excluded.last_success_measured_at,
                comparison_rate_limits.last_success_measured_at
            ),
            updated_at = excluded.updated_at
        """,
        (
            DISCOVERY_RATE_LIMIT_ROUTE,
            utc_now_iso(),
            error_code,
            message,
            retry_after_seconds,
            blocked_until,
            json.dumps(usage or {}, ensure_ascii=False),
            last_success,
            utc_now_iso(),
        ),
    )


def clear_discovery_rate_limit(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM comparison_rate_limits WHERE route = ?",
        (DISCOVERY_RATE_LIMIT_ROUTE,),
    )


def get_discovery_rate_limit(conn: sqlite3.Connection) -> RateLimitState | None:
    row = conn.execute(
        """
        SELECT route, hit_at, error_code, message, retry_after_seconds,
               blocked_until, usage_json, last_success_measured_at
        FROM comparison_rate_limits
        WHERE route = ?
        """,
        (DISCOVERY_RATE_LIMIT_ROUTE,),
    ).fetchone()
    if not row:
        return None
    blocked_until = row["blocked_until"]
    reset_moment_known = bool(blocked_until)
    # Without a Meta-provided end time the status is informational only:
    # it must never permanently block future explicit requests.
    active = False
    if blocked_until:
        try:
            until = datetime.fromisoformat(str(blocked_until))
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            active = datetime.now(tz=timezone.utc) < until
        except ValueError:
            active = False
            reset_moment_known = False
    return RateLimitState(
        route=str(row["route"]),
        hit_at=str(row["hit_at"]),
        error_code=int(row["error_code"]) if row["error_code"] is not None else None,
        message=row["message"],
        retry_after_seconds=int(row["retry_after_seconds"])
        if row["retry_after_seconds"] is not None
        else None,
        blocked_until=str(blocked_until) if blocked_until else None,
        usage_json=row["usage_json"],
        last_success_measured_at=row["last_success_measured_at"],
        active=active,
        reset_moment_known=reset_moment_known,
    )


def discovery_requests_blocked(conn: sqlite3.Connection) -> RateLimitState | None:
    """Return the rate-limit row only when a Meta wait window is still active."""
    state = get_discovery_rate_limit(conn)
    if state is not None and state.active:
        return state
    return None


def own_account_succeeded_recently(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    within_hours: int = 12,
) -> bool:
    row = conn.execute(
        """
        SELECT r.measured_at
        FROM comparison_fetch_account_results ar
        JOIN comparison_fetch_runs r ON r.id = ar.fetch_run_id
        WHERE ar.account_id = ?
          AND ar.status = 'success'
        ORDER BY r.measured_at DESC, ar.id DESC
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    if not row:
        return False
    try:
        measured = datetime.fromisoformat(str(row["measured_at"]))
        if measured.tzinfo is None:
            measured = measured.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = datetime.now(tz=timezone.utc) - measured
    return age.total_seconds() <= within_hours * 3600


def account_result_exists(
    conn: sqlite3.Connection,
    *,
    fetch_run_id: int,
    account_id: int,
    status: str = "success",
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM comparison_fetch_account_results
        WHERE fetch_run_id = ? AND account_id = ? AND status = ?
        LIMIT 1
        """,
        (fetch_run_id, account_id, status),
    ).fetchone()
    return row is not None


def _row_to_account(row: sqlite3.Row) -> ComparisonAccount:
    return ComparisonAccount(
        account_id=int(row["id"]),
        platform=row["platform"],
        external_id=row["external_id"],
        name=row["name"],
        username=row["username"],
        comparison_role=row["comparison_role"],
        is_discovery_host=bool(row["is_discovery_host"]),
        access_status=row["access_status"] or "ok",
    )


@dataclass(frozen=True, slots=True)
class DiscoveryCheckpoint:
    account_id: int
    username: str
    period_start: str
    period_end: str
    cursor_after: str | None
    pages_fetched: int
    posts_fetched: int
    is_complete: bool
    last_fetch_run_id: int | None
    account_external_id: str | None
    account_payload_json: str | None


def get_discovery_checkpoint(
    conn: sqlite3.Connection,
    account_id: int,
) -> DiscoveryCheckpoint | None:
    row = conn.execute(
        """
        SELECT account_id, username, period_start, period_end, cursor_after,
               pages_fetched, posts_fetched, is_complete, last_fetch_run_id,
               account_external_id, account_payload_json
        FROM comparison_discovery_checkpoints
        WHERE account_id = ?
        """,
        (account_id,),
    ).fetchone()
    if not row:
        return None
    return DiscoveryCheckpoint(
        account_id=int(row["account_id"]),
        username=str(row["username"]),
        period_start=str(row["period_start"]),
        period_end=str(row["period_end"]),
        cursor_after=row["cursor_after"],
        pages_fetched=int(row["pages_fetched"] or 0),
        posts_fetched=int(row["posts_fetched"] or 0),
        is_complete=bool(row["is_complete"]),
        last_fetch_run_id=int(row["last_fetch_run_id"])
        if row["last_fetch_run_id"] is not None
        else None,
        account_external_id=row["account_external_id"],
        account_payload_json=row["account_payload_json"],
    )


def save_discovery_checkpoint(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    username: str,
    period_start: str,
    period_end: str,
    cursor_after: str | None,
    pages_fetched: int,
    posts_fetched: int,
    is_complete: bool,
    last_fetch_run_id: int | None,
    account_external_id: str | None = None,
    account_payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO comparison_discovery_checkpoints (
            account_id, username, period_start, period_end, cursor_after,
            pages_fetched, posts_fetched, is_complete, last_fetch_run_id,
            account_external_id, account_payload_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            username = excluded.username,
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            cursor_after = excluded.cursor_after,
            pages_fetched = excluded.pages_fetched,
            posts_fetched = excluded.posts_fetched,
            is_complete = excluded.is_complete,
            last_fetch_run_id = excluded.last_fetch_run_id,
            account_external_id = COALESCE(
                excluded.account_external_id,
                comparison_discovery_checkpoints.account_external_id
            ),
            account_payload_json = COALESCE(
                excluded.account_payload_json,
                comparison_discovery_checkpoints.account_payload_json
            ),
            updated_at = excluded.updated_at
        """,
        (
            account_id,
            normalize_username(username),
            period_start,
            period_end,
            cursor_after,
            pages_fetched,
            posts_fetched,
            1 if is_complete else 0,
            last_fetch_run_id,
            account_external_id,
            json.dumps(account_payload, ensure_ascii=False) if account_payload else None,
            utc_now_iso(),
        ),
    )


def clear_discovery_checkpoint(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute(
        "DELETE FROM comparison_discovery_checkpoints WHERE account_id = ?",
        (account_id,),
    )


def list_open_discovery_checkpoints(
    conn: sqlite3.Connection,
) -> list[DiscoveryCheckpoint]:
    """Incomplete Business Discovery fetches that can be resumed."""
    rows = conn.execute(
        """
        SELECT account_id, username, period_start, period_end, cursor_after,
               pages_fetched, posts_fetched, is_complete, last_fetch_run_id,
               account_external_id, account_payload_json
        FROM comparison_discovery_checkpoints
        WHERE is_complete = 0
          AND cursor_after IS NOT NULL
          AND TRIM(cursor_after) != ''
        ORDER BY username COLLATE NOCASE
        """
    ).fetchall()
    return [
        DiscoveryCheckpoint(
            account_id=int(row["account_id"]),
            username=str(row["username"]),
            period_start=str(row["period_start"]),
            period_end=str(row["period_end"]),
            cursor_after=row["cursor_after"],
            pages_fetched=int(row["pages_fetched"] or 0),
            posts_fetched=int(row["posts_fetched"] or 0),
            is_complete=bool(row["is_complete"]),
            last_fetch_run_id=int(row["last_fetch_run_id"])
            if row["last_fetch_run_id"] is not None
            else None,
            account_external_id=row["account_external_id"],
            account_payload_json=row["account_payload_json"],
        )
        for row in rows
    ]


def list_known_post_external_ids(
    conn: sqlite3.Connection,
    account_id: int,
) -> set[str]:
    rows = conn.execute(
        """
        SELECT external_id FROM posts
        WHERE account_id = ? AND platform = 'instagram' AND external_id IS NOT NULL
        """,
        (account_id,),
    ).fetchall()
    return {str(row["external_id"]) for row in rows if row["external_id"]}


def log_discovery_request(
    conn: sqlite3.Connection,
    *,
    fetch_run_id: int | None,
    account_id: int | None,
    username: str,
    page_index: int,
    page_size: int,
    posts_received: int,
    duration_ms: float | None,
    usage_headers: dict[str, Any] | None,
    cursor_before: str | None,
    cursor_after: str | None,
    status: str,
    measured_at: str,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO comparison_discovery_request_log (
            fetch_run_id, account_id, username, page_index, page_size,
            posts_received, duration_ms, usage_json, cursor_before, cursor_after,
            status, error_message, measured_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fetch_run_id,
            account_id,
            normalize_username(username),
            page_index,
            page_size,
            posts_received,
            duration_ms,
            json.dumps(usage_headers or {}, ensure_ascii=False),
            cursor_before,
            cursor_after,
            status,
            error_message,
            measured_at,
            utc_now_iso(),
        ),
    )
