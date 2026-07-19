"""Tests for sync recency helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sync.orchestrator import _is_within_days


def test_is_within_days_recent() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert _is_within_days(recent, 14) is True


def test_is_within_days_old() -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    assert _is_within_days(old, 30) is False
