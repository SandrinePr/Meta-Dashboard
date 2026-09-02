"""Monthly top-performing posts by engagement metric."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from db.database import get_connection
from meta.availability import is_post_unavailable
from ui.media import extract_post_stats

METRIC_KEYS = ("views", "likes", "comments", "saves", "shares")

METRIC_LABELS: dict[str, str] = {
    "views": "Weergaven",
    "likes": "Likes",
    "comments": "Reacties",
    "saves": "Opgeslagen",
    "shares": "Gedeeld",
}

DUTCH_MONTHS = (
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
)


@dataclass(slots=True, frozen=True)
class TopPost:
    """A post ranked on one engagement metric."""

    post_id: int
    platform: str
    text: str | None
    permalink: str | None
    thumbnail_url: str | None
    published_at: str
    content_type: str
    stats: dict[str, int]
    metric: str
    metric_value: int


def format_month_label(year: int, month: int) -> str:
    """Return a Dutch month label such as ``augustus 2026``."""
    name = DUTCH_MONTHS[month - 1]
    return f"{name} {year}"


def parse_month_key(key: str) -> tuple[int, int]:
    """Parse ``YYYY-MM`` into ``(year, month)``."""
    year_str, month_str = key.split("-", 1)
    return int(year_str), int(month_str)


def _months_between(newest: tuple[int, int], oldest: tuple[int, int]) -> list[tuple[int, int]]:
    """Return inclusive month range, newest first."""
    y, m = newest
    end_y, end_m = oldest
    months: list[tuple[int, int]] = []
    while (y, m) >= (end_y, end_m):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


def get_available_months(*, today: date | None = None) -> list[tuple[int, int]]:
    """Return selectable calendar months from first post month through today."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                MIN(strftime('%Y-%m', published_at)) AS min_ym,
                MAX(strftime('%Y-%m', published_at)) AS max_ym
            FROM posts
            WHERE published_at IS NOT NULL AND published_at != ''
            """
        ).fetchone()

    current_date = today or date.today()
    current = (current_date.year, current_date.month)
    if not row or not row["min_ym"]:
        return [current]

    earliest = parse_month_key(row["min_ym"])
    latest_db = parse_month_key(row["max_ym"]) if row["max_ym"] else earliest
    latest = max(latest_db, current)
    return _months_between(latest, earliest)


def _fetch_posts_for_month(
    year: int,
    month: int,
    *,
    platforms: set[str] | None = None,
) -> list[dict]:
    ym = f"{year:04d}-{month:02d}"
    query = """
        SELECT
            id,
            platform,
            text,
            permalink,
            thumbnail_url,
            published_at,
            content_type,
            raw_json
        FROM posts
        WHERE strftime('%Y-%m', published_at) = ?
    """
    params: list[object] = [ym]
    if platforms:
        placeholders = ", ".join("?" for _ in platforms)
        query += f" AND platform IN ({placeholders})"
        params.extend(sorted(platforms))

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    posts: list[dict] = []
    for row in rows:
        if is_post_unavailable(row["raw_json"]):
            continue
        posts.append(dict(row))
    return posts


def get_monthly_top_posts(
    year: int,
    month: int,
    *,
    platforms: set[str] | None = None,
    limit: int = 3,
) -> dict[str, list[TopPost]]:
    """Return top ``limit`` posts per metric for the selected calendar month."""
    posts = _fetch_posts_for_month(year, month, platforms=platforms)
    ranked: dict[str, list[TopPost]] = {key: [] for key in METRIC_KEYS}

    for metric in METRIC_KEYS:
        candidates: list[TopPost] = []
        for row in posts:
            if metric == "saves" and row["platform"] == "facebook":
                continue
            stats = extract_post_stats(row["platform"], row["raw_json"])
            value = stats.get(metric)
            if value is None:
                continue
            candidates.append(
                TopPost(
                    post_id=row["id"],
                    platform=row["platform"],
                    text=row["text"],
                    permalink=row["permalink"],
                    thumbnail_url=row["thumbnail_url"],
                    published_at=row["published_at"],
                    content_type=row["content_type"],
                    stats=stats,
                    metric=metric,
                    metric_value=value,
                )
            )
        candidates.sort(
            key=lambda item: (-item.metric_value, item.published_at),
        )
        ranked[metric] = candidates[:limit]

    return ranked


def count_posts_for_month(
    year: int,
    month: int,
    *,
    platforms: set[str] | None = None,
) -> int:
    """Return number of available posts published in the given month."""
    return len(_fetch_posts_for_month(year, month, platforms=platforms))
