"""Read-side analytics for the Social vergelijking page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db.comparison_repository import (
    ComparisonAccount,
    get_latest_completed_run,
    get_preferences,
    list_comparison_accounts,
)
from db.database import get_connection

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


@dataclass(frozen=True, slots=True)
class AccountComparisonRow:
    account_id: int
    username: str
    display_name: str
    role: str
    followers: int | None
    followers_measured_at: str | None
    posts_in_period: int
    likes_total: int | None
    comments_total: int | None
    likes_avg: float | None
    comments_avg: float | None
    interaction_avg: float | None
    posts_with_likes: int
    posts_with_comments: int
    posts_with_interaction: int
    metrics_incomplete: bool
    period_fetch_complete: bool | None
    post_metrics_measured_from: str | None
    post_metrics_measured_to: str | None
    measured_at: str | None
    has_fetched_data: bool
    last_success_measured_at: str | None


@dataclass(frozen=True, slots=True)
class ContentMixRow:
    account_id: int
    username: str
    display_name: str
    reels: int
    images: int
    carousels: int
    other_videos: int
    unknown: int
    total: int
    has_fetched_data: bool
    period_fetch_complete: bool | None
    last_success_measured_at: str | None


@dataclass(frozen=True, slots=True)
class TopPostRow:
    post_id: int
    account_name: str
    published_at: str
    content_type: str
    likes: int | None
    comments: int | None
    interaction: int | None
    permalink: str | None
    measured_at: str | None


@dataclass(frozen=True, slots=True)
class GrowthRow:
    account_id: int
    username: str
    display_name: str
    followers_start: int | None
    followers_end: int | None
    followers_delta: int | None
    followers_pct: float | None
    start_measured_at: str | None
    end_measured_at: str | None
    insufficient: bool
    measurement_days: int
    first_day: date | None = None
    last_day: date | None = None
    missing_note: str | None = None
    likes_delta: int | None = None
    comments_delta: int | None = None
    comparable_posts: int = 0
    posts_only_first_day: int = 0
    posts_only_last_day: int = 0
    engagement_incomplete: bool = False


@dataclass(frozen=True, slots=True)
class GrowthOverview:
    """Follower + engagement growth from real stored measurement days."""

    tracking_since: date | None
    rows: list[GrowthRow]


def _amsterdam_bounds(start: date, end: date) -> tuple[str, str]:
    start_dt = datetime.combine(start, time.min, tzinfo=AMSTERDAM).astimezone(timezone.utc)
    end_dt = datetime.combine(end, time.max.replace(microsecond=0), tzinfo=AMSTERDAM).astimezone(
        timezone.utc
    )
    return start_dt.isoformat(), end_dt.isoformat()


def _display_name(account: ComparisonAccount) -> str:
    if account.username:
        return f"@{account.username}"
    if account.name:
        return account.name
    return account.external_id


def _avg(total: int | None, count: int) -> float | None:
    if total is None or count <= 0:
        return None
    return total / count


def selected_accounts(
    own_account_id: int | None,
    competitor_usernames: list[str],
) -> list[ComparisonAccount]:
    with get_connection() as conn:
        accounts = list_comparison_accounts(conn)
        by_username = {
            (a.username or "").casefold(): a
            for a in accounts
            if a.username and a.access_status in {"ok", "pending"}
        }
        selected: list[ComparisonAccount] = []
        if own_account_id is not None:
            own = next((a for a in accounts if a.account_id == own_account_id), None)
            if own is not None:
                selected.append(own)
        for username in competitor_usernames:
            key = username.casefold()
            account = by_username.get(key)
            if account and all(a.account_id != account.account_id for a in selected):
                selected.append(account)
        return selected


def latest_measurement_note(conn) -> str | None:
    """Latest successful fetch measurement time (not a rate-limited/failed-only run)."""
    row = conn.execute(
        """
        SELECT r.measured_at
        FROM comparison_fetch_runs r
        WHERE EXISTS (
            SELECT 1 FROM comparison_fetch_account_results ar
            WHERE ar.fetch_run_id = r.id AND ar.status = 'success'
        )
        ORDER BY r.measured_at DESC, r.id DESC
        LIMIT 1
        """
    ).fetchone()
    if row and row["measured_at"]:
        return str(row["measured_at"])
    run = get_latest_completed_run(conn)
    if not run:
        return None
    return str(run["measured_at"])


def _account_last_success(conn, account_id: int) -> str | None:
    """Latest successful measurement timestamp for one account (UTC ISO)."""
    row = conn.execute(
        """
        SELECT MAX(ts) AS last_ts FROM (
            SELECT measured_at AS ts FROM account_measurements WHERE account_id = ?
            UNION ALL
            SELECT pm.measured_at AS ts
            FROM post_measurements pm
            JOIN posts p ON p.id = pm.post_id
            WHERE p.account_id = ?
            UNION ALL
            SELECT r.measured_at AS ts
            FROM comparison_fetch_account_results ar
            JOIN comparison_fetch_runs r ON r.id = ar.fetch_run_id
            WHERE ar.account_id = ? AND ar.status = 'success'
        )
        """,
        (account_id, account_id, account_id),
    ).fetchone()
    if not row or row["last_ts"] is None:
        return None
    return str(row["last_ts"])


def _account_has_fetched_data(conn, account_id: int) -> bool:
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM posts WHERE account_id = ? AND platform = 'instagram') AS posts,
            (SELECT COUNT(*) FROM account_measurements WHERE account_id = ?) AS measurements,
            (SELECT COUNT(*) FROM comparison_fetch_account_results
             WHERE account_id = ? AND status IN ('success', 'partial')) AS successes
        """,
        (account_id, account_id, account_id),
    ).fetchone()
    if not row:
        return False
    return int(row["posts"] or 0) > 0 or int(row["successes"] or 0) > 0 or int(row["measurements"] or 0) > 0


def _period_fetch_complete(conn, account_id: int) -> bool | None:
    """Whether the rolling 3-month media window was fully paged for this account.

    Checkpoint wins while a resume is pending; otherwise the latest account result.
    """
    cp = conn.execute(
        """
        SELECT is_complete
        FROM comparison_discovery_checkpoints
        WHERE account_id = ?
        """,
        (account_id,),
    ).fetchone()
    if cp is not None:
        return bool(int(cp["is_complete"] or 0) == 1)
    row = conn.execute(
        """
        SELECT is_complete
        FROM comparison_fetch_account_results
        WHERE account_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    if row is None:
        return None
    return bool(int(row["is_complete"] or 0) == 1)


def _amsterdam_day_key(measured_at: str) -> str | None:
    try:
        dt = datetime.fromisoformat(str(measured_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(AMSTERDAM).date().isoformat()
    except ValueError:
        return None


def _daily_measurement_points(measurements: list[Any]) -> list[Any]:
    """Keep the last measurement per Amsterdam calendar day."""
    by_day: dict[str, Any] = {}
    for row in measurements:
        day = _amsterdam_day_key(str(row["measured_at"]))
        if day is None:
            continue
        by_day[day] = row
    return [by_day[key] for key in sorted(by_day)]


def account_comparison_rows(
    *,
    own_account_id: int | None,
    competitor_usernames: list[str],
    period_start: date,
    period_end: date,
) -> tuple[list[AccountComparisonRow], str | None, bool]:
    start_iso, end_iso = _amsterdam_bounds(period_start, period_end)
    accounts = selected_accounts(own_account_id, competitor_usernames)
    rows: list[AccountComparisonRow] = []
    with get_connection() as conn:
        measured_at = latest_measurement_note(conn)
        any_incomplete = False
        for account in accounts:
            has_data = _account_has_fetched_data(conn, account.account_id)
            last_success = _account_last_success(conn, account.account_id)
            period_complete = _period_fetch_complete(conn, account.account_id)
            followers_row = conn.execute(
                """
                SELECT followers_count, measured_at
                FROM account_measurements
                WHERE account_id = ?
                ORDER BY measured_at DESC, id DESC
                LIMIT 1
                """,
                (account.account_id,),
            ).fetchone()
            post_rows = conn.execute(
                """
                SELECT p.id AS post_id,
                       (
                           SELECT pm.like_count
                           FROM post_measurements pm
                           WHERE pm.post_id = p.id
                           ORDER BY pm.measured_at DESC, pm.id DESC
                           LIMIT 1
                       ) AS like_count,
                       (
                           SELECT pm.comments_count
                           FROM post_measurements pm
                           WHERE pm.post_id = p.id
                           ORDER BY pm.measured_at DESC, pm.id DESC
                           LIMIT 1
                       ) AS comments_count,
                       (
                           SELECT pm.measured_at
                           FROM post_measurements pm
                           WHERE pm.post_id = p.id
                           ORDER BY pm.measured_at DESC, pm.id DESC
                           LIMIT 1
                       ) AS measured_at
                FROM posts p
                WHERE p.account_id = ?
                  AND p.platform = 'instagram'
                  AND p.published_at >= ?
                  AND p.published_at <= ?
                """,
                (account.account_id, start_iso, end_iso),
            ).fetchall()

            posts_in_period = len(post_rows)
            likes_values = [r["like_count"] for r in post_rows if r["like_count"] is not None]
            comments_values = [
                r["comments_count"] for r in post_rows if r["comments_count"] is not None
            ]
            interaction_values = []
            metric_times: list[str] = []
            for r in post_rows:
                if r["measured_at"]:
                    metric_times.append(str(r["measured_at"]))
                if r["like_count"] is None or r["comments_count"] is None:
                    continue
                interaction_values.append(int(r["like_count"]) + int(r["comments_count"]))

            likes_total = sum(likes_values) if likes_values else None
            comments_total = sum(comments_values) if comments_values else None
            incomplete = has_data and posts_in_period > 0 and (
                len(likes_values) < posts_in_period or len(comments_values) < posts_in_period
            )
            period_incomplete = period_complete is False
            any_incomplete = any_incomplete or incomplete or period_incomplete
            metric_from = min(metric_times) if metric_times else None
            metric_to = max(metric_times) if metric_times else None

            rows.append(
                AccountComparisonRow(
                    account_id=account.account_id,
                    username=account.username or "",
                    display_name=_display_name(account),
                    role=account.comparison_role or "",
                    followers=followers_row["followers_count"] if followers_row else None,
                    followers_measured_at=followers_row["measured_at"] if followers_row else None,
                    posts_in_period=posts_in_period,
                    likes_total=likes_total,
                    comments_total=comments_total,
                    likes_avg=_avg(likes_total, len(likes_values)),
                    comments_avg=_avg(comments_total, len(comments_values)),
                    interaction_avg=_avg(
                        sum(interaction_values) if interaction_values else None,
                        len(interaction_values),
                    ),
                    posts_with_likes=len(likes_values),
                    posts_with_comments=len(comments_values),
                    posts_with_interaction=len(interaction_values),
                    metrics_incomplete=incomplete,
                    period_fetch_complete=period_complete,
                    post_metrics_measured_from=metric_from,
                    post_metrics_measured_to=metric_to,
                    measured_at=measured_at,
                    has_fetched_data=has_data,
                    last_success_measured_at=last_success,
                )
            )
    return rows, measured_at if accounts else None, any_incomplete


def content_mix_rows(
    *,
    own_account_id: int | None,
    competitor_usernames: list[str],
    period_start: date,
    period_end: date,
) -> list[ContentMixRow]:
    start_iso, end_iso = _amsterdam_bounds(period_start, period_end)
    accounts = selected_accounts(own_account_id, competitor_usernames)
    rows: list[ContentMixRow] = []
    with get_connection() as conn:
        for account in accounts:
            has_data = _account_has_fetched_data(conn, account.account_id)
            last_success = _account_last_success(conn, account.account_id)
            period_complete = _period_fetch_complete(conn, account.account_id)
            post_rows = conn.execute(
                """
                SELECT content_type, media_type, media_product_type
                FROM posts
                WHERE account_id = ?
                  AND platform = 'instagram'
                  AND published_at >= ?
                  AND published_at <= ?
                """,
                (account.account_id, start_iso, end_iso),
            ).fetchall()
            reels = images = carousels = other_videos = unknown = 0
            for post in post_rows:
                content = (post["content_type"] or "").lower()
                media = (post["media_type"] or "").upper()
                product = (post["media_product_type"] or "").upper()
                if product == "REELS" or content == "reel":
                    reels += 1
                elif media == "CAROUSEL_ALBUM" or content == "carousel":
                    carousels += 1
                elif media == "IMAGE" or content == "image":
                    images += 1
                elif media == "VIDEO" or content == "video":
                    other_videos += 1
                else:
                    unknown += 1
            rows.append(
                ContentMixRow(
                    account_id=account.account_id,
                    username=account.username or "",
                    display_name=_display_name(account),
                    reels=reels,
                    images=images,
                    carousels=carousels,
                    other_videos=other_videos,
                    unknown=unknown,
                    total=len(post_rows),
                    has_fetched_data=has_data,
                    period_fetch_complete=period_complete,
                    last_success_measured_at=last_success,
                )
            )
    return rows


def top_posts(
    *,
    own_account_id: int | None,
    competitor_usernames: list[str],
    period_start: date,
    period_end: date,
    limit: int = 20,
) -> tuple[list[TopPostRow], bool, list[str]]:
    """Return top posts, incompleteness flag, and accounts still missing data."""
    start_iso, end_iso = _amsterdam_bounds(period_start, period_end)
    accounts = selected_accounts(own_account_id, competitor_usernames)
    if not accounts:
        return [], False, []
    account_ids = [a.account_id for a in accounts]
    placeholders = ",".join("?" for _ in account_ids)
    with get_connection() as conn:
        missing = [
            _display_name(account)
            for account in accounts
            if not _account_has_fetched_data(conn, account.account_id)
        ]
        rows = conn.execute(
            f"""
            SELECT p.id AS post_id,
                   p.permalink,
                   p.published_at,
                   p.content_type,
                   p.media_type,
                   p.media_product_type,
                   a.username,
                   a.name,
                   (
                       SELECT pm.like_count FROM post_measurements pm
                       WHERE pm.post_id = p.id
                       ORDER BY pm.measured_at DESC, pm.id DESC LIMIT 1
                   ) AS like_count,
                   (
                       SELECT pm.comments_count FROM post_measurements pm
                       WHERE pm.post_id = p.id
                       ORDER BY pm.measured_at DESC, pm.id DESC LIMIT 1
                   ) AS comments_count,
                   (
                       SELECT pm.measured_at FROM post_measurements pm
                       WHERE pm.post_id = p.id
                       ORDER BY pm.measured_at DESC, pm.id DESC LIMIT 1
                   ) AS measured_at
            FROM posts p
            JOIN accounts a ON a.id = p.account_id
            WHERE p.account_id IN ({placeholders})
              AND p.platform = 'instagram'
              AND p.published_at >= ?
              AND p.published_at <= ?
            """,
            [*account_ids, start_iso, end_iso],
        ).fetchall()

    ranked: list[TopPostRow] = []
    incomplete = False
    for row in rows:
        likes = row["like_count"]
        comments = row["comments_count"]
        if likes is None or comments is None:
            incomplete = True
            interaction = None
        else:
            interaction = int(likes) + int(comments)
        if interaction is None:
            continue
        username = row["username"]
        name = row["name"]
        account_name = f"@{username}" if username else (name or "onbekend")
        content = row["content_type"] or "unknown"
        ranked.append(
            TopPostRow(
                post_id=int(row["post_id"]),
                account_name=account_name,
                published_at=str(row["published_at"]),
                content_type=content,
                likes=int(likes) if likes is not None else None,
                comments=int(comments) if comments is not None else None,
                interaction=interaction,
                permalink=row["permalink"],
                measured_at=row["measured_at"],
            )
        )
    ranked.sort(key=lambda item: (item.interaction or -1, item.published_at), reverse=True)
    return ranked[:limit], incomplete, missing


def _parse_amsterdam_date(measured_at: str | None) -> date | None:
    key = _amsterdam_day_key(str(measured_at)) if measured_at else None
    if key is None:
        return None
    try:
        return date.fromisoformat(key)
    except ValueError:
        return None


def _format_day_nl(value: date) -> str:
    return value.strftime("%d-%m-%Y")


def _missing_measurement_note(daily: list[Any]) -> str:
    if not daily:
        return "Eerste en tweede meetdag ontbreken."
    if len(daily) == 1:
        day = _parse_amsterdam_date(daily[0]["measured_at"])
        label = _format_day_nl(day) if day else "onbekend"
        return f"Tweede meetdag ontbreekt (enige meetdag: {label})."
    return "Onvoldoende meetgeschiedenis."


def _engagement_between_days(
    conn,
    *,
    account_id: int,
    first_day: date,
    last_day: date,
) -> tuple[int | None, int | None, int, int, int, bool]:
    """Compare likes/comments for posts measured on both Amsterdam days.

    Returns:
        likes_delta, comments_delta, comparable_count,
        only_first, only_last, incomplete_metrics
    """
    rows = conn.execute(
        """
        SELECT pm.post_id, pm.measured_at, pm.like_count, pm.comments_count
        FROM post_measurements pm
        JOIN posts p ON p.id = pm.post_id
        WHERE p.account_id = ?
          AND p.platform = 'instagram'
        ORDER BY pm.measured_at ASC, pm.id ASC
        """,
        (account_id,),
    ).fetchall()

    first_by_post: dict[int, Any] = {}
    last_by_post: dict[int, Any] = {}
    for row in rows:
        day = _parse_amsterdam_date(row["measured_at"])
        if day is None:
            continue
        post_id = int(row["post_id"])
        if day == first_day:
            first_by_post[post_id] = row
        elif day == last_day:
            last_by_post[post_id] = row

    shared = set(first_by_post) & set(last_by_post)
    only_first = len(first_by_post) - len(shared)
    only_last = len(last_by_post) - len(shared)
    if not shared:
        return None, None, 0, only_first, only_last, False

    likes_delta = 0
    comments_delta = 0
    likes_ok = False
    comments_ok = False
    incomplete = False
    for post_id in shared:
        a = first_by_post[post_id]
        b = last_by_post[post_id]
        if a["like_count"] is None or b["like_count"] is None:
            incomplete = True
        else:
            likes_delta += int(b["like_count"]) - int(a["like_count"])
            likes_ok = True
        if a["comments_count"] is None or b["comments_count"] is None:
            incomplete = True
        else:
            comments_delta += int(b["comments_count"]) - int(a["comments_count"])
            comments_ok = True

    return (
        likes_delta if likes_ok else None,
        comments_delta if comments_ok else None,
        len(shared),
        only_first,
        only_last,
        incomplete,
    )


def growth_rows(
    *,
    own_account_id: int | None,
    competitor_usernames: list[str],
) -> list[GrowthRow]:
    return growth_overview(
        own_account_id=own_account_id,
        competitor_usernames=competitor_usernames,
    ).rows


def growth_overview(
    *,
    own_account_id: int | None,
    competitor_usernames: list[str],
) -> GrowthOverview:
    """Build growth overview from real account_measurements only (no invented days)."""
    accounts = selected_accounts(own_account_id, competitor_usernames)
    rows: list[GrowthRow] = []
    tracking_since: date | None = None
    with get_connection() as conn:
        for account in accounts:
            measurements = conn.execute(
                """
                SELECT followers_count, measured_at
                FROM account_measurements
                WHERE account_id = ?
                ORDER BY measured_at ASC, id ASC
                """,
                (account.account_id,),
            ).fetchall()
            # Keep all raw rows in DB; for display use one point per Amsterdam day.
            daily = _daily_measurement_points(list(measurements))
            first_day = _parse_amsterdam_date(daily[0]["measured_at"]) if daily else None
            last_day = (
                _parse_amsterdam_date(daily[-1]["measured_at"]) if daily else None
            )
            if first_day is not None and (
                tracking_since is None or first_day < tracking_since
            ):
                tracking_since = first_day

            if len(daily) < 2:
                only = daily[0] if daily else None
                only_followers = (
                    int(only["followers_count"])
                    if only is not None and only["followers_count"] is not None
                    else None
                )
                rows.append(
                    GrowthRow(
                        account_id=account.account_id,
                        username=account.username or "",
                        display_name=_display_name(account),
                        followers_start=only_followers,
                        followers_end=None,
                        followers_delta=None,
                        followers_pct=None,
                        start_measured_at=only["measured_at"] if only is not None else None,
                        end_measured_at=None,
                        insufficient=True,
                        measurement_days=len(daily),
                        first_day=first_day,
                        last_day=last_day,
                        missing_note=_missing_measurement_note(daily),
                    )
                )
                continue

            first = daily[0]
            last = daily[-1]
            start_val = first["followers_count"]
            end_val = last["followers_count"]
            delta = None
            pct = None
            if start_val is not None and end_val is not None:
                delta = int(end_val) - int(start_val)
                if int(start_val) == 0:
                    pct = None
                else:
                    pct = (delta / int(start_val)) * 100.0

            likes_delta = comments_delta = None
            comparable = only_first = only_last = 0
            engagement_incomplete = False
            if first_day is not None and last_day is not None and first_day != last_day:
                (
                    likes_delta,
                    comments_delta,
                    comparable,
                    only_first,
                    only_last,
                    engagement_incomplete,
                ) = _engagement_between_days(
                    conn,
                    account_id=account.account_id,
                    first_day=first_day,
                    last_day=last_day,
                )

            rows.append(
                GrowthRow(
                    account_id=account.account_id,
                    username=account.username or "",
                    display_name=_display_name(account),
                    followers_start=int(start_val) if start_val is not None else None,
                    followers_end=int(end_val) if end_val is not None else None,
                    followers_delta=delta,
                    followers_pct=pct,
                    start_measured_at=first["measured_at"],
                    end_measured_at=last["measured_at"],
                    insufficient=False,
                    measurement_days=len(daily),
                    first_day=first_day,
                    last_day=last_day,
                    missing_note=None,
                    likes_delta=likes_delta,
                    comments_delta=comments_delta,
                    comparable_posts=comparable,
                    posts_only_first_day=only_first,
                    posts_only_last_day=only_last,
                    engagement_incomplete=engagement_incomplete,
                )
            )
    return GrowthOverview(tracking_since=tracking_since, rows=rows)


def load_preference_state() -> tuple[int | None, list[str]]:
    with get_connection() as conn:
        return get_preferences(conn)
