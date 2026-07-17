"""Parse and attach Meta insights metrics (views/reach/saves) onto post payloads."""

from __future__ import annotations

from typing import Any


def _metric_value(item: dict[str, Any]) -> int | None:
    """Extract a numeric metric value from an insights data item."""
    total_value = item.get("total_value")
    if isinstance(total_value, dict):
        value = total_value.get("value")
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)

    values = item.get("values")
    if isinstance(values, list) and values:
        last = values[-1]
        if isinstance(last, dict):
            value = last.get("value")
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return None


def parse_insights_metrics(insights_payload: dict[str, Any] | None) -> dict[str, int]:
    """Map Instagram/Facebook insights `data` items to flat metric names."""
    if not isinstance(insights_payload, dict):
        return {}
    data = insights_payload.get("data")
    if not isinstance(data, list):
        return {}

    metrics: dict[str, int] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        value = _metric_value(item)
        if value is not None:
            metrics[name] = value
    return metrics


def flatten_instagram_insights(payload: dict[str, Any], insights: dict[str, Any]) -> dict[str, Any]:
    """Copy insights onto a media payload and add flat helper fields for the UI."""
    enriched = dict(payload)
    enriched["insights"] = insights
    metrics = parse_insights_metrics(insights)

    # Prefer total_views (boosted + organic) when available, else organic views,
    # else legacy impressions for older feed posts.
    for key in ("total_views", "views", "impressions"):
        if key in metrics:
            enriched["insights_views"] = metrics[key]
            break
    if "saved" in metrics:
        enriched["insights_saved"] = metrics["saved"]
    if "reach" in metrics:
        enriched["insights_reach"] = metrics["reach"]
    return enriched


def flatten_facebook_insights(payload: dict[str, Any], insights: dict[str, Any]) -> dict[str, Any]:
    """Copy Facebook post insights onto a payload with flat view helpers."""
    enriched = dict(payload)
    enriched["insights"] = insights
    metrics = parse_insights_metrics(insights)
    for key in (
        "post_media_view",
        "post_total_media_view_unique",
        "post_video_views",
        "post_impressions",
        "post_impressions_unique",
    ):
        if key in metrics:
            enriched["insights_views"] = metrics[key]
            break

    # Facebook does not expose post saves via Graph API; keep saves only when present
    # in activity breakdown (rare) for forward compatibility.
    activity = metrics.get("post_activity_by_action_type")
    if isinstance(activity, dict):
        for save_key in ("save", "saves", "saved"):
            if save_key in activity and isinstance(activity[save_key], (int, float)):
                enriched["insights_saved"] = int(activity[save_key])
    return enriched
