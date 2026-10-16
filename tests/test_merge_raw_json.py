"""Tests for post raw_json merge that preserves Insights on re-sync."""

from __future__ import annotations

import json

from db.repository import merge_post_raw_json


def test_merge_preserves_insights_when_new_payload_lacks_them() -> None:
    existing = json.dumps(
        {
            "id": "1",
            "like_count": 5,
            "insights_views": 900,
            "insights_saved": 3,
        }
    )
    new = json.dumps({"id": "1", "like_count": 12, "caption": "nieuw"})
    merged = json.loads(merge_post_raw_json(existing, new) or "{}")
    assert merged["like_count"] == 12
    assert merged["caption"] == "nieuw"
    assert merged["insights_views"] == 900
    assert merged["insights_saved"] == 3


def test_merge_prefers_new_insights_when_present() -> None:
    existing = json.dumps({"insights_views": 100})
    new = json.dumps({"insights_views": 250, "like_count": 1})
    merged = json.loads(merge_post_raw_json(existing, new) or "{}")
    assert merged["insights_views"] == 250
    assert merged["like_count"] == 1


def test_merge_preserves_unavailable_flag() -> None:
    existing = json.dumps(
        {"caption": "old", "rro_unavailable": True, "rro_unavailable_reason": "gone"}
    )
    new = json.dumps({"caption": "new", "like_count": 1})
    merged = json.loads(merge_post_raw_json(existing, new) or "{}")
    assert merged["rro_unavailable"] is True
    assert merged["rro_unavailable_reason"] == "gone"


def test_merge_clears_unavailable_when_post_is_live_again() -> None:
    existing = json.dumps({"rro_unavailable": True, "rro_unavailable_reason": "gone"})
    new = json.dumps({"caption": "back online"})
    merged = json.loads(
        merge_post_raw_json(existing, new, clear_unavailable=True) or "{}"
    )
    assert "rro_unavailable" not in merged


def test_merge_preserves_media_urls_when_lean_payload_omits_them() -> None:
    existing = json.dumps(
        {
            "id": "1",
            "media_url": "https://scontent.cdninstagram.com/v/t51/image.jpg",
            "thumbnail_url": "https://scontent.cdninstagram.com/v/t51/thumb.jpg",
            "caption": "Blijf staan",
            "like_count": 5,
        }
    )
    new = json.dumps({"id": "1", "like_count": 12, "media_type": "IMAGE"})
    merged = json.loads(merge_post_raw_json(existing, new) or "{}")
    assert merged["like_count"] == 12
    assert merged["media_url"].endswith("image.jpg")
    assert merged["thumbnail_url"].endswith("thumb.jpg")
    assert merged["caption"] == "Blijf staan"


def test_merge_prefers_new_media_url_when_present() -> None:
    existing = json.dumps({"media_url": "https://scontent.cdninstagram.com/old.jpg"})
    new = json.dumps({"media_url": "https://scontent.cdninstagram.com/new.jpg"})
    merged = json.loads(merge_post_raw_json(existing, new) or "{}")
    assert merged["media_url"].endswith("new.jpg")
