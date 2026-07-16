"""Meta API Inspector.

Validation-only tool to inspect Meta Graph API responses before any DB sync.
This script does not write to SQLite.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPTS_DIR.parent
for _path in (_SCRIPTS_DIR, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from meta_setup import (
    load_dotenv_values,
    mask_secret,
    print_env_status,
    require_env_file,
)

from meta.client import MetaClient, MetaClientError

logger = logging.getLogger(__name__)

HASHTAG_PATTERN = re.compile(r"#(\w+)", re.UNICODE)

REQUIRED_POST_FIELDS = (
    "id",
    "date",
    "text",
    "hashtags",
    "permalink",
    "media_type",
    "media_url",
    "thumbnail_url",
    "comments_count",
)

REQUIRED_COMMENT_FIELDS = (
    "id",
    "author",
    "text",
    "date",
)


@dataclass(slots=True)
class NormalizedPost:
    id: str
    date: str | None
    text: str | None
    hashtags: list[str]
    permalink: str | None
    media_type: str | None
    media_url: str | None
    thumbnail_url: str | None
    comments_count: int | None
    available_fields: list[str]
    missing_fields: list[str]
    raw: dict[str, Any]


@dataclass(slots=True)
class NormalizedComment:
    id: str
    author: str | None
    text: str | None
    date: str | None
    available_fields: list[str]
    missing_fields: list[str]
    raw: dict[str, Any]


def extract_hashtags(text: str | None) -> list[str]:
    """Extract hashtags from post text/caption."""
    if not text:
        return []
    return sorted({match.group(1) for match in HASHTAG_PATTERN.finditer(text)})


def _field_presence(payload: dict[str, Any], normalized: dict[str, Any]) -> tuple[list[str], list[str]]:
    available: list[str] = []
    missing: list[str] = []
    for field_name, value in normalized.items():
        if field_name in {"available_fields", "missing_fields", "raw"}:
            continue
        is_present = value not in (None, "", [])
        if is_present:
            available.append(field_name)
        else:
            missing.append(field_name)
    return available, missing


def normalize_instagram_post(payload: dict[str, Any]) -> NormalizedPost:
    """Map IG media payload to normalized inspector schema."""
    text = payload.get("caption")
    normalized = {
        "id": str(payload.get("id", "")),
        "date": payload.get("timestamp"),
        "text": text,
        "hashtags": extract_hashtags(text),
        "permalink": payload.get("permalink"),
        "media_type": payload.get("media_type"),
        "media_url": payload.get("media_url"),
        "thumbnail_url": payload.get("thumbnail_url"),
        "comments_count": payload.get("comments_count"),
    }
    available, missing = _field_presence(payload, normalized)
    return NormalizedPost(
        **normalized,
        available_fields=available,
        missing_fields=missing,
        raw=payload,
    )


def normalize_facebook_post(payload: dict[str, Any]) -> NormalizedPost:
    """Map FB post payload to normalized inspector schema."""
    text = payload.get("message")
    media_type: str | None = None
    media_url: str | None = None
    thumbnail_url: str | None = payload.get("full_picture")

    attachments = payload.get("attachments")
    if isinstance(attachments, dict):
        items = attachments.get("data")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                media_type = first.get("type")
                media = first.get("media")
                if isinstance(media, dict):
                    image = media.get("image")
                    if isinstance(image, dict):
                        media_url = image.get("src")
                media_url = media_url or first.get("url")

    normalized = {
        "id": str(payload.get("id", "")),
        "date": payload.get("created_time"),
        "text": text,
        "hashtags": extract_hashtags(text),
        "permalink": payload.get("permalink_url"),
        "media_type": media_type,
        "media_url": media_url,
        "thumbnail_url": thumbnail_url,
        "comments_count": payload.get("comments", {}).get("summary", {}).get("total_count")
        if isinstance(payload.get("comments"), dict)
        else None,
    }
    available, missing = _field_presence(payload, normalized)
    return NormalizedPost(
        **normalized,
        available_fields=available,
        missing_fields=missing,
        raw=payload,
    )


def normalize_instagram_comment(payload: dict[str, Any]) -> NormalizedComment:
    """Map IG comment payload to normalized inspector schema."""
    normalized = {
        "id": str(payload.get("id", "")),
        "author": payload.get("username"),
        "text": payload.get("text"),
        "date": payload.get("timestamp"),
    }
    available, missing = _field_presence(payload, normalized)
    return NormalizedComment(
        **normalized,
        available_fields=available,
        missing_fields=missing,
        raw=payload,
    )


def normalize_facebook_comment(payload: dict[str, Any]) -> NormalizedComment:
    """Map FB comment payload to normalized inspector schema."""
    author: str | None = None
    from_obj = payload.get("from")
    if isinstance(from_obj, dict):
        author = from_obj.get("name")
    normalized = {
        "id": str(payload.get("id", "")),
        "author": author,
        "text": payload.get("message"),
        "date": payload.get("created_time"),
    }
    available, missing = _field_presence(payload, normalized)
    return NormalizedComment(
        **normalized,
        available_fields=available,
        missing_fields=missing,
        raw=payload,
    )


def _print_header(title: str) -> None:
    print(f"\n{'=' * 88}\n{title}\n{'=' * 88}")


def _safe_truncate(value: str | None, limit: int = 120) -> str:
    if not value:
        return "-"
    return value if len(value) <= limit else f"{value[:limit - 3]}..."


def _print_post(platform: str, idx: int, post: NormalizedPost) -> None:
    print(f"\n[{platform} POST {idx}]")
    print(f"  ID            : {post.id or '-'}")
    print(f"  Datum         : {post.date or '-'}")
    print(f"  Tekst         : {_safe_truncate(post.text)}")
    print(f"  Hashtags      : {', '.join(post.hashtags) if post.hashtags else '-'}")
    print(f"  Permalink     : {post.permalink or '-'}")
    print(f"  Media type    : {post.media_type or '-'}")
    print(f"  Media URL     : {post.media_url or '-'}")
    print(f"  Thumbnail URL : {post.thumbnail_url or '-'}")
    print(
        f"  Comments cnt  : {post.comments_count if post.comments_count is not None else '-'}"
    )
    print(f"  Velden ok     : {', '.join(post.available_fields) if post.available_fields else '-'}")
    print(f"  Velden missen : {', '.join(post.missing_fields) if post.missing_fields else '-'}")


def _print_comment(platform: str, idx: int, comment: NormalizedComment) -> None:
    print(f"    - [{platform} COMMENT {idx}]")
    print(f"      ID            : {comment.id or '-'}")
    print(f"      Auteur        : {comment.author or '-'}")
    print(f"      Tekst         : {_safe_truncate(comment.text, limit=100)}")
    print(f"      Datum         : {comment.date or '-'}")
    print(
        f"      Velden ok     : {', '.join(comment.available_fields) if comment.available_fields else '-'}"
    )
    print(
        f"      Velden missen : {', '.join(comment.missing_fields) if comment.missing_fields else '-'}"
    )


def _as_utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def validate_configuration() -> tuple[dict[str, str], list[str]]:
    """Validate `.env` presence and required tokens for inspector checks."""
    require_env_file()
    values = load_dotenv_values()

    required_for_all = ("META_APP_ID", "META_USER_ACCESS_TOKEN", "META_PAGE_ID")
    required_for_content = ("META_PAGE_ACCESS_TOKEN", "META_INSTAGRAM_BUSINESS_ACCOUNT_ID")

    missing = print_env_status(values, required=required_for_all + required_for_content)

    print("\nTokenvereisten per test:")
    print(f"  - Pages test                 : META_USER_ACCESS_TOKEN [{mask_secret(values.get('META_USER_ACCESS_TOKEN'))}]")
    print(
        "  - Instagram media/comments   : META_PAGE_ACCESS_TOKEN + "
        f"META_INSTAGRAM_BUSINESS_ACCOUNT_ID [{mask_secret(values.get('META_PAGE_ACCESS_TOKEN'))}]"
    )
    print(
        "  - Facebook posts/comments    : META_PAGE_ACCESS_TOKEN "
        f"[{mask_secret(values.get('META_PAGE_ACCESS_TOKEN'))}]"
    )

    if missing:
        print("\nInspector kan niet starten zolang verplichte waarden ontbreken.")
        print("Setup-volgorde:")
        print("  1. Zet nieuwe User Token in META_USER_ACCESS_TOKEN (.env)")
        print("  2. python scripts/refresh_all_tokens.py")
        print("  3. python scripts/meta_api_inspector.py --limit 5 --export-json inspector-output.json")

    return values, missing


def inspect_api(
    client: MetaClient,
    *,
    limit: int,
    include_raw_in_stdout: bool,
    env_values: dict[str, str],
) -> dict[str, Any]:
    """Run full API inspector flow and return serializable report."""
    report: dict[str, Any] = {
        "generated_at_utc": _as_utc_now(),
        "summary": {},
        "pages": [],
        "instagram": {
            "business_account_id": None,
            "posts": [],
        },
        "facebook": {
            "posts": [],
        },
        "errors": [],
    }

    _print_header("META API INSPECTOR - CONNECTIVITY")
    print(f"Base URL: {client.base_url}")
    print(f"Configured Page ID: {env_values.get('META_PAGE_ID') or '-'}")
    print(
        "Configured IG Business Account ID: "
        f"{env_values.get('META_INSTAGRAM_BUSINESS_ACCOUNT_ID') or '-'}"
    )

    if not env_values.get("META_USER_ACCESS_TOKEN"):
        report["errors"].append("META_USER_ACCESS_TOKEN ontbreekt")
        raise MetaClientError("META_USER_ACCESS_TOKEN ontbreekt in .env")

    pages = client.get_pages()
    report["pages"] = pages
    print(f"Verbinding OK. Pages gevonden: {len(pages)}")
    if pages:
        for i, page in enumerate(pages, start=1):
            print(f"  [{i}] {page.get('name', '-') } (id={page.get('id', '-')})")

    ig_account_id = (
        env_values.get("META_INSTAGRAM_BUSINESS_ACCOUNT_ID")
        or client.get_instagram_business_account_id()
    )
    report["instagram"]["business_account_id"] = ig_account_id
    _print_header("INSTAGRAM BUSINESS ACCOUNT")
    print(f"Gekoppeld account ID: {ig_account_id or 'NIET GEVONDEN'}")

    _print_header("INSTAGRAM POSTS + COMMENTS")
    if not env_values.get("META_PAGE_ACCESS_TOKEN"):
        print("Overgeslagen: META_PAGE_ACCESS_TOKEN ontbreekt (vereist voor Instagram API calls).")
        report["errors"].append("META_PAGE_ACCESS_TOKEN ontbreekt voor Instagram test")
        ig_posts_raw = []
    elif not ig_account_id:
        print("Overgeslagen: META_INSTAGRAM_BUSINESS_ACCOUNT_ID ontbreekt en kon niet worden opgehaald.")
        report["errors"].append("Instagram business account ID ontbreekt")
        ig_posts_raw = []
    else:
        print("Instagram media test starten met Page Access Token...")
        ig_posts_raw = client.get_instagram_media(ig_account_id, limit=limit)
    ig_posts: list[dict[str, Any]] = []
    total_ig_comments = 0
    for idx, raw_post in enumerate(ig_posts_raw, start=1):
        normalized = normalize_instagram_post(raw_post)
        comments_raw = client.get_instagram_comments(normalized.id, limit=limit)
        comments = [normalize_instagram_comment(item) for item in comments_raw]
        total_ig_comments += len(comments)

        _print_post("IG", idx, normalized)
        print(f"  Comments opgehaald: {len(comments)}")
        for c_idx, comment in enumerate(comments, start=1):
            _print_comment("IG", c_idx, comment)

        serialized_post = asdict(normalized)
        serialized_post["comments"] = [asdict(comment) for comment in comments]
        ig_posts.append(serialized_post)

        if include_raw_in_stdout:
            print("  RAW POST JSON:")
            print(json.dumps(raw_post, indent=2, ensure_ascii=False))
            if comments_raw:
                print("  RAW COMMENTS JSON:")
                print(json.dumps(comments_raw, indent=2, ensure_ascii=False))

    report["instagram"]["posts"] = ig_posts

    _print_header("FACEBOOK POSTS + COMMENTS")
    if not env_values.get("META_PAGE_ACCESS_TOKEN"):
        print("Overgeslagen: META_PAGE_ACCESS_TOKEN ontbreekt (vereist voor Facebook API calls).")
        report["errors"].append("META_PAGE_ACCESS_TOKEN ontbreekt voor Facebook test")
        fb_posts_raw = []
    else:
        print("Facebook posts test starten met Page Access Token...")
        fb_posts_raw = client.get_facebook_page_posts(limit=limit)
    fb_posts: list[dict[str, Any]] = []
    total_fb_comments = 0
    for idx, raw_post in enumerate(fb_posts_raw, start=1):
        normalized = normalize_facebook_post(raw_post)
        comments_raw = client.get_facebook_comments(normalized.id, limit=limit)
        comments = [normalize_facebook_comment(item) for item in comments_raw]
        total_fb_comments += len(comments)

        _print_post("FB", idx, normalized)
        print(f"  Comments opgehaald: {len(comments)}")
        for c_idx, comment in enumerate(comments, start=1):
            _print_comment("FB", c_idx, comment)

        serialized_post = asdict(normalized)
        serialized_post["comments"] = [asdict(comment) for comment in comments]
        fb_posts.append(serialized_post)

        if include_raw_in_stdout:
            print("  RAW POST JSON:")
            print(json.dumps(raw_post, indent=2, ensure_ascii=False))
            if comments_raw:
                print("  RAW COMMENTS JSON:")
                print(json.dumps(comments_raw, indent=2, ensure_ascii=False))

    report["facebook"]["posts"] = fb_posts

    report["summary"] = {
        "pages_count": len(pages),
        "instagram_post_count": len(ig_posts),
        "instagram_comment_count": total_ig_comments,
        "facebook_post_count": len(fb_posts),
        "facebook_comment_count": total_fb_comments,
        "required_post_fields": list(REQUIRED_POST_FIELDS),
        "required_comment_fields": list(REQUIRED_COMMENT_FIELDS),
    }

    _print_header("SAMENVATTING")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print("\nInspector klaar. Er is niets naar SQLite geschreven.")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Meta API data availability without SQLite sync.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Aantal posts/comments per endpoint (standaard: 5).",
    )
    parser.add_argument(
        "--export-json",
        type=Path,
        default=None,
        help="Optioneel pad om volledige inspector-output als JSON op te slaan.",
    )
    parser.add_argument(
        "--show-raw",
        action="store_true",
        help="Toon ruwe JSON van posts/comments direct in console-output.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Toon debug logging.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    try:
        env_values, missing = validate_configuration()
        if missing:
            raise SystemExit(1)

        client = MetaClient.from_settings()
        report = inspect_api(
            client,
            limit=max(args.limit, 1),
            include_raw_in_stdout=args.show_raw,
            env_values=env_values,
        )
        if args.export_json is not None:
            _write_json(args.export_json, report)
            print(f"\nJSON-export opgeslagen op: {args.export_json}")
    except MetaClientError as exc:
        logger.exception("Meta inspector failed with known client error.")
        print(f"\nMeta API inspector fout: {exc}")
        raise SystemExit(1) from exc
    except Exception as exc:  # pragma: no cover - safety net for manual debugging
        logger.exception("Meta inspector failed unexpectedly.")
        print(f"\nOnverwachte inspector fout: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
