"""Download post thumbnails into data/media for offline/Render-safe display.

When CDN URLs are expired (common for Facebook), refresh image URLs via the
Meta Graph API with the page token, then download and cache locally.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

SEED_DB = ROOT / "data" / "seed_social_search.db"
RUNTIME_DB = ROOT / "data" / "social_search.db"
MEDIA_DIR = ROOT / "data" / "media"
SEED_MEDIA_ZIP = ROOT / "data" / "seed_media.zip"
MAX_SIDE = 480
JPEG_QUALITY = 72

from db.repository import merge_post_raw_json  # noqa: E402
from meta.client import MetaClient, MetaClientError  # noqa: E402
from meta.endpoints import (  # noqa: E402
    FACEBOOK_POST_FIELDS,
    INSTAGRAM_MEDIA_FIELDS,
    as_fields_param,
)
from sync.mappers import normalize_facebook_post, normalize_instagram_post  # noqa: E402
from ui.media import _is_displayable_image_url, resolve_display_image_url  # noqa: E402


def _candidate_urls(row: sqlite3.Row) -> list[str]:
    urls: list[str] = []
    primary, _ = resolve_display_image_url(
        thumbnail_url=row["thumbnail_url"],
        media_url=row["media_url"],
        media_type=row["media_type"],
        raw_json=row["raw_json"],
        prefer_thumbnail=True,
    )
    if primary and _is_displayable_image_url(primary):
        urls.append(primary)
    for key in ("thumbnail_url", "media_url"):
        value = row[key]
        if value and _is_displayable_image_url(value) and value not in urls:
            urls.append(value)
    return urls


def _save_compressed_jpeg(content: bytes, target: Path) -> bool:
    try:
        image = Image.open(io.BytesIO(content))
        image = image.convert("RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return target.exists() and target.stat().st_size > 0
    except Exception:
        return False


def _download_image(session: requests.Session, url: str) -> bytes | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.facebook.com/",
    }
    try:
        response = session.get(url, timeout=25, headers=headers)
    except requests.RequestException:
        return None
    if response.status_code != 200 or not response.content:
        return None
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "image" not in content_type and not url.lower().split("?")[0].endswith(
        (".jpg", ".jpeg", ".png", ".webp")
    ):
        # Some CDN responses omit content-type; still try decode via Pillow later.
        if len(response.content) < 100:
            return None
    return response.content


def _refresh_media_urls(
    client: MetaClient,
    *,
    platform: str,
    external_id: str,
) -> tuple[str | None, str | None, dict | None]:
    """Return (thumbnail_url, media_url, fresh_payload) from Graph API."""
    if not external_id:
        return None, None, None
    try:
        if platform == "facebook":
            fresh = client.get_json(
                external_id,
                params={"fields": as_fields_param(FACEBOOK_POST_FIELDS)},
            )
            normalized = normalize_facebook_post(fresh)
        else:
            fresh = client.get_json(
                external_id,
                params={"fields": as_fields_param(INSTAGRAM_MEDIA_FIELDS)},
            )
            normalized = normalize_instagram_post(fresh)
    except MetaClientError:
        return None, None, None
    return normalized.get("thumbnail_url"), normalized.get("media_url"), fresh


def _persist_refreshed_urls(
    conn: sqlite3.Connection,
    *,
    post_id: int,
    existing_raw: str | None,
    thumbnail_url: str | None,
    media_url: str | None,
    fresh_payload: dict,
) -> None:
    merged_raw = merge_post_raw_json(
        existing_raw,
        json.dumps(fresh_payload, ensure_ascii=False),
    )
    conn.execute(
        """
        UPDATE posts
        SET thumbnail_url = COALESCE(?, thumbnail_url),
            media_url = COALESCE(?, media_url),
            raw_json = COALESCE(?, raw_json)
        WHERE id = ?
        """,
        (thumbnail_url, media_url, merged_raw, post_id),
    )
    conn.execute(
        """
        UPDATE search_index
        SET thumbnail_url = COALESCE(?, thumbnail_url)
        WHERE entity_type = 'post' AND entity_id = ?
        """,
        (thumbnail_url, post_id),
    )


def cache_thumbnails(
    db_path: Path,
    *,
    force: bool = False,
    missing_only: bool = True,
) -> tuple[int, int, int]:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if force:
        for path in MEDIA_DIR.glob("post_*.*"):
            path.unlink(missing_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, platform, external_id, thumbnail_url, media_url, media_type, raw_json
        FROM posts
        ORDER BY id
        """
    ).fetchall()

    ok = skipped = failed = 0
    session = requests.Session()
    client: MetaClient | None = None
    try:
        client = MetaClient.from_settings()
    except Exception as exc:
        print(f"Graph refresh unavailable ({exc}); CDN-only mode.", flush=True)

    for idx, row in enumerate(rows, start=1):
        target = MEDIA_DIR / f"post_{row['id']}.jpg"
        if target.exists() and not force and missing_only:
            skipped += 1
            continue

        urls = _candidate_urls(row)
        content: bytes | None = None
        for url in urls:
            content = _download_image(session, url)
            if content:
                break

        refreshed = False
        if not content and client is not None:
            thumb, media, fresh = _refresh_media_urls(
                client,
                platform=row["platform"],
                external_id=str(row["external_id"] or ""),
            )
            for url in (thumb, media):
                if url and _is_displayable_image_url(url):
                    content = _download_image(session, url)
                    if content:
                        refreshed = True
                        _persist_refreshed_urls(
                            conn,
                            post_id=int(row["id"]),
                            existing_raw=row["raw_json"],
                            thumbnail_url=thumb,
                            media_url=media,
                            fresh_payload=fresh or {},
                        )
                        break

        if content and _save_compressed_jpeg(content, target):
            ok += 1
        elif not urls and not refreshed:
            skipped += 1
        else:
            failed += 1

        if idx % 50 == 0 or idx == len(rows):
            print(
                f"media cache: {idx}/{len(rows)} saved={ok} skipped={skipped} "
                f"failed={failed}",
                flush=True,
            )

    conn.commit()
    conn.close()
    return ok, skipped, failed


def zip_media() -> None:
    if SEED_MEDIA_ZIP.exists():
        SEED_MEDIA_ZIP.unlink()
    files = sorted(MEDIA_DIR.glob("post_*.jpg"))
    with zipfile.ZipFile(SEED_MEDIA_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=path.name)
    print(
        f"Wrote {SEED_MEDIA_ZIP} "
        f"({SEED_MEDIA_ZIP.stat().st_size / 1024 / 1024:.2f} MB, {len(files)} files)",
        flush=True,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Cache post thumbnails for Render.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download every thumbnail (slow).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Database path (default: seed DB, else runtime DB).",
    )
    args = parser.parse_args()

    db_path = args.db or (SEED_DB if SEED_DB.exists() else RUNTIME_DB)
    if not db_path.exists():
        raise SystemExit(f"No database found at {db_path}")
    print(f"Caching thumbnails from {db_path}", flush=True)
    ok, skipped, failed = cache_thumbnails(
        db_path,
        force=args.force,
        missing_only=not args.force,
    )
    print(f"Done: saved={ok} skipped={skipped} failed={failed}", flush=True)
    zip_media()


if __name__ == "__main__":
    main()
