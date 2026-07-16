"""Tests for Meta insights parsing helpers."""

from __future__ import annotations

import json

from meta.insights import (
    flatten_instagram_insights,
    parse_insights_metrics,
)
from ui.media import extract_post_stats


def test_parse_insights_values_list() -> None:
    payload = {
        "data": [
            {"name": "views", "period": "lifetime", "values": [{"value": 1200}]},
            {"name": "saved", "period": "lifetime", "values": [{"value": 9}]},
            {"name": "reach", "period": "lifetime", "values": [{"value": 800}]},
        ]
    }
    assert parse_insights_metrics(payload) == {"views": 1200, "saved": 9, "reach": 800}


def test_parse_insights_total_value() -> None:
    payload = {
        "data": [
            {"name": "total_views", "period": "lifetime", "total_value": {"value": 4500}},
        ]
    }
    assert parse_insights_metrics(payload) == {"total_views": 4500}


def test_extract_post_stats_reads_insights_views_for_image() -> None:
    raw = json.dumps(
        {
            "like_count": 10,
            "comments_count": 2,
            "media_type": "IMAGE",
            "insights": {
                "data": [
                    {"name": "views", "values": [{"value": 3210}]},
                    {"name": "saved", "values": [{"value": 14}]},
                ]
            },
        }
    )
    assert extract_post_stats("instagram", raw) == {
        "likes": 10,
        "comments": 2,
        "views": 3210,
        "saves": 14,
    }


def test_extract_post_stats_prefers_flattened_insights_views() -> None:
    raw = json.dumps(
        {
            "like_count": 1,
            "insights_views": 999,
            "insights": {"data": [{"name": "views", "values": [{"value": 1}]}]},
        }
    )
    assert extract_post_stats("instagram", raw)["views"] == 999


def test_flatten_instagram_insights_sets_helper_fields() -> None:
    payload = {"id": "1", "like_count": 3}
    insights = {
        "data": [
            {"name": "views", "values": [{"value": 50}]},
            {"name": "saved", "values": [{"value": 2}]},
        ]
    }
    enriched = flatten_instagram_insights(payload, insights)
    assert enriched["insights_views"] == 50
    assert enriched["insights_saved"] == 2
    assert enriched["insights"] == insights
