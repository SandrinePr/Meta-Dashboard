"""Download and cache post thumbnails for Streamlit (Render-safe local files).

Meta CDN URLs expire and often 403 from datacenter IPs. Live display must use
files in ``data/media``. On cache miss we refresh URLs via Graph and download.
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

from db.database import get_connection
from db.repository import merge_post_raw_json
from meta.client import MetaClient, MetaClientError
from meta.endpoints import FACEBOOK_POST_FIELDS, INSTAGRAM_MEDIA_FIELDS, as_fields_param
from sync.mappers import normalize_facebook_post, normalize_instagram_post
from ui.media import MEDIA_DIR, _is_displayable_image_url, local_media_path, resolve_display_image_url

logger = logging.getLogger(__name__)

MAX_SIDE = 480
JPEG_QUALITY = 72
FAIL_COOLDOWN_SECONDS = 600
_DOWNLOAD_TIMEOUT = 20

_meta_client_lock = threading.Lock()
_meta_client: MetaClient | None = None
_meta_client_failed = False
_in_progress: set[int] = set()
_in_progress_lock = threading.Lock()
_failed_until: dict[int, float] = {}
_session_local = threading.local()


def _http_session() -> requests.Session:
    session = getattr(_session_local, "session", None)
    if session is None:
        session = requests.Session()
        _session_local.session = session
    return session


def _get_meta_client() -> MetaClient | None:
    global _meta_client, _meta_client_failed
    if _meta_client_failed:
        return None
    if _meta_client is not None:
        return _meta_client
    with _meta_client_lock:
        if _meta_client is not None or _meta_client_failed:
            return _meta_client
        try:
            _meta_client = MetaClient.from_settings()
        except Exception as exc:
            logger.warning("Graph thumbnail refresh unavailable: %s", exc)
            _meta_client_failed = True
            return None
    return _meta_client


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


def save_compressed_jpeg(content: bytes, target: Path) -> bool:
    try:
        image = Image.open(io.BytesIO(content))
        image = image.convert("RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return target.exists() and target.stat().st_size > 0
    except Exception:
        return False


def download_image_bytes(url: str, *, session: requests.Session | None = None) -> bytes | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.instagram.com/",
    }
    client = session or _http_session()
    try:
        response = client.get(url, timeout=_DOWNLOAD_TIMEOUT, headers=headers)
    except requests.RequestException:
        return None
    if response.status_code != 200 or not response.content:
        return None
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "image" not in content_type and not url.lower().split("?")[0].endswith(
        (".jpg", ".jpeg", ".png", ".webp")
    ):
        if len(response.content) < 100:
            return None
    return response.content


def refresh_media_urls(
    client: MetaClient,
    *,
    platform: str,
    external_id: str,
) -> tuple[str | None, str | None, dict | None]:
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


def persist_refreshed_urls(
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


def _allows_graph_refresh(row: sqlite3.Row) -> bool:
    """Page-token Graph refresh only works for our own Instagram/Facebook media."""
    role = ""
    try:
        role = str(row["comparison_role"] or "").strip().lower()
    except (KeyError, IndexError):
        role = ""
    return role != "competitor"


def _cache_row(row: sqlite3.Row, *, client: MetaClient | None) -> Path | None:
    post_id = int(row["id"])
    existing = local_media_path(post_id)
    if existing is not None:
        return existing

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    target = MEDIA_DIR / f"post_{post_id}.jpg"
    session = _http_session()
    content: bytes | None = None
    for url in _candidate_urls(row):
        content = download_image_bytes(url, session=session)
        if content:
            break

    graph_client = client if client is not None and _allows_graph_refresh(row) else None
    if not content and graph_client is not None:
        thumb, media, fresh = refresh_media_urls(
            graph_client,
            platform=row["platform"],
            external_id=str(row["external_id"] or ""),
        )
        for url in (thumb, media):
            if url and _is_displayable_image_url(url):
                content = download_image_bytes(url, session=session)
                if content:
                    with get_connection() as conn:
                        persist_refreshed_urls(
                            conn,
                            post_id=post_id,
                            existing_raw=row["raw_json"],
                            thumbnail_url=thumb,
                            media_url=media,
                            fresh_payload=fresh or {},
                        )
                        conn.commit()
                    break

    if content and save_compressed_jpeg(content, target):
        _failed_until.pop(post_id, None)
        return target
    _failed_until[post_id] = time.monotonic() + FAIL_COOLDOWN_SECONDS
    return None


def ensure_post_thumbnail(post_id: int) -> Path | None:
    """Return a local thumbnail path, downloading via Graph/CDN on miss."""
    existing = local_media_path(post_id)
    if existing is not None:
        return existing

    now = time.monotonic()
    blocked_until = _failed_until.get(post_id, 0.0)
    if blocked_until > now:
        return None

    with _in_progress_lock:
        if post_id in _in_progress:
            return None
        _in_progress.add(post_id)
    try:
        existing = local_media_path(post_id)
        if existing is not None:
            return existing
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT p.id, p.platform, p.external_id, p.thumbnail_url, p.media_url,
                       p.media_type, p.raw_json, a.comparison_role
                FROM posts p
                LEFT JOIN accounts a ON a.id = p.account_id
                WHERE p.id = ?
                """,
                (post_id,),
            ).fetchone()
        if row is None:
            return None
        return _cache_row(row, client=_get_meta_client())
    except Exception:
        logger.exception("Thumbnail cache failed for post %s", post_id)
        _failed_until[post_id] = time.monotonic() + FAIL_COOLDOWN_SECONDS
        return None
    finally:
        with _in_progress_lock:
            _in_progress.discard(post_id)


def ensure_posts_thumbnails(
    post_ids: list[int],
    *,
    max_workers: int = 4,
) -> int:
    """Cache missing thumbnails for the given post ids. Returns files saved."""
    missing: list[int] = []
    for post_id in post_ids:
        if post_id and local_media_path(post_id) is None:
            missing.append(int(post_id))
    if not missing:
        return 0
    saved = 0
    workers = max(1, min(max_workers, len(missing)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(ensure_post_thumbnail, post_id) for post_id in missing]
        for future in as_completed(futures):
            try:
                if future.result() is not None:
                    saved += 1
            except Exception:
                logger.exception("Parallel thumbnail cache failed")
    return saved


def cache_missing_thumbnails(
    *,
    recent_first: bool = True,
    limit: int | None = None,
) -> tuple[int, int, int]:
    """Cache thumbnails for posts that have no local file yet."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    recency = "p.id DESC" if recent_first else "p.id ASC"
    sql = f"""
        SELECT p.id, p.platform, p.external_id, p.thumbnail_url, p.media_url,
               p.media_type, p.raw_json, a.comparison_role
        FROM posts p
        LEFT JOIN accounts a ON a.id = p.account_id
        ORDER BY CASE WHEN a.comparison_role = 'competitor' THEN 1 ELSE 0 END,
                 {recency}
    """
    with get_connection() as conn:
        rows = conn.execute(sql).fetchall()

    client = _get_meta_client()
    ok = skipped = failed = 0
    processed = 0
    for row in rows:
        post_id = int(row["id"])
        if local_media_path(post_id) is not None:
            skipped += 1
            continue
        if limit is not None and processed >= limit:
            break
        processed += 1
        path = _cache_row(row, client=client)
        if path is not None:
            ok += 1
        else:
            failed += 1
        if processed % 25 == 0:
            logger.info(
                "thumbnail cache: saved=%s skipped=%s failed=%s",
                ok,
                skipped,
                failed,
            )
    return ok, skipped, failed
