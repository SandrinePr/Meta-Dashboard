"""Build a deployable seed SQLite DB from the local synced database.

Keeps only posts/comments from the last LOOKBACK_DAYS (~1.5 years) so Render
starts with searchable data without a long Meta sync.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOOKBACK_DAYS = 548
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "social_search.db"
SEED = ROOT / "data" / "seed_social_search.db"
TMP = ROOT / "data" / "seed_tmp.db"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build deployable seed DB for Render.")
    parser.add_argument(
        "--skip-thumbnails",
        action="store_true",
        help="Do not download missing thumbnails; zip existing media only.",
    )
    args = parser.parse_args()

    if not SRC.exists():
        raise SystemExit(f"Source DB not found: {SRC}")

    sys.path.insert(0, str(ROOT))
    from db.repository import rebuild_search_index

    if TMP.exists():
        TMP.unlink()
    shutil.copy2(SRC, TMP)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f"Building seed from {SRC}")
    print(f"Cutoff: {cutoff} ({LOOKBACK_DAYS} days) — skipped; seed mirrors full local DB")

    conn = sqlite3.connect(TMP)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    # Drop known local test fixtures that break image display.
    conn.execute(
        """
        DELETE FROM comments WHERE post_id IN (
            SELECT id FROM posts
            WHERE thumbnail_url LIKE '%example.com%'
               OR media_url LIKE '%example.com%'
               OR text IN ('Updated', 'Yumi', 'CULIMAAT special offer')
        )
        """
    )
    conn.execute(
        """
        DELETE FROM post_hashtags WHERE post_id IN (
            SELECT id FROM posts
            WHERE thumbnail_url LIKE '%example.com%'
               OR media_url LIKE '%example.com%'
               OR text IN ('Updated', 'Yumi', 'CULIMAAT special offer')
        )
        """
    )
    conn.execute(
        """
        DELETE FROM posts
        WHERE thumbnail_url LIKE '%example.com%'
           OR media_url LIKE '%example.com%'
           OR text IN ('Updated', 'Yumi', 'CULIMAAT special offer')
        """
    )
    conn.execute(
        "DELETE FROM hashtags WHERE id NOT IN (SELECT hashtag_id FROM post_hashtags)"
    )
    conn.execute("DELETE FROM search_index")
    rebuild_search_index(conn)
    conn.commit()

    posts = conn.execute(
        "SELECT platform, COUNT(*) AS c FROM posts GROUP BY platform"
    ).fetchall()
    comments = conn.execute(
        "SELECT platform, COUNT(*) AS c FROM comments GROUP BY platform"
    ).fetchall()
    print("posts:", [dict(r) for r in posts])
    print("comments:", [dict(r) for r in comments])
    conn.close()

    vacuum_conn = sqlite3.connect(TMP)
    vacuum_conn.execute("VACUUM")
    vacuum_conn.close()

    if SEED.exists():
        SEED.unlink()
    TMP.rename(SEED)
    print(f"Wrote {SEED} ({SEED.stat().st_size / 1024 / 1024:.2f} MB)")

    # Cache thumbnails next so Render can show images without live CDN URLs.
    import subprocess

    if args.skip_thumbnails:
        import zipfile

        media_dir = ROOT / "data" / "media"
        thumbs_dir = ROOT / "data" / "thumbnails"
        media_dir.mkdir(parents=True, exist_ok=True)
        if thumbs_dir.exists():
            for thumb in thumbs_dir.glob("*.jpg"):
                dest = media_dir / f"post_{thumb.stem}.jpg"
                if not dest.exists():
                    shutil.copy2(thumb, dest)
        seed_zip = ROOT / "data" / "seed_media.zip"
        if seed_zip.exists():
            seed_zip.unlink()
        files = sorted(media_dir.glob("post_*.jpg"))
        with zipfile.ZipFile(seed_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, arcname=path.name)
        print(
            f"Wrote {seed_zip} "
            f"({seed_zip.stat().st_size / 1024 / 1024:.2f} MB, {len(files)} files)"
        )
    else:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "cache_thumbnails.py")],
            check=True,
        )

    version_path = ROOT / "data" / "seed_version.txt"
    version_path.write_text(
        datetime.now(timezone.utc).isoformat(),
        encoding="utf-8",
    )
    print(f"Wrote {version_path}")


if __name__ == "__main__":
    main()
