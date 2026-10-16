"""Quick check: DB vs Meta API for recent months."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from config import get_settings
from meta.client import MetaClient
from sync.mappers import parse_published_at


def db_stats() -> None:
    db = Path("data/social_search.db")
    if not db.exists():
        print("DB: missing")
        return
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT MAX(published_at), COUNT(*) FROM posts")
    print("DB max published_at, count:", cur.fetchone())
    cur.execute(
        "SELECT substr(published_at, 1, 7) AS m, COUNT(*) "
        "FROM posts GROUP BY m ORDER BY m DESC LIMIT 8"
    )
    print("DB recent months:", cur.fetchall())
    conn.close()


def meta_stats() -> None:
    settings = get_settings()
    if not settings.meta_page_access_token:
        print("Meta: no page token in .env")
        return
    client = MetaClient.from_settings()
    ig_id = settings.meta_instagram_business_account_id
    if not ig_id:
        print("Meta: no instagram business account id")
        return
    since = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
    items = client.get_instagram_media(ig_id, since=since)
    aug = [
        i
        for i in items
        if (parse_published_at(i.get("timestamp")) or "").startswith("2026-08")
    ]
    sep = [
        i
        for i in items
        if (parse_published_at(i.get("timestamp")) or "").startswith("2026-09")
    ]
    print(f"Meta IG since Jul 1: total={len(items)} aug={len(aug)} sep={len(sep)}")
    if aug:
        print("  sample aug:", parse_published_at(aug[0].get("timestamp")))
    if sep:
        print("  sample sep:", parse_published_at(sep[0].get("timestamp")))


if __name__ == "__main__":
    db_stats()
    meta_stats()
