"""Sync CLI entrypoint."""

from __future__ import annotations

import argparse
import sys

from sync.orchestrator import get_sync_summary, run_sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync Meta content into SQLite.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Sync Instagram and Facebook.")
    group.add_argument(
        "--platform",
        choices=["instagram", "facebook"],
        help="Sync one platform.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Volledige sync: Insights + comments voor alle posts (veel trager).",
    )
    return parser


def _print_stats(stats) -> None:
    totals = get_sync_summary()
    print("\nSync voltooid.")
    print(f"  Instagram posts toegevoegd : {stats.instagram_posts_added}")
    print(f"  Instagram posts bijgewerkt : {stats.instagram_posts_updated}")
    print(f"  Instagram comments toegevoegd: {stats.instagram_comments_added}")
    print(f"  Instagram comments bijgewerkt: {stats.instagram_comments_updated}")
    print(f"  Facebook posts toegevoegd  : {stats.facebook_posts_added}")
    print(f"  Facebook posts bijgewerkt  : {stats.facebook_posts_updated}")
    print(f"  Facebook comments toegevoegd : {stats.facebook_comments_added}")
    print(f"  Facebook comments bijgewerkt : {stats.facebook_comments_updated}")
    print(f"  Insights bijgewerkt          : {stats.insights_ok}")
    print(f"  Insights mislukt             : {stats.insights_failed}")
    print("\nTotaal in database:")
    print(f"  Instagram posts   : {totals['instagram_posts']}")
    print(f"  Instagram comments: {totals['instagram_comments']}")
    print(f"  Facebook posts    : {totals['facebook_posts']}")
    print(f"  Facebook comments : {totals['facebook_comments']}")

    if stats.errors:
        print("\nErrors:")
        for error in stats.errors:
            print(f"  - {error}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    platform = "all" if args.all else args.platform

    try:
        stats = run_sync(platform, full=args.full)
        _print_stats(stats)
        if stats.errors:
            raise SystemExit(1)
    except Exception as exc:
        print(f"Sync mislukt: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
