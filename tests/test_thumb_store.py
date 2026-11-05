"""Thumbnail bytes survive in SQLite, and cards never receive a token URL."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from db.database import get_connection
from db.thumb_store import materialize_thumbnails, store_jpeg
from ui.media import choose_card_image


def _jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color=(20, 40, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_choose_card_image_prefers_file_and_strips_tokens() -> None:
    assert choose_card_image("data:image/jpeg;base64,abc", "https://cdn.example/a.jpg") == (
        "data:image/jpeg;base64,abc"
    )
    cleaned = choose_card_image(
        None,
        "https://scontent.cdninstagram.com/a.jpg?oe=111&oh=222&access_token=EAAFAKE1234567890ABCDEFGHIJ",
    )
    assert cleaned is not None
    assert cleaned.startswith("https://scontent.cdninstagram.com/a.jpg?")
    assert "oe=111" in cleaned
    assert "access_token" not in cleaned
    assert "EAA" not in cleaned
    assert choose_card_image(None, "https://cdn.example/clip.mp4") is None
    assert choose_card_image(None, None) is None


def test_snapshot_blob_restores_missing_thumbnail(tmp_path: Path) -> None:
    db_path = tmp_path / "social.db"
    media_dir = tmp_path / "media"
    schema = Path("db/schema.sql").read_text(encoding="utf-8")
    with get_connection(db_path) as conn:
        conn.executescript(schema)
        conn.execute(
            """
            INSERT INTO accounts (platform, external_id, name, username)
            VALUES ('instagram', 'acct-1', 'TAOL', 'theartofliving.online')
            """
        )
        conn.execute(
            """
            INSERT INTO posts (
                platform, external_id, account_id, content_type, published_at
            )
            VALUES ('instagram', 'post-1', 1, 'post', '2026-11-04T18:16:44+00:00')
            """
        )
        conn.commit()
    store_jpeg(1, _jpeg_bytes(), db_path=db_path)

    written = materialize_thumbnails(media_dir, db_path)
    target = media_dir / "post_1.jpg"
    assert written == 1
    assert target.exists()
    assert target.read_bytes().startswith(b"\xff\xd8")
    assert materialize_thumbnails(media_dir, db_path) == 0
