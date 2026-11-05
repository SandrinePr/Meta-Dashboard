"""Stored posts keep their content and lose access tokens."""

from __future__ import annotations

from pathlib import Path

from db.database import get_connection, initialize_database
from db.repository import upsert_account, upsert_post
from meta.redact import redact_text


def test_upsert_drops_token_and_keeps_the_url(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "redact.db"
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    token = "EAAFAKE1234567890ABCDEFGHIJ"
    media = f"https://cdn.example/photo.jpg?_nc_cat=1&access_token={token}&oe=AB12"
    with get_connection(db_path) as conn:
        account_id = upsert_account(
            conn,
            platform="instagram",
            external_id="ig-1",
            username="example",
        )
        upsert_post(
            conn,
            platform="instagram",
            external_id="post-1",
            account_id=account_id,
            content_type="post",
            text=f"caption stays {token}",
            permalink="https://www.instagram.com/p/abc/",
            media_url=media,
            thumbnail_url=media,
            media_type="IMAGE",
            published_at="2026-11-01T00:00:00Z",
            raw_json='{"media_url": "' + media + '", "like_count": 4}',
        )
        conn.commit()
        row = conn.execute("SELECT text, media_url, thumbnail_url, raw_json FROM posts").fetchone()
    assert row["text"] == "caption stays "
    assert "cdn.example/photo.jpg" in row["media_url"]
    assert "_nc_cat=1" in row["media_url"]
    assert "oe=AB12" in row["media_url"]
    assert "access_token" not in row["media_url"]
    assert token not in row["media_url"]
    assert token not in row["thumbnail_url"]
    assert token not in row["raw_json"]
    assert "like_count" in row["raw_json"]
    assert redact_text(None) is None
