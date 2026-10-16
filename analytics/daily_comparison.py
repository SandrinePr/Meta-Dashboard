"""Day-over-day comparison from stored measurements only (no Meta API)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from analytics.comparison import _period_fetch_complete, selected_accounts
from db.comparison_repository import ComparisonAccount
from db.database import get_connection

AMSTERDAM = ZoneInfo("Europe/Amsterdam")

INSUFFICIENT_DAILY_HISTORY = (
    "Nog onvoldoende meetgeschiedenis. Vergelijking beschikbaar na een volgende meting."
)
NO_SECOND_MEASUREMENT = "Nog geen tweede meting"
MISSING_START_MEASUREMENT = "Beginmeting ontbreekt"
NOT_MEASURED = "Niet gemeten"
PUBLISHED_POSTS_UNKNOWN = "Niet bekend"

# Coverage of the published-posts count between two measurement moments.
PUBLISHED_COVERAGE_EXACT = "exact"
PUBLISHED_COVERAGE_MINIMUM = "minimum"
PUBLISHED_COVERAGE_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DailyAccountRow:
    account_id: int
    display_name: str
    followers_day1: int | None
    followers_day2: int | None
    followers_delta: int | None
    measured_at_day1: str | None
    measured_at_day2: str | None
    incomplete: bool
    posts_only_day1: int = 0
    posts_only_day2: int = 0
    comparable_posts: int = 0
    likes_delta: int | None = None
    comments_delta: int | None = None
    engagement_incomplete: bool = False
    published_posts_count: int | None = None
    published_posts_coverage: str = PUBLISHED_COVERAGE_UNKNOWN


@dataclass(frozen=True, slots=True)
class DailyPostDeltaRow:
    post_id: int
    account_name: str
    published_at: str | None
    content_type: str
    permalink: str | None
    likes_day1: int | None
    likes_day2: int | None
    likes_delta: int | None
    comments_day1: int | None
    comments_day2: int | None
    comments_delta: int | None
    measured_at_day1: str | None
    measured_at_day2: str | None
    incomplete: bool


@dataclass(frozen=True, slots=True)
class DailyNewPostRow:
    post_id: int
    account_name: str
    published_at: str | None
    content_type: str
    permalink: str | None
    likes: int | None
    comments: int | None
    measured_at: str | None


@dataclass(frozen=True, slots=True)
class DailyComparisonResult:
    day1: date
    day2: date
    account_rows: list[DailyAccountRow]
    comparable_posts: list[DailyPostDeltaRow]
    new_posts: list[DailyNewPostRow]
    day1_moments: list[str]
    day2_moments: list[str]
    warnings: list[str] = field(default_factory=list)
    incomplete: bool = False


@dataclass(frozen=True, slots=True)
class AccountDeltaSummary:
    """Per-account follower + engagement delta between two measurement snapshots."""

    account_id: int
    display_name: str
    followers_start: int | None
    followers_end: int | None
    followers_delta: int | None
    start_measured_at: str | None
    end_measured_at: str | None
    insufficient: bool
    missing_note: str | None
    likes_delta: int | None
    comments_delta: int | None
    comparable_posts: int
    posts_only_start: int
    posts_only_end: int
    engagement_incomplete: bool
    published_posts_count: int | None = None
    published_posts_coverage: str = PUBLISHED_COVERAGE_UNKNOWN


@dataclass(frozen=True, slots=True)
class IntradayComparisonResult:
    day: date
    account_rows: list[AccountDeltaSummary]
    warnings: list[str] = field(default_factory=list)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def amsterdam_day(value: str | None) -> date | None:
    dt = _parse_utc(value)
    if dt is None:
        return None
    return dt.astimezone(AMSTERDAM).date()


def _display_name(account: ComparisonAccount) -> str:
    if account.username:
        return f"@{account.username}"
    if account.name:
        return account.name
    return account.external_id


def _delta(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return int(b) - int(a)


def count_posts_published_between(
    posts: list[Any],
    *,
    account_id: int,
    start_measured_at: str | None,
    end_measured_at: str | None,
) -> int:
    """Unique hoofdposts with published_at strictly after start and on/before end.

    Uses publication time only — newly discovered older posts are excluded.
    """
    start = _parse_utc(start_measured_at)
    end = _parse_utc(end_measured_at)
    if start is None or end is None or end <= start:
        return 0
    seen: set[int] = set()
    for post in posts:
        if int(post["account_id"]) != account_id:
            continue
        pub = _parse_utc(post["published_at"])
        if pub is None:
            continue
        if not (start < pub <= end):
            continue
        keys = set(post.keys())
        post_id = int(post["post_id"] if "post_id" in keys else post["id"])
        seen.add(post_id)
    return len(seen)


def resolve_published_posts_metric(
    *,
    account_id: int,
    start_measured_at: str | None,
    end_measured_at: str | None,
    posts: list[Any],
    period_fetch_complete: bool | None,
) -> tuple[int | None, str]:
    """Return (count, coverage) for posts published between two measurement moments."""
    if not start_measured_at or not end_measured_at:
        return None, PUBLISHED_COVERAGE_UNKNOWN
    start = _parse_utc(start_measured_at)
    end = _parse_utc(end_measured_at)
    if start is None or end is None or end <= start:
        return None, PUBLISHED_COVERAGE_UNKNOWN

    count = count_posts_published_between(
        posts,
        account_id=account_id,
        start_measured_at=start_measured_at,
        end_measured_at=end_measured_at,
    )
    if period_fetch_complete is True:
        return count, PUBLISHED_COVERAGE_EXACT
    # Incomplete or unknown fetch completeness: known lower bound only.
    return count, PUBLISHED_COVERAGE_MINIMUM


def format_published_posts_cell(
    count: int | None,
    coverage: str,
) -> str:
    if coverage == PUBLISHED_COVERAGE_UNKNOWN or count is None:
        return PUBLISHED_POSTS_UNKNOWN
    if coverage == PUBLISHED_COVERAGE_MINIMUM:
        return f"Minimaal {count}"
    return str(count)


def list_measurement_days() -> list[date]:
    """Distinct Amsterdam calendar days that have any stored measurement."""
    days: set[date] = set()
    with get_connection() as conn:
        for row in conn.execute("SELECT measured_at FROM account_measurements").fetchall():
            day = amsterdam_day(row["measured_at"])
            if day is not None:
                days.add(day)
        for row in conn.execute("SELECT measured_at FROM post_measurements").fetchall():
            day = amsterdam_day(row["measured_at"])
            if day is not None:
                days.add(day)
    return sorted(days)


def default_day_pair(days: list[date] | None = None) -> tuple[date | None, date | None]:
    """Newest day and previous day, or (None, None) if fewer than two days."""
    available = days if days is not None else list_measurement_days()
    if len(available) < 2:
        return None, None
    return available[-2], available[-1]


def _last_by_day(
    rows: list[Any],
    *,
    day: date,
    id_key: str,
) -> dict[Any, Any]:
    """Keep the last measurement per id on the given Amsterdam day."""
    chosen: dict[Any, Any] = {}
    for row in rows:
        row_day = amsterdam_day(row["measured_at"])
        if row_day != day:
            continue
        key = row[id_key]
        prev = chosen.get(key)
        if prev is None:
            chosen[key] = row
            continue
        prev_ts = str(prev["measured_at"])
        cur_ts = str(row["measured_at"])
        if cur_ts > prev_ts or (
            cur_ts == prev_ts and int(row["id"]) > int(prev["id"])
        ):
            chosen[key] = row
    return chosen


def _moment_list(rows: list[Any], day: date) -> list[str]:
    moments = sorted(
        {
            str(row["measured_at"])
            for row in rows
            if amsterdam_day(row["measured_at"]) == day and row["measured_at"]
        }
    )
    return moments


def build_daily_comparison(
    *,
    own_account_id: int | None,
    competitor_usernames: list[str],
    day1: date,
    day2: date,
) -> DailyComparisonResult:
    """Compare stored measurements between two Amsterdam calendar days."""
    if day1 > day2:
        day1, day2 = day2, day1

    accounts = selected_accounts(own_account_id, competitor_usernames)
    if not accounts:
        return DailyComparisonResult(
            day1=day1,
            day2=day2,
            account_rows=[],
            comparable_posts=[],
            new_posts=[],
            day1_moments=[],
            day2_moments=[],
            warnings=["Geen accounts geselecteerd."],
            incomplete=True,
        )

    account_ids = [a.account_id for a in accounts]
    placeholders = ",".join("?" for _ in account_ids)

    with get_connection() as conn:
        account_meas = conn.execute(
            f"""
            SELECT id, account_id, measured_at, followers_count
            FROM account_measurements
            WHERE account_id IN ({placeholders})
            ORDER BY measured_at ASC, id ASC
            """,
            account_ids,
        ).fetchall()
        post_meas = conn.execute(
            f"""
            SELECT pm.id, pm.post_id, pm.measured_at, pm.like_count, pm.comments_count,
                   p.account_id, p.published_at, p.content_type, p.permalink,
                   a.username, a.name
            FROM post_measurements pm
            JOIN posts p ON p.id = pm.post_id
            JOIN accounts a ON a.id = p.account_id
            WHERE p.account_id IN ({placeholders})
              AND p.platform = 'instagram'
            ORDER BY pm.measured_at ASC, pm.id ASC
            """,
            account_ids,
        ).fetchall()
        posts = conn.execute(
            f"""
            SELECT p.id AS post_id, p.account_id, p.published_at, p.content_type, p.permalink,
                   a.username, a.name
            FROM posts p
            JOIN accounts a ON a.id = p.account_id
            WHERE p.account_id IN ({placeholders})
              AND p.platform = 'instagram'
            """,
            account_ids,
        ).fetchall()
        period_complete_by_account = {
            account.account_id: _period_fetch_complete(conn, account.account_id)
            for account in accounts
        }

    posts_list = list(posts)
    day1_account = _last_by_day(list(account_meas), day=day1, id_key="account_id")
    day2_account = _last_by_day(list(account_meas), day=day2, id_key="account_id")
    day1_posts = _last_by_day(list(post_meas), day=day1, id_key="post_id")
    day2_posts = _last_by_day(list(post_meas), day=day2, id_key="post_id")

    comparable: list[DailyPostDeltaRow] = []
    shared_ids = set(day1_posts) & set(day2_posts)
    incomplete = False
    for post_id in sorted(shared_ids):
        p1 = day1_posts[post_id]
        p2 = day2_posts[post_id]
        likes1 = int(p1["like_count"]) if p1["like_count"] is not None else None
        likes2 = int(p2["like_count"]) if p2["like_count"] is not None else None
        c1 = int(p1["comments_count"]) if p1["comments_count"] is not None else None
        c2 = int(p2["comments_count"]) if p2["comments_count"] is not None else None
        post_incomplete = likes1 is None or likes2 is None or c1 is None or c2 is None
        if post_incomplete:
            incomplete = True
        username = p2["username"] or p1["username"]
        name = p2["name"] or p1["name"]
        account_name = f"@{username}" if username else (name or "onbekend")
        comparable.append(
            DailyPostDeltaRow(
                post_id=int(post_id),
                account_name=account_name,
                published_at=p2["published_at"] or p1["published_at"],
                content_type=str(p2["content_type"] or p1["content_type"] or "unknown"),
                permalink=p2["permalink"] or p1["permalink"],
                likes_day1=likes1,
                likes_day2=likes2,
                likes_delta=_delta(likes1, likes2),
                comments_day1=c1,
                comments_day2=c2,
                comments_delta=_delta(c1, c2),
                measured_at_day1=str(p1["measured_at"]),
                measured_at_day2=str(p2["measured_at"]),
                incomplete=post_incomplete,
            )
        )
    comparable.sort(
        key=lambda row: (
            abs(row.likes_delta or 0) + abs(row.comments_delta or 0),
            row.published_at or "",
        ),
        reverse=True,
    )

    posts_by_account: dict[int, list[DailyPostDeltaRow]] = {}
    for post in comparable:
        # Map via account_id from either day measurement.
        aid = None
        src = day2_posts.get(post.post_id) or day1_posts.get(post.post_id)
        if src is not None:
            aid = int(src["account_id"])
        if aid is not None:
            posts_by_account.setdefault(aid, []).append(post)

    day1_ids_by_account: dict[int, set[int]] = {}
    day2_ids_by_account: dict[int, set[int]] = {}
    for post_id, row in day1_posts.items():
        day1_ids_by_account.setdefault(int(row["account_id"]), set()).add(int(post_id))
    for post_id, row in day2_posts.items():
        day2_ids_by_account.setdefault(int(row["account_id"]), set()).add(int(post_id))

    account_rows: list[DailyAccountRow] = []
    warnings: list[str] = []

    for account in accounts:
        m1 = day1_account.get(account.account_id)
        m2 = day2_account.get(account.account_id)
        f1 = int(m1["followers_count"]) if m1 and m1["followers_count"] is not None else None
        f2 = int(m2["followers_count"]) if m2 and m2["followers_count"] is not None else None
        row_incomplete = f1 is None or f2 is None
        if row_incomplete:
            incomplete = True
        ids1 = day1_ids_by_account.get(account.account_id, set())
        ids2 = day2_ids_by_account.get(account.account_id, set())
        shared_acc = ids1 & ids2
        account_posts = posts_by_account.get(account.account_id, [])
        likes_ok = False
        comments_ok = False
        likes_delta = 0
        comments_delta = 0
        engagement_incomplete = False
        for post in account_posts:
            if post.likes_day1 is None or post.likes_day2 is None:
                engagement_incomplete = True
            else:
                likes_delta += int(post.likes_delta or 0)
                likes_ok = True
            if post.comments_day1 is None or post.comments_day2 is None:
                engagement_incomplete = True
            else:
                comments_delta += int(post.comments_delta or 0)
                comments_ok = True
        start_at = str(m1["measured_at"]) if m1 else None
        end_at = str(m2["measured_at"]) if m2 else None
        published_count, published_coverage = resolve_published_posts_metric(
            account_id=account.account_id,
            start_measured_at=start_at,
            end_measured_at=end_at,
            posts=posts_list,
            period_fetch_complete=period_complete_by_account.get(account.account_id),
        )
        account_rows.append(
            DailyAccountRow(
                account_id=account.account_id,
                display_name=_display_name(account),
                followers_day1=f1,
                followers_day2=f2,
                followers_delta=_delta(f1, f2),
                measured_at_day1=start_at,
                measured_at_day2=end_at,
                incomplete=row_incomplete,
                posts_only_day1=len(ids1 - shared_acc),
                posts_only_day2=len(ids2 - shared_acc),
                comparable_posts=len(shared_acc),
                likes_delta=likes_delta if likes_ok else None,
                comments_delta=comments_delta if comments_ok else None,
                engagement_incomplete=engagement_incomplete,
                published_posts_count=published_count,
                published_posts_coverage=published_coverage,
            )
        )

    # Nieuwe posts: published between each account's measurement moments (not discovery time).
    new_posts: list[DailyNewPostRow] = []
    seen_new: set[int] = set()
    for row in account_rows:
        if not row.measured_at_day1 or not row.measured_at_day2:
            continue
        start = _parse_utc(row.measured_at_day1)
        end = _parse_utc(row.measured_at_day2)
        if start is None or end is None:
            continue
        for post in posts_list:
            if int(post["account_id"]) != row.account_id:
                continue
            pub = _parse_utc(post["published_at"])
            if pub is None or not (start < pub <= end):
                continue
            post_id = int(post["post_id"])
            if post_id in seen_new:
                continue
            seen_new.add(post_id)
            m2 = day2_posts.get(post_id)
            username = post["username"]
            name = post["name"]
            account_name = f"@{username}" if username else (name or "onbekend")
            new_posts.append(
                DailyNewPostRow(
                    post_id=post_id,
                    account_name=account_name,
                    published_at=post["published_at"],
                    content_type=str(post["content_type"] or "unknown"),
                    permalink=post["permalink"],
                    likes=int(m2["like_count"]) if m2 and m2["like_count"] is not None else None,
                    comments=(
                        int(m2["comments_count"])
                        if m2 and m2["comments_count"] is not None
                        else None
                    ),
                    measured_at=str(m2["measured_at"]) if m2 else None,
                )
            )
    new_posts.sort(key=lambda row: row.published_at or "", reverse=True)

    if incomplete:
        warnings.append(
            "Sommige accounts of posts missen een meting op één van de gekozen dagen."
        )
    only_day1 = len(day1_posts) - len(shared_ids)
    only_day2 = len(day2_posts) - len(shared_ids)
    if only_day1 > 0 or only_day2 > 0:
        warnings.append(
            f"Posts alleen op één meetdag gemeten: {only_day1} op dag 1, {only_day2} op dag 2 "
            "(niet in de likes/reacties-vergelijking)."
        )

    return DailyComparisonResult(
        day1=day1,
        day2=day2,
        account_rows=account_rows,
        comparable_posts=comparable,
        new_posts=new_posts,
        day1_moments=_moment_list(list(account_meas) + list(post_meas), day1),
        day2_moments=_moment_list(list(account_meas) + list(post_meas), day2),
        warnings=warnings,
        incomplete=incomplete,
    )


def _growth_missing_note(
    *,
    start_measured_at: str | None,
    end_measured_at: str | None,
    followers_start: int | None,
    followers_end: int | None,
) -> str | None:
    """Table-only message when begin/end are not comparable."""
    if followers_start is not None and followers_end is not None:
        return None
    if end_measured_at and not start_measured_at:
        return MISSING_START_MEASUREMENT
    if followers_end is not None and followers_start is None:
        return MISSING_START_MEASUREMENT
    return NO_SECOND_MEASUREMENT


def summarize_daily_account_deltas(
    result: DailyComparisonResult,
) -> list[AccountDeltaSummary]:
    """Aggregate day-over-day follower + engagement deltas per account."""
    summaries: list[AccountDeltaSummary] = []
    for row in result.account_rows:
        insufficient = row.incomplete or row.followers_delta is None
        summaries.append(
            AccountDeltaSummary(
                account_id=row.account_id,
                display_name=row.display_name,
                followers_start=row.followers_day1,
                followers_end=row.followers_day2,
                followers_delta=row.followers_delta,
                start_measured_at=row.measured_at_day1,
                end_measured_at=row.measured_at_day2,
                insufficient=insufficient,
                missing_note=_growth_missing_note(
                    start_measured_at=row.measured_at_day1,
                    end_measured_at=row.measured_at_day2,
                    followers_start=row.followers_day1,
                    followers_end=row.followers_day2,
                )
                if insufficient
                else None,
                likes_delta=row.likes_delta,
                comments_delta=row.comments_delta,
                comparable_posts=row.comparable_posts,
                posts_only_start=row.posts_only_day1,
                posts_only_end=row.posts_only_day2,
                engagement_incomplete=row.engagement_incomplete,
                published_posts_count=row.published_posts_count,
                published_posts_coverage=row.published_posts_coverage,
            )
        )
    return summaries


def _first_last_by_day(
    rows: list[Any],
    *,
    day: date,
    id_key: str,
) -> tuple[dict[Any, Any], dict[Any, Any]]:
    """First and last measurement per id on one Amsterdam day."""
    first: dict[Any, Any] = {}
    last: dict[Any, Any] = {}
    for row in rows:
        row_day = amsterdam_day(row["measured_at"])
        if row_day != day:
            continue
        key = row[id_key]
        cur_ts = str(row["measured_at"])
        cur_id = int(row["id"])
        prev_first = first.get(key)
        if prev_first is None:
            first[key] = row
        else:
            prev_ts = str(prev_first["measured_at"])
            if cur_ts < prev_ts or (
                cur_ts == prev_ts and cur_id < int(prev_first["id"])
            ):
                first[key] = row
        prev_last = last.get(key)
        if prev_last is None:
            last[key] = row
        else:
            prev_ts = str(prev_last["measured_at"])
            if cur_ts > prev_ts or (
                cur_ts == prev_ts and cur_id > int(prev_last["id"])
            ):
                last[key] = row
    return first, last


def build_intraday_comparison(
    *,
    own_account_id: int | None,
    competitor_usernames: list[str],
    day: date,
) -> IntradayComparisonResult:
    """Compare first vs last real measurement within one Amsterdam calendar day."""
    accounts = selected_accounts(own_account_id, competitor_usernames)
    if not accounts:
        return IntradayComparisonResult(
            day=day,
            account_rows=[],
            warnings=["Geen accounts geselecteerd."],
        )

    account_ids = [a.account_id for a in accounts]
    placeholders = ",".join("?" for _ in account_ids)
    with get_connection() as conn:
        account_meas = conn.execute(
            f"""
            SELECT id, account_id, measured_at, followers_count, fetch_run_id
            FROM account_measurements
            WHERE account_id IN ({placeholders})
            ORDER BY measured_at ASC, id ASC
            """,
            account_ids,
        ).fetchall()
        post_meas = conn.execute(
            f"""
            SELECT pm.id, pm.post_id, pm.measured_at, pm.like_count, pm.comments_count,
                   pm.fetch_run_id, p.account_id, a.username, a.name
            FROM post_measurements pm
            JOIN posts p ON p.id = pm.post_id
            JOIN accounts a ON a.id = p.account_id
            WHERE p.account_id IN ({placeholders})
              AND p.platform = 'instagram'
            ORDER BY pm.measured_at ASC, pm.id ASC
            """,
            account_ids,
        ).fetchall()
        posts = conn.execute(
            f"""
            SELECT p.id AS post_id, p.account_id, p.published_at
            FROM posts p
            WHERE p.account_id IN ({placeholders})
              AND p.platform = 'instagram'
            """,
            account_ids,
        ).fetchall()
        period_complete_by_account = {
            account.account_id: _period_fetch_complete(conn, account.account_id)
            for account in accounts
        }

    account_meas_list = list(account_meas)
    post_meas_list = list(post_meas)
    posts_list = list(posts)
    summaries: list[AccountDeltaSummary] = []
    warnings: list[str] = []

    for account in accounts:
        moments = [
            row
            for row in account_meas_list
            if int(row["account_id"]) == account.account_id
            and amsterdam_day(row["measured_at"]) == day
        ]
        # Distinct measurement moments (timestamp); need at least two.
        distinct_ts = sorted({str(row["measured_at"]) for row in moments if row["measured_at"]})
        if len(moments) < 2 or len(distinct_ts) < 2:
            start_only = str(moments[0]["measured_at"]) if moments else None
            published_count, published_coverage = resolve_published_posts_metric(
                account_id=account.account_id,
                start_measured_at=start_only,
                end_measured_at=None,
                posts=posts_list,
                period_fetch_complete=period_complete_by_account.get(account.account_id),
            )
            summaries.append(
                AccountDeltaSummary(
                    account_id=account.account_id,
                    display_name=_display_name(account),
                    followers_start=(
                        int(moments[0]["followers_count"])
                        if moments and moments[0]["followers_count"] is not None
                        else None
                    ),
                    followers_end=None,
                    followers_delta=None,
                    start_measured_at=start_only,
                    end_measured_at=None,
                    insufficient=True,
                    missing_note=NO_SECOND_MEASUREMENT,
                    likes_delta=None,
                    comments_delta=None,
                    comparable_posts=0,
                    posts_only_start=0,
                    posts_only_end=0,
                    engagement_incomplete=False,
                    published_posts_count=published_count,
                    published_posts_coverage=published_coverage,
                )
            )
            continue

        first = moments[0]
        last = moments[-1]
        # Prefer first/last by distinct timestamp order.
        first = next(row for row in moments if str(row["measured_at"]) == distinct_ts[0])
        last = next(
            row for row in reversed(moments) if str(row["measured_at"]) == distinct_ts[-1]
        )
        f1 = (
            int(first["followers_count"])
            if first["followers_count"] is not None
            else None
        )
        f2 = (
            int(last["followers_count"])
            if last["followers_count"] is not None
            else None
        )
        first_run = first["fetch_run_id"]
        last_run = last["fetch_run_id"]
        start_at = str(first["measured_at"])
        end_at = str(last["measured_at"])

        posts_first: dict[int, Any] = {}
        posts_last: dict[int, Any] = {}
        for row in post_meas_list:
            if int(row["account_id"]) != account.account_id:
                continue
            if amsterdam_day(row["measured_at"]) != day:
                continue
            post_id = int(row["post_id"])
            if first_run is not None and row["fetch_run_id"] == first_run:
                posts_first[post_id] = row
            if last_run is not None and row["fetch_run_id"] == last_run:
                posts_last[post_id] = row

        # Fallback when fetch_run_id missing: first/last post measurement by time that day.
        if not posts_first and not posts_last:
            p_first, p_last = _first_last_by_day(
                [r for r in post_meas_list if int(r["account_id"]) == account.account_id],
                day=day,
                id_key="post_id",
            )
            posts_first, posts_last = p_first, p_last

        shared = set(posts_first) & set(posts_last)
        only_start = len(posts_first) - len(shared)
        only_end = len(posts_last) - len(shared)
        likes_delta = 0
        comments_delta = 0
        likes_ok = False
        comments_ok = False
        engagement_incomplete = False
        for post_id in shared:
            a = posts_first[post_id]
            b = posts_last[post_id]
            if a["like_count"] is None or b["like_count"] is None:
                engagement_incomplete = True
            else:
                likes_delta += int(b["like_count"]) - int(a["like_count"])
                likes_ok = True
            if a["comments_count"] is None or b["comments_count"] is None:
                engagement_incomplete = True
            else:
                comments_delta += int(b["comments_count"]) - int(a["comments_count"])
                comments_ok = True

        published_count, published_coverage = resolve_published_posts_metric(
            account_id=account.account_id,
            start_measured_at=start_at,
            end_measured_at=end_at,
            posts=posts_list,
            period_fetch_complete=period_complete_by_account.get(account.account_id),
        )
        summaries.append(
            AccountDeltaSummary(
                account_id=account.account_id,
                display_name=_display_name(account),
                followers_start=f1,
                followers_end=f2,
                followers_delta=_delta(f1, f2),
                start_measured_at=start_at,
                end_measured_at=end_at,
                insufficient=f1 is None or f2 is None,
                missing_note=NO_SECOND_MEASUREMENT if (f1 is None or f2 is None) else None,
                likes_delta=likes_delta if likes_ok else None,
                comments_delta=comments_delta if comments_ok else None,
                comparable_posts=len(shared),
                posts_only_start=only_start,
                posts_only_end=only_end,
                engagement_incomplete=engagement_incomplete,
                published_posts_count=published_count,
                published_posts_coverage=published_coverage,
            )
        )
        if only_start or only_end:
            warnings.append(
                f"{_display_name(account)}: gedeeltelijke postdekking "
                f"({only_start} alleen eerste meting, {only_end} alleen laatste)."
            )

    return IntradayComparisonResult(day=day, account_rows=summaries, warnings=warnings)
