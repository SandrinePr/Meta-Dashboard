"""Follower growth series from stored measurements only (no Meta API)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from zoneinfo import ZoneInfo

from analytics.comparison import selected_accounts
from analytics.daily_comparison import _display_name, _parse_utc, amsterdam_day
from db.database import get_connection

AMSTERDAM = ZoneInfo("Europe/Amsterdam")

SeriesMode = Literal["daily", "intraday"]

# Fixed chart palette — one colour per account (by order).
ACCOUNT_COLORS = (
    "#E87722",
    "#2F5D50",
    "#3A4A6B",
    "#8B4513",
    "#5B7C99",
    "#C45C26",
    "#6B4F7A",
    "#4A6B3A",
    "#7A5C2E",
    "#2E5A7A",
)


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    measured_at: str
    label: str
    followers: int
    growth_abs: float
    growth_pct: float


@dataclass(frozen=True, slots=True)
class AccountFollowerSeries:
    account_id: int
    display_name: str
    points: list[SeriesPoint]
    color: str
    # Chart-eligible only when comparable begin + end exist in the chosen period.
    chartable: bool = False
    # Table messaging: missing begin on period start, or no second measurement.
    missing_start: bool = False
    insufficient: bool = False


@dataclass(frozen=True, slots=True)
class GrowthSeriesResult:
    mode: SeriesMode
    series: list[AccountFollowerSeries]
    notes: list[str] = field(default_factory=list)  # unused; kept for API stability


def _fmt_label(measured_at: str, *, with_time: bool) -> str:
    dt = _parse_utc(measured_at)
    if dt is None:
        return measured_at
    local = dt.astimezone(AMSTERDAM)
    if with_time:
        return local.strftime("%d-%m-%Y %H:%M")
    return local.strftime("%d-%m-%Y")


def _last_per_amsterdam_day(rows: list[Any]) -> dict[date, Any]:
    """Last measurement per Amsterdam calendar day (rows chronologically ordered)."""
    by_day: dict[date, Any] = {}
    for row in rows:
        day = amsterdam_day(row["measured_at"])
        if day is None:
            continue
        by_day[day] = row
    return by_day


def load_account_measurements(
    account_ids: list[int],
) -> dict[int, list[Any]]:
    if not account_ids:
        return {}
    placeholders = ",".join("?" for _ in account_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, account_id, measured_at, followers_count
            FROM account_measurements
            WHERE account_id IN ({placeholders})
              AND followers_count IS NOT NULL
            ORDER BY measured_at ASC, id ASC
            """,
            account_ids,
        ).fetchall()
    by_account: dict[int, list[Any]] = {aid: [] for aid in account_ids}
    for row in rows:
        by_account[int(row["account_id"])].append(row)
    return by_account


def _points_from_rows(
    rows: list[Any],
    *,
    with_time: bool,
) -> list[SeriesPoint]:
    if not rows:
        return []
    base = int(rows[0]["followers_count"])
    points: list[SeriesPoint] = []
    for row in rows:
        followers = int(row["followers_count"])
        growth_abs = float(followers - base)
        growth_pct = (growth_abs / base * 100.0) if base else 0.0
        measured_at = str(row["measured_at"])
        points.append(
            SeriesPoint(
                measured_at=measured_at,
                label=_fmt_label(measured_at, with_time=with_time),
                followers=followers,
                growth_abs=growth_abs,
                growth_pct=growth_pct,
            )
        )
    return points


def build_growth_series(
    *,
    own_account_id: int | None,
    competitor_usernames: list[str],
    mode: SeriesMode,
    day1: date | None = None,
    day2: date | None = None,
    intraday_day: date | None = None,
    metric: str | None = None,  # ignored; chart plots growth in counts
) -> GrowthSeriesResult:
    """Build follower-growth series for the chosen Growth period.

    Points carry both absolute and percentage growth from the period begin.
    Only accounts with a real begin and end measurement in the period are
    chartable. Missing begin is never replaced by a later day.
    """
    _ = metric
    accounts = selected_accounts(own_account_id, competitor_usernames)
    if not accounts:
        return GrowthSeriesResult(mode=mode, series=[])

    by_account = load_account_measurements([a.account_id for a in accounts])
    series: list[AccountFollowerSeries] = []
    with_time = mode == "intraday"

    for index, account in enumerate(accounts):
        color = ACCOUNT_COLORS[index % len(ACCOUNT_COLORS)]
        rows = by_account.get(account.account_id, [])
        name = _display_name(account)

        if mode == "daily":
            if day1 is None or day2 is None:
                continue
            start, end = (day1, day2) if day1 <= day2 else (day2, day1)
            by_day = _last_per_amsterdam_day(
                [
                    r
                    for r in rows
                    if (d := amsterdam_day(r["measured_at"])) is not None
                    and start <= d <= end
                ]
            )
            has_start = start in by_day
            has_end = end in by_day
            if not has_start and not has_end:
                series.append(
                    AccountFollowerSeries(
                        account_id=account.account_id,
                        display_name=name,
                        points=[],
                        color=color,
                        chartable=False,
                        insufficient=True,
                    )
                )
                continue
            if not has_start:
                # End exists (or mid points) but no measurement on period start day.
                series.append(
                    AccountFollowerSeries(
                        account_id=account.account_id,
                        display_name=name,
                        points=[],
                        color=color,
                        chartable=False,
                        missing_start=True,
                        insufficient=True,
                    )
                )
                continue
            if not has_end:
                series.append(
                    AccountFollowerSeries(
                        account_id=account.account_id,
                        display_name=name,
                        points=[],
                        color=color,
                        chartable=False,
                        insufficient=True,
                    )
                )
                continue
            # Comparable begin + end: include last measurement per day in [start, end]
            # that falls on days we actually measured — but always include start & end.
            ordered_days = sorted(d for d in by_day if start <= d <= end)
            chosen_rows = [by_day[d] for d in ordered_days]
            points = _points_from_rows(chosen_rows, with_time=False)
            series.append(
                AccountFollowerSeries(
                    account_id=account.account_id,
                    display_name=name,
                    points=points,
                    color=color,
                    chartable=len(points) >= 2,
                    insufficient=len(points) < 2,
                )
            )
            continue

        # Intraday: first vs last real moment on that day.
        if intraday_day is None:
            continue
        day_rows = [
            r for r in rows if amsterdam_day(r["measured_at"]) == intraday_day
        ]
        if len(day_rows) < 2:
            series.append(
                AccountFollowerSeries(
                    account_id=account.account_id,
                    display_name=name,
                    points=[],
                    color=color,
                    chartable=False,
                    insufficient=True,
                    missing_start=len(day_rows) == 0,
                )
            )
            continue
        # Distinct timestamps required.
        distinct = []
        seen_ts: set[str] = set()
        for row in day_rows:
            ts = str(row["measured_at"])
            if ts in seen_ts:
                continue
            seen_ts.add(ts)
            distinct.append(row)
        if len(distinct) < 2:
            series.append(
                AccountFollowerSeries(
                    account_id=account.account_id,
                    display_name=name,
                    points=[],
                    color=color,
                    chartable=False,
                    insufficient=True,
                )
            )
            continue
        points = _points_from_rows(distinct, with_time=True)
        series.append(
            AccountFollowerSeries(
                account_id=account.account_id,
                display_name=name,
                points=points,
                color=color,
                chartable=True,
            )
        )

    return GrowthSeriesResult(mode=mode, series=series)
