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
