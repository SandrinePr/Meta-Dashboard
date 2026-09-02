"""Tests for on-demand insights refresh."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from sync.engagement_refresh import refresh_post_insights


def test_refresh_post_insights_updates_total_views() -> None:
    client = MagicMock()
    client.get_json.return_value = {
        "id": "18118533478746173",
        "like_count": 400,
        "comments_count": 15,
        "media_product_type": "REELS",
    }
    client.get_instagram_media_insights.return_value = {
        "data": [
            {"name": "views", "values": [{"value": 30000}]},
            {"name": "total_views", "total_value": {"value": 1_003_602}},
            {"name": "saved", "values": [{"value": 136}]},
        ]
    }

    existing = json.dumps({"insights_views": 258560, "like_count": 352})
    new_raw, error = refresh_post_insights(
        client,
        post_id=44,
        platform="instagram",
        external_id="18118533478746173",
        raw_json=existing,
    )

    assert error is None
    assert new_raw is not None
    payload = json.loads(new_raw)
    assert payload["insights_views"] == 1_003_602
    assert payload["like_count"] == 400


def test_refresh_post_insights_only_skips_media_fetch() -> None:
    client = MagicMock()
    client.get_instagram_media_insights.return_value = {
        "data": [
            {"name": "total_views", "total_value": {"value": 500_000}},
        ]
    }

    existing = json.dumps({"insights_views": 100, "like_count": 12, "caption": "keep me"})
    new_raw, error = refresh_post_insights(
        client,
        post_id=1,
        platform="instagram",
        external_id="123",
        raw_json=existing,
        insights_only=True,
    )

    assert error is None
    client.get_json.assert_not_called()
    payload = json.loads(new_raw or "{}")
    assert payload["insights_views"] == 500_000
    assert payload["like_count"] == 12
    assert payload["caption"] == "keep me"
