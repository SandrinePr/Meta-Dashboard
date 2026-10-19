"""Download post thumbnails into data/media for offline/Render-safe display.

When CDN URLs are expired (common for Facebook), refresh image URLs via the
Meta Graph API with the page token, then download and cache locally.
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

SEED_DB = ROOT / "data" / "seed_social_search.db"
RUNTIME_DB = ROOT / "data" / "social_search.db"
MEDIA_DIR = ROOT / "data" / "media"
SEED_MEDIA_ZIP = ROOT / "data" / "seed_media.zip"


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
        help="Database path (default: runtime DB, else seed DB).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cache at most this many missing thumbnails (newest first).",
    )
    parser.add_argument(
        "--oldest-first",
        action="store_true",
        help="Process oldest posts first instead of newest.",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Do not rebuild seed_media.zip after caching.",
    )
    args = parser.parse_args()

    db_path = args.db or (RUNTIME_DB if RUNTIME_DB.exists() else SEED_DB)
    if not db_path.exists():
        raise SystemExit(f"No database found at {db_path}")
    os.environ["DATABASE_PATH"] = str(db_path.resolve())

    from sync.thumbnails import cache_missing_thumbnails
    from ui.media import MEDIA_DIR as runtime_media_dir

    if args.force:
        runtime_media_dir.mkdir(parents=True, exist_ok=True)
        for path in runtime_media_dir.glob("post_*.*"):
            path.unlink(missing_ok=True)

    print(f"Caching thumbnails from {db_path}", flush=True)
    ok, skipped, failed = cache_missing_thumbnails(
        recent_first=not args.oldest_first,
        limit=args.limit,
    )
    print(f"Done: saved={ok} skipped={skipped} failed={failed}", flush=True)
    if not args.no_zip:
        zip_media()


if __name__ == "__main__":
    main()
