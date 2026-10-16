"""Refresh insights for every month that has posts, then rebuild seed DB."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analytics.performance import get_month_post_counts  # noqa: E402
from sync.engagement_refresh import refresh_month_insights  # noqa: E402


def main() -> None:
    counts = get_month_post_counts()
    months = sorted(counts.keys())
    for year, month in months:
        if counts[(year, month)] <= 0:
            continue
        print(f"Refreshing {year}-{month:02d} ({counts[(year, month)]} posts)…")
        stats = refresh_month_insights(
            year,
            month,
            platforms={"instagram", "facebook"},
            insights_only=True,
        )
        print(f"  updated={stats.updated} skipped={stats.skipped} failed={stats.failed}")
        if stats.token_expired:
            print("Token expired — stop.")
            break


if __name__ == "__main__":
    main()
