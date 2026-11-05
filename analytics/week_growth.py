"""Week-to-week follower growth from stored measurements only.

Missing weeks stay missing. Nothing here invents a follower count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

AMSTERDAM = ZoneInfo("Europe/Amsterdam")

INSUFFICIENT_GROWTH = "Nog onvoldoende meetgegevens"

GrowthKind = Literal["insufficient", "week", "gap"]


@dataclass(frozen=True, slots=True)
class WeekMeasurement:
    measured_at: str
    followers: int
    week_start: str | None = None


@dataclass(frozen=True, slots=True)
class WeekGrowth:
    kind: GrowthKind
    absolute: int | None
    percent: float | None
    previous_at: str | None
    current_at: str | None
    period_days: int | None
    label: str


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


def _period_days(start: datetime, end: datetime) -> int:
    start_day = start.astimezone(AMSTERDAM).date()
    end_day = end.astimezone(AMSTERDAM).date()
    return (end_day - start_day).days


def _fmt_local(value: str) -> str:
    parsed = _parse(value)
    if parsed is None:
        return value
    return parsed.astimezone(AMSTERDAM).strftime("%d-%m-%Y %H:%M")


def _period_phrase(days: int) -> str:
    if days == 1:
        return "1 dag"
    return f"{days} dagen"


def _number(value: int) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,}".replace(",", ".")


def _percent(value: float | None) -> str:
    if value is None:
        return "n.b."
    sign = "+" if value > 0 else ""
    text = f"{value:.1f}".replace(".", ",")
    return f"{sign}{text}%" if value > 0 else f"{text}%"


def describe_week_growth(
    points: list[WeekMeasurement],
    *,
    expected_weeks: list[str] | None = None,
) -> WeekGrowth:
    """Compare the latest two real follower measurements.

    ``week_start`` is the Friday (YYYY-MM-DD) of a weekly cycle. A comparison
    is a normal week only when those Fridays are seven days apart and no
    expected cycle between them lacks a measurement. Otherwise the numbers,
    when shown, carry the actual elapsed period.
    """
    valid = [point for point in points if point.followers is not None]
    valid.sort(key=lambda point: (_parse(point.measured_at) or datetime.min.replace(tzinfo=timezone.utc), point.measured_at))
    if len(valid) < 2:
        return WeekGrowth(
            kind="insufficient",
            absolute=None,
            percent=None,
            previous_at=valid[0].measured_at if valid else None,
            current_at=valid[0].measured_at if valid else None,
            period_days=None,
            label=INSUFFICIENT_GROWTH,
        )

    previous = valid[-2]
    current = valid[-1]
    start = _parse(previous.measured_at)
    end = _parse(current.measured_at)
    if start is None or end is None or end <= start:
        return WeekGrowth(
            kind="insufficient",
            absolute=None,
            percent=None,
            previous_at=previous.measured_at,
            current_at=current.measured_at,
            period_days=None,
            label=INSUFFICIENT_GROWTH,
        )

    absolute = int(current.followers) - int(previous.followers)
    percent = None
    if previous.followers:
        percent = absolute / int(previous.followers) * 100.0
    days = _period_days(start, end)
    numbers = _number(absolute)
    if percent is None:
        numbers = f"{numbers} (percentage n.b.)"
    else:
        numbers = f"{numbers} ({_percent(percent)})"
    period = f"{_fmt_local(previous.measured_at)} → {_fmt_local(current.measured_at)} ({_period_phrase(days)})"

    adjacent = False
    if previous.week_start and current.week_start:
        prev_day = _parse(previous.week_start + "T00:00:00+00:00")
        curr_day = _parse(current.week_start + "T00:00:00+00:00")
        if prev_day and curr_day and (curr_day.date() - prev_day.date()).days == 7:
            between = [
                week
                for week in (expected_weeks or [])
                if previous.week_start < week < current.week_start
            ]
            measured_weeks = {
                point.week_start
                for point in valid
                if point.week_start
            }
            adjacent = not any(week not in measured_weeks for week in between)

    if adjacent:
        return WeekGrowth(
            kind="week",
            absolute=absolute,
            percent=percent,
            previous_at=previous.measured_at,
            current_at=current.measured_at,
            period_days=days,
            label=f"{numbers} · {period}",
        )
    return WeekGrowth(
        kind="gap",
        absolute=absolute,
        percent=percent,
        previous_at=previous.measured_at,
        current_at=current.measured_at,
        period_days=days,
        label=f"Datagat · {numbers} · {period}",
    )
