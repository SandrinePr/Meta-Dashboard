"""Download post thumbnails into data/media for offline/Render-safe display."""

from __future__ import annotations

import io
import sqlite3
import sys
import zipfile
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEED_DB = ROOT / "data" / "seed_social_search.db"
RUNTIME_DB = ROOT / "data" / "social_search.db"
MEDIA_DIR = ROOT / "data" / "media"
SEED_MEDIA_ZIP = ROOT / "data" / "seed_media.zip"
MAX_SIDE = 480
JPEG_QUALITY = 72

from ui.media import _is_displayable_image_url, resolve_display_image_url  # noqa: E402


def _pick_url(row: sqlite3.Row) -> str | None:
    url, _ = resolve_display_image_url(
        thumbnail_url=row["thumbnail_url"],
        media_url=row["media_url"],
        media_type=row["media_type"],
        raw_json=row["raw_json"],
        prefer_thumbnail=True,
    )
    return url if _is_displayable_image_url(url) else None


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


def cache_thumbnails(db_path: Path, *, force: bool = False) -> tuple[int, int, int]:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if force:
        for path in MEDIA_DIR.glob("post_*.*"):
            path.unlink(missing_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, platform, thumbnail_url, media_url, media_type, raw_json
        FROM posts
        ORDER BY id
        """
    ).fetchall()
    conn.close()

    ok = skipped = failed = 0
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MetaDashboard/1.0)",
        "Accept": "image/*,*/*;q=0.8",
    }

    for idx, row in enumerate(rows, start=1):
        target = MEDIA_DIR / f"post_{row['id']}.jpg"
        if target.exists() and not force:
            skipped += 1
            continue
        url = _pick_url(row)
        if not url:
            skipped += 1
            continue
        try:
            response = session.get(url, timeout=20, headers=headers)
            if response.status_code != 200 or not response.content:
                failed += 1
                continue
            if _save_compressed_jpeg(response.content, target):
                ok += 1
            else:
                failed += 1
        except requests.RequestException:
            failed += 1

        if idx % 50 == 0 or idx == len(rows):
            print(
                f"media cache: {idx}/{len(rows)} saved={ok} skipped={skipped} failed={failed}",
                flush=True,
            )

    return ok, skipped, failed


def zip_media() -> None:
    if SEED_MEDIA_ZIP.exists():
        SEED_MEDIA_ZIP.unlink()
    with zipfile.ZipFile(SEED_MEDIA_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(MEDIA_DIR.glob("post_*.jpg")):
            zf.write(path, arcname=path.name)
    print(
        f"Wrote {SEED_MEDIA_ZIP} "
        f"({SEED_MEDIA_ZIP.stat().st_size / 1024 / 1024:.2f} MB, "
        f"{len(list(MEDIA_DIR.glob('post_*.jpg')))} files)",
        flush=True,
    )


def main() -> None:
    db_path = SEED_DB if SEED_DB.exists() else RUNTIME_DB
    if not db_path.exists():
        raise SystemExit(f"No database found at {db_path}")
    print(f"Caching thumbnails from {db_path}", flush=True)
    ok, skipped, failed = cache_thumbnails(db_path, force=True)
    print(f"Done: saved={ok} skipped={skipped} failed={failed}", flush=True)
    zip_media()


if __name__ == "__main__":
    main()
