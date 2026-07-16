"""Manual smoke checks for MetaClient using local .env settings."""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from meta.client import MetaClient, MetaClientError


def _print_preview(label: str, items: list[dict[str, Any]], preview_count: int = 2) -> None:
    print(f"\n{label}: {len(items)} item(s)")
    for item in items[:preview_count]:
        print(json.dumps(item, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Meta API client against .env config.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging for request diagnostics.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    client = MetaClient.from_settings()

    try:
        pages = client.get_pages()
        _print_preview("Pages", pages)

        ig_account_id = client.get_instagram_business_account_id()
        print(f"\nLinked Instagram Business Account ID: {ig_account_id}")

        if ig_account_id:
            media = client.get_instagram_media(ig_account_id, limit=5)
            _print_preview("Instagram media", media)
            if media:
                media_id = media[0].get("id")
                if media_id:
                    comments = client.get_instagram_comments(str(media_id), limit=5)
                    _print_preview("Instagram comments for first media item", comments)

        posts = client.get_facebook_page_posts(limit=5)
        _print_preview("Facebook page posts", posts)
        if posts:
            post_id = posts[0].get("id")
            if post_id:
                comments = client.get_facebook_comments(str(post_id), limit=5)
                _print_preview("Facebook comments for first post", comments)

        print("\nMeta client smoke check completed.")
    except MetaClientError as exc:
        print(f"\nMeta client error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
