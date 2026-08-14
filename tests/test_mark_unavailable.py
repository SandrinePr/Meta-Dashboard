"""Tests for marking unavailable posts in the repository."""

from __future__ import annotations

import json
import sqlite3

from db.database import initialize_database
from db.repository import mark_post_unavailable, upsert_account, upsert_post


def test_mark_post_unavailable_sets_flag(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    initialize_database()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    account_id = upsert_account(
        conn,
        platform="instagram",
        external_id="acc1",
    )
    result = upsert_post(
        conn,
        platform="instagram",
        external_id="post1",
        account_id=account_id,
        content_type="post",
        text="Hello",
        permalink="https://instagram.com/p/abc",
        media_url=None,
        thumbnail_url=None,
        media_type="IMAGE",
        published_at="2025-01-01T12:00:00+00:00",
        raw_json=json.dumps({"caption": "Hello"}),
    )
    mark_post_unavailable(conn, result.row_id, reason="does not exist")
    conn.commit()
    row = conn.execute(
        "SELECT raw_json FROM posts WHERE id = ?",
        (result.row_id,),
    ).fetchone()
    payload = json.loads(row["raw_json"])
    assert payload["rro_unavailable"] is True
    assert payload["rro_unavailable_reason"] == "does not exist"
    assert payload["caption"] == "Hello"
