"""Tests for Meta rate-limit handling without live API calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import responses

from db.comparison_repository import (
    clear_discovery_rate_limit,
    discovery_requests_blocked,
    get_discovery_rate_limit,
    set_discovery_rate_limit,
)
from db.database import get_connection, initialize_database
from meta.client import MetaClient, MetaRequestError, RATE_LIMIT_USER_MESSAGE
from meta.rate_limit import build_rate_limit_info, is_rate_limit_error, parse_retry_after_seconds
from sync.comparison import _order_competitors, resume_hoog_design_fetch, run_comparison_sync


def _setup(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "rate.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)


def _client() -> MetaClient:
    return MetaClient(
        base_url="https://graph.facebook.com/v25.0",
        timeout_seconds=5,
        user_access_token="user-token",
        page_access_token="page-token",
        default_page_id="123",
        default_instagram_business_account_id="456",
    )


def test_code_4_is_rate_limit() -> None:
    assert is_rate_limit_error(error_code=4, message="Application request limit reached")


def test_parse_retry_after_from_header_only() -> None:
    assert parse_retry_after_seconds(headers={"Retry-After": "90"}) == 90
    assert parse_retry_after_seconds(headers={}) is None
    assert parse_retry_after_seconds(message="try again in 2 minutes") == 120


@responses.activate
def test_rate_limit_error_is_not_retried() -> None:
    client = _client()
    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/456",
        json={
            "error": {
                "message": "(#4) Application request limit reached",
                "code": 4,
                "is_transient": True,
            }
        },
        status=403,
        headers={"x-app-usage": '{"call_count":100,"total_cputime":10,"total_time":10}'},
    )
    try:
        client.get_json("456", params={"fields": "id"})
        raise AssertionError("expected MetaRequestError")
    except MetaRequestError as exc:
        assert exc.is_rate_limited is True
        assert exc.error_code == 4
        assert str(exc) == RATE_LIMIT_USER_MESSAGE
        assert exc.usage_headers.get("x-app-usage", {}).get("call_count") == 100
    assert len(responses.calls) == 1


@responses.activate
def test_retry_after_header_sets_blocked_until() -> None:
    client = _client()
    responses.add(
        responses.GET,
        "https://graph.facebook.com/v25.0/456",
        json={"error": {"message": "rate limit", "code": 4, "is_transient": True}},
        status=403,
        headers={"Retry-After": "120"},
    )
    try:
        client.get_json("456", params={"fields": "id"})
        raise AssertionError("expected MetaRequestError")
    except MetaRequestError as exc:
        assert exc.retry_after_seconds == 120
        assert exc.blocked_until is not None


def test_force_does_not_bypass_active_rate_limit_wait(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    blocked_until = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()
    with get_connection() as conn:
        set_discovery_rate_limit(
            conn,
            error_code=4,
            message=RATE_LIMIT_USER_MESSAGE,
            retry_after_seconds=3600,
            blocked_until=blocked_until,
            usage={"x-app-usage": {"call_count": 100}},
        )
        conn.commit()

    result = run_comparison_sync(trigger_source="test", force=True)
    assert result.status == "skipped"
    assert result.rate_limited is True
    assert result.error_message == RATE_LIMIT_USER_MESSAGE
    assert result.rate_limit_blocked_until == blocked_until


def test_competitor_order_puts_missing_day_priority_first() -> None:
    assert _order_competitors(
        ["vtwonen", "hoog.design", "excellent.magazine", "bestinteriors_official"]
    ) == [
        "excellent.magazine",
        "vtwonen",
        "bestinteriors_official",
        "hoog.design",
    ]


def test_rate_limit_state_clears_when_inactive(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    past = (datetime.now(tz=timezone.utc) - timedelta(minutes=5)).isoformat()
    with get_connection() as conn:
        set_discovery_rate_limit(
            conn,
            error_code=4,
            message=RATE_LIMIT_USER_MESSAGE,
            retry_after_seconds=1,
            blocked_until=past,
        )
        state = get_discovery_rate_limit(conn)
        assert state is not None
        assert state.active is False
        clear_discovery_rate_limit(conn)
        assert get_discovery_rate_limit(conn) is None
        conn.commit()


def test_build_rate_limit_info_does_not_invent_wait() -> None:
    info = build_rate_limit_info(error_code=4, message="Application request limit reached")
    assert info.retry_after_seconds is None
    assert info.blocked_until is None


def test_rate_limit_without_end_time_is_informational_only(
    tmp_path: Path, monkeypatch
) -> None:
    """Persisted #4 without Retry-After must not permanently block future runs."""
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        set_discovery_rate_limit(
            conn,
            error_code=4,
            message=RATE_LIMIT_USER_MESSAGE,
            retry_after_seconds=None,
            blocked_until=None,
            usage={"x-app-usage": {"call_count": 100}},
        )
        state = get_discovery_rate_limit(conn)
        assert state is not None
        assert state.active is False
        assert state.reset_moment_known is False
        assert discovery_requests_blocked(conn) is None
        conn.commit()

    # Gate must not skip; sync proceeds until Meta responds (token missing in test).
    result = run_comparison_sync(trigger_source="test", force=False)
    assert result.status != "skipped" or result.skipped_reason is None or (
        "Wachttijd" not in (result.skipped_reason or "")
    )
    assert result.rate_limit_blocked_until is None


def test_resume_hoog_design_is_single_account_only(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    called: dict[str, object] = {}

    def fake_sync(**kwargs):
        called.update(kwargs)
        from sync.comparison import ComparisonSyncResult

        return ComparisonSyncResult(status="skipped", skipped_reason="test-stub")

    monkeypatch.setattr("sync.comparison.run_comparison_sync", fake_sync)
    result = resume_hoog_design_fetch(trigger_source="test_resume")
    assert called["only_usernames"] == ["hoog.design"]
    assert called["include_own"] is False
    assert called["force"] is False
    assert called["trigger_source"] == "test_resume"
    assert result.status == "skipped"


def test_resume_account_fetch_targets_requested_username(
    tmp_path: Path, monkeypatch
) -> None:
    _setup(tmp_path, monkeypatch)
    called: dict[str, object] = {}

    def fake_sync(**kwargs):
        called.update(kwargs)
        from sync.comparison import ComparisonSyncResult

        return ComparisonSyncResult(status="skipped", skipped_reason="test-stub")

    monkeypatch.setattr("sync.comparison.run_comparison_sync", fake_sync)
    from sync.comparison import resume_account_fetch

    result = resume_account_fetch(
        "bestinteriors_official",
        trigger_source="test_resume_best",
    )
    assert called["only_usernames"] == ["bestinteriors_official"]
    assert called["include_own"] is False
    assert called["trigger_source"] == "test_resume_best"
    assert result.status == "skipped"
