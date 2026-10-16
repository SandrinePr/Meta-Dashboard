"""Mocked central-refresh path: profile-first then media, including #4 stop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.comparison_repository import (
    ROLE_COMPETITOR,
    ROLE_OWN,
    ensure_default_comparison_accounts,
    find_active_fetch_run,
    get_account_by_username,
    get_discovery_rate_limit,
    save_preferences,
    upsert_comparison_account,
)
from db.database import get_connection, initialize_database
from meta.client import RATE_LIMIT_USER_MESSAGE, MetaRequestError, MetaRequestMeta
from meta.discovery import DiscoveryPageResult
from sync.comparison import (
    _COMPETITOR_FETCH_PRIORITY,
    _fetch_competitor_profile,
    run_comparison_sync,
)


def _setup(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "profile_first.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("META_PAGE_ACCESS_TOKEN", "page-token")
    monkeypatch.setenv("META_USER_ACCESS_TOKEN", "user-token")
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    monkeypatch.setattr("sync.comparison.time_module.sleep", lambda *_a, **_k: None)


def _seed_accounts(conn) -> None:
    ensure_default_comparison_accounts(conn)
    own = upsert_comparison_account(
        conn,
        external_id="own-ig-1",
        username="theartofliving.online",
        name="TAOL",
        comparison_role=ROLE_OWN,
        is_discovery_host=True,
        access_status="ok",
    )
    for username, ext in (
        ("excellent.magazine", "ig-ex"),
        ("vtwonen", "ig-vt"),
        ("bestinteriors_official", "ig-bi"),
        ("hoog.design", "ig-hoog"),
    ):
        upsert_comparison_account(
            conn,
            external_id=ext,
            username=username,
            name=username,
            comparison_role=ROLE_COMPETITOR,
            is_discovery_host=False,
            access_status="ok",
        )
    save_preferences(
        conn,
        own_account_id=own,
        competitors=[
            "hoog.design",
            "bestinteriors_official",
            "excellent.magazine",
            "vtwonen",
        ],
    )
    conn.commit()


def _meta_ok() -> MetaRequestMeta:
    return MetaRequestMeta(
        duration_ms=12.0,
        usage_headers={"x-app-usage": {"call_count": 1, "total_time": 1}},
    )


def _profile_discovery(username: str, followers: int, external_id: str) -> dict[str, Any]:
    return {
        "id": external_id,
        "username": username,
        "name": username,
        "followers_count": followers,
        "media_count": 10,
    }


def _empty_media_page(username: str, external_id: str) -> DiscoveryPageResult:
    return DiscoveryPageResult(
        page_index=1,
        page_size=25,
        posts=[],
        posts_in_period=[],
        account_payload=_profile_discovery(username, 1, external_id),
        cursor_before=None,
        cursor_after=None,
        reached_cutoff=True,
        request_meta=_meta_ok(),
        stopped_reason="cutoff bereikt",
    )


def test_fetch_competitor_profile_is_exported() -> None:
    assert callable(_fetch_competitor_profile)
    assert _COMPETITOR_FETCH_PRIORITY[:3] == (
        "excellent.magazine",
        "vtwonen",
        "bestinteriors_official",
    )


def test_central_refresh_profile_success_then_media(
    tmp_path: Path, monkeypatch
) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        _seed_accounts(conn)

    profile_calls: list[str] = []
    media_calls: list[str] = []

    def fake_profile(client, host_ig_id, username, **kwargs):
        assert kwargs.get("include_media") is False
        profile_calls.append(username)
        followers = {"excellent.magazine": 100, "vtwonen": 200, "bestinteriors_official": 300}.get(
            username, 1
        )
        ext = {
            "excellent.magazine": "ig-ex",
            "vtwonen": "ig-vt",
            "bestinteriors_official": "ig-bi",
            "hoog.design": "ig-hoog",
        }[username]
        return _profile_discovery(username, followers, ext), _meta_ok()

    def fake_media_pages(client, host_ig_id, username, **kwargs):
        media_calls.append(username)
        ext = {
            "excellent.magazine": "ig-ex",
            "vtwonen": "ig-vt",
            "bestinteriors_official": "ig-bi",
            "hoog.design": "ig-hoog",
        }[username]
        yield _empty_media_page(username, ext)

    monkeypatch.setattr(
        "sync.comparison.MetaClient.from_settings",
        lambda: object(),
    )
    monkeypatch.setattr("sync.comparison.fetch_business_discovery", fake_profile)
    monkeypatch.setattr("sync.comparison.iter_discovery_media_pages", fake_media_pages)
    monkeypatch.setattr(
        "sync.comparison._has_follower_measurement_on_day",
        lambda *_a, **_k: False,
    )

    result = run_comparison_sync(trigger_source="manual", include_own=False)
    assert result.status in {"completed", "completed_with_errors"}
    assert result.run_id is not None
    # Priority order for profile phase.
    assert profile_calls[:3] == [
        "excellent.magazine",
        "vtwonen",
        "bestinteriors_official",
    ]
    assert media_calls[:3] == [
        "excellent.magazine",
        "vtwonen",
        "bestinteriors_official",
    ]

    with get_connection() as conn:
        assert find_active_fetch_run(conn) is None
        for username, expected in (
            ("excellent.magazine", 100),
            ("vtwonen", 200),
            ("bestinteriors_official", 300),
        ):
            account = get_account_by_username(conn, username)
            assert account is not None
            row = conn.execute(
                """
                SELECT followers_count FROM account_measurements
                WHERE account_id = ?
                ORDER BY measured_at DESC, id DESC LIMIT 1
                """,
                (account.account_id,),
            ).fetchone()
            assert row is not None
            assert int(row["followers_count"]) == expected
        run = conn.execute(
            "SELECT status, finished_at FROM comparison_fetch_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        assert run["status"] != "running"
        assert run["finished_at"] is not None


def test_central_refresh_stops_on_profile_rate_limit_without_retries(
    tmp_path: Path, monkeypatch
) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        _seed_accounts(conn)

    profile_calls: list[str] = []
    media_calls: list[str] = []

    def fake_profile(client, host_ig_id, username, **kwargs):
        profile_calls.append(username)
        if username == "excellent.magazine":
            raise MetaRequestError(
                RATE_LIMIT_USER_MESSAGE,
                error_code=4,
                is_rate_limited=True,
                usage_headers={
                    "x-app-usage": {"call_count": 8, "total_cputime": 0, "total_time": 114}
                },
                retry_after_seconds=None,
                blocked_until=None,
            )
        return _profile_discovery(username, 1, f"ig-{username}"), _meta_ok()

    def fake_media_pages(client, host_ig_id, username, **kwargs):
        media_calls.append(username)
        yield _empty_media_page(username, f"ig-{username}")

    monkeypatch.setattr("sync.comparison.MetaClient.from_settings", lambda: object())
    monkeypatch.setattr("sync.comparison.fetch_business_discovery", fake_profile)
    monkeypatch.setattr("sync.comparison.iter_discovery_media_pages", fake_media_pages)
    monkeypatch.setattr(
        "sync.comparison._has_follower_measurement_on_day",
        lambda *_a, **_k: False,
    )

    result = run_comparison_sync(trigger_source="manual", include_own=False)
    assert result.rate_limited is True
    assert result.error_message == RATE_LIMIT_USER_MESSAGE
    assert profile_calls == ["excellent.magazine"]
    assert media_calls == []
    assert result.run_id is not None

    with get_connection() as conn:
        assert find_active_fetch_run(conn) is None
        state = get_discovery_rate_limit(conn)
        assert state is not None
        assert state.error_code == 4
        run = conn.execute(
            "SELECT status, finished_at, error_message FROM comparison_fetch_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        assert run["status"] != "running"
        assert run["finished_at"] is not None
        assert run["error_message"] == RATE_LIMIT_USER_MESSAGE


def test_unexpected_error_finishes_running_fetch_run(
    tmp_path: Path, monkeypatch
) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        _seed_accounts(conn)

    monkeypatch.setattr("sync.comparison.MetaClient.from_settings", lambda: object())
    monkeypatch.setattr(
        "sync.comparison._execute_comparison_sync",
        lambda **_kwargs: (_ for _ in ()).throw(NameError("_fetch_competitor_profile is not defined")),
    )

    result = run_comparison_sync(trigger_source="manual", include_own=False)
    assert result.status == "failed"
    assert "_fetch_competitor_profile" in (result.error_message or "")
    assert result.run_id is not None

    with get_connection() as conn:
        assert find_active_fetch_run(conn) is None
        run = conn.execute(
            "SELECT status, finished_at FROM comparison_fetch_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        assert run["status"] == "failed"
        assert run["finished_at"] is not None
