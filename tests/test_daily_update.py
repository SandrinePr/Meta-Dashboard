"""Daily 13:30 / 20:30 steps: new posts only, resume after a Meta limit."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from db.comparison_repository import (
    ensure_default_comparison_accounts,
    finish_fetch_run,
    start_fetch_run,
    upsert_comparison_post,
    upsert_post_measurement,
)
from db.database import get_connection, initialize_database
from meta.client import MetaRequestError
from sync.daily_update import MediaPage, run_daily_update
from sync.scheduler import EVENING_SOURCE, run_evening_refresh

NOW = datetime(2026, 11, 6, 12, 30, tzinfo=timezone.utc)


def _setup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "daily_update.db"))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database()


def test_daily_step_keeps_history_and_resumes_after_limit(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        own = ensure_default_comparison_accounts(conn)
        run = start_fetch_run(
            conn,
            trigger_source="seed",
            measured_at="2026-08-01T12:00:00+00:00",
            period_start="2026-08-01T12:00:00+00:00",
            period_end="2026-08-01T12:00:00+00:00",
        )
        stored = upsert_comparison_post(
            conn,
            account_id=own.account_id,
            external_id="old-post",
            text="bewaard",
            permalink="https://instagram.com/p/old",
            media_type="IMAGE",
            media_product_type="FEED",
            published_at="2026-08-01T12:00:00+00:00",
            raw_json='{"id":"old-post","caption":"bewaard"}',
        )
        upsert_post_measurement(
            conn,
            post_id=stored.row_id,
            fetch_run_id=run,
            measured_at="2026-08-01T12:00:00+00:00",
            like_count=9,
            comments_count=1,
        )
        finish_fetch_run(conn, run, status="completed")
        conn.commit()
        own_id = own.account_id

    calls: list[tuple[str, str]] = []
    blocked = {"hoog.design"}

    def _profile(client, host_id, username, *, own):
        calls.append(("profile", username))
        if username in blocked:
            raise MetaRequestError("limit", error_code=4, is_rate_limited=True)
        external_id = "17841400191455972" if own else f"id-{username}"
        return {
            "id": external_id,
            "username": username,
            "name": username,
            "followers_count": 20,
            "media_count": 2,
        }

    def _media(client, host_id, username, *, own, cursor, watermark):
        calls.append(("media", username))
        if username == "theartofliving.online":
            return MediaPage(
                [
                    {
                        "id": "old-post",
                        "timestamp": "2026-08-01T12:00:00+0000",
                        "like_count": 9,
                        "comments_count": 1,
                        "caption": "overschreven",
                    },
                    {
                        "id": "new-post",
                        "timestamp": "2026-11-06T08:00:00+0000",
                        "like_count": 4,
                        "comments_count": 2,
                        "caption": "nieuw",
                    },
                ],
                None,
                True,
            )
        return MediaPage([], None, True)

    monkeypatch.setattr("sync.daily_update._load_profile", _profile)
    monkeypatch.setattr("sync.daily_update._load_media_page", _media)

    first = run_daily_update(trigger_source="schedule_afternoon_comparison", client=object(), now=NOW)
    assert first.rate_limited is True
    assert [item.username for item in first.accounts] == ["theartofliving.online"]
    assert first.accounts[0].posts_fetched == 1
    assert ("profile", "bestinteriors_official") not in calls

    with get_connection() as conn:
        conn.execute("DELETE FROM comparison_rate_limits")
        conn.commit()
        old = conn.execute(
            "SELECT text FROM posts WHERE external_id = 'old-post'"
        ).fetchone()
        assert old["text"] == "bewaard"
        measurements = conn.execute(
            """
            SELECT measured_at, like_count FROM post_measurements
            WHERE post_id = (SELECT id FROM posts WHERE external_id = 'old-post')
            """
        ).fetchall()
        assert len(measurements) == 1
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM posts WHERE account_id = ?",
            (own_id,),
        ).fetchone()
        assert int(total["c"]) == 2

    blocked.clear()
    calls.clear()
    second = run_daily_update(trigger_source="schedule_afternoon_comparison", client=object(), now=NOW)
    assert second.rate_limited is False
    updated = {item.username for item in second.accounts}
    assert updated == {
        "hoog.design",
        "bestinteriors_official",
        "excellent.magazine",
        "vtwonen",
    }
    assert ("profile", "theartofliving.online") not in calls
    assert all(item.posts_fetched == 0 for item in second.accounts)
    with get_connection() as conn:
        follower = conn.execute(
            """
            SELECT measured_at FROM account_measurements
            WHERE account_id = ? AND measured_at = ?
            """,
            (own_id, NOW.isoformat()),
        ).fetchone()
        assert follower is not None


def test_code_100_skips_one_account_and_continues(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)

    def _profile(client, host_id, username, *, own):
        if username == "hoog.design":
            raise MetaRequestError(
                "Unsupported get request. Object with ID 'hoog.design' does not exist",
                error_code=100,
            )
        return {
            "id": f"id-{username}",
            "username": username,
            "name": username,
            "followers_count": 3,
            "media_count": 1,
        }

    def _media(client, host_id, username, *, own, cursor, watermark):
        return MediaPage([], None, True)

    monkeypatch.setattr("sync.daily_update._load_profile", _profile)
    monkeypatch.setattr("sync.daily_update._load_media_page", _media)
    result = run_daily_update(trigger_source="schedule_evening_refresh", client=object(), now=NOW)
    failed = [item.username for item in result.accounts if item.status == "failed"]
    succeeded = [item.username for item in result.accounts if item.status == "success"]
    assert failed == ["hoog.design"]
    assert "theartofliving.online" in succeeded
    assert "bestinteriors_official" in succeeded
    assert "excellent.magazine" in succeeded
    assert "vtwonen" in succeeded
    assert result.status == "completed_with_errors"


def test_evening_job_does_not_run_the_full_sync(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    called: list[str] = []

    def _daily(**kwargs):
        called.append(kwargs["trigger_source"])
        from sync.comparison import ComparisonSyncResult

        return ComparisonSyncResult(status="completed", run_id=1, measured_at=NOW.isoformat())

    monkeypatch.setattr("sync.daily_update.run_daily_update", _daily)
    monkeypatch.setattr("sync.daily_update.daily_slot_complete", lambda *args, **kwargs: False)
    monkeypatch.setattr("sync.orchestrator.run_sync", lambda **kwargs: called.append("full-sync"))
    monkeypatch.setattr(
        "sync.thumbnails.cache_missing_thumbnails",
        lambda **kwargs: called.append(f"thumbs:{kwargs.get('limit')}") or (0, 0, 0),
    )
    run_evening_refresh()
    assert called == [EVENING_SOURCE, "thumbs:20"]


def test_second_page_keeps_the_original_boundary(tmp_path: Path, monkeypatch) -> None:
    """Page two stays behind the original date, not the newest post from page one."""
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        own = ensure_default_comparison_accounts(conn)
        run = start_fetch_run(
            conn,
            trigger_source="seed",
            measured_at="2026-08-01T12:00:00+00:00",
            period_start="2026-08-01T12:00:00+00:00",
            period_end="2026-08-01T12:00:00+00:00",
        )
        stored = upsert_comparison_post(
            conn,
            account_id=own.account_id,
            external_id="known-post",
            text="bekend",
            permalink="https://instagram.com/p/known",
            media_type="IMAGE",
            media_product_type="FEED",
            published_at="2026-08-01T12:00:00+00:00",
            raw_json='{"id":"known-post"}',
        )
        upsert_post_measurement(
            conn,
            post_id=stored.row_id,
            fetch_run_id=run,
            measured_at="2026-08-01T12:00:00+00:00",
            like_count=9,
            comments_count=1,
        )
        finish_fetch_run(conn, run, status="completed")
        conn.commit()

    def _profile(client, host_id, username, *, own):
        return {
            "id": "17841400191455972" if own else f"id-{username}",
            "username": username,
            "name": username,
            "followers_count": 11,
            "media_count": 3,
        }

    def _media(client, host_id, username, *, own, cursor, watermark):
        if username != "theartofliving.online":
            return MediaPage([], None, True)
        if cursor is None:
            assert watermark is not None
            assert watermark.isoformat().startswith("2026-08-01")
            return MediaPage(
                [
                    {
                        "id": "known-post",
                        "timestamp": "2026-08-01T12:00:00+0000",
                        "like_count": 40,
                        "comments_count": 8,
                        "caption": "nieuwe likes",
                    },
                    {
                        "id": "page1-new",
                        "timestamp": "2026-11-06T10:00:00+0000",
                        "like_count": 2,
                        "comments_count": 0,
                        "caption": "pagina een",
                    },
                ],
                "cursor-page-2",
                False,
            )
        assert cursor == "cursor-page-2"
        assert watermark is not None
        assert watermark.isoformat().startswith("2026-08-01")
        return MediaPage(
            [
                {
                    "id": "page2-new",
                    "timestamp": "2026-10-20T10:00:00+0000",
                    "like_count": 5,
                    "comments_count": 1,
                    "caption": "pagina twee",
                },
                {
                    "id": "too-old",
                    "timestamp": "2026-07-01T10:00:00+0000",
                    "like_count": 1,
                    "comments_count": 0,
                    "caption": "ouder dan de grens",
                },
            ],
            "cursor-page-3",
            True,
        )

    monkeypatch.setattr("sync.daily_update._load_profile", _profile)
    monkeypatch.setattr("sync.daily_update._load_media_page", _media)

    afternoon = run_daily_update(
        trigger_source="schedule_afternoon_comparison", client=object(), now=NOW
    )
    assert afternoon.accounts[0].posts_fetched == 1
    evening = run_daily_update(
        trigger_source="schedule_evening_refresh",
        client=object(),
        now=NOW.replace(hour=19, minute=30),
    )
    own_pages = [item for item in evening.accounts if item.username == "theartofliving.online"]
    assert own_pages[0].posts_fetched == 1
    with get_connection() as conn:
        ids = {
            row["external_id"]
            for row in conn.execute("SELECT external_id FROM posts").fetchall()
        }
        assert "page1-new" in ids
        assert "page2-new" in ids
        assert "too-old" not in ids
        likes = conn.execute(
            """
            SELECT like_count FROM post_measurements
            WHERE post_id = (SELECT id FROM posts WHERE external_id = 'known-post')
            ORDER BY measured_at
            """
        ).fetchall()
        assert [row["like_count"] for row in likes] == [9]
