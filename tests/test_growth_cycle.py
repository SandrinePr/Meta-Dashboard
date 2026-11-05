"""Weekly growth queue. Time is injected; no Meta calls."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from analytics.week_growth import (
    INSUFFICIENT_GROWTH,
    WeekMeasurement,
    describe_week_growth,
)
from db.comparison_repository import (
    ROLE_COMPETITOR,
    ROLE_OWN,
    save_preferences,
    start_fetch_run,
    upsert_account_measurement,
    upsert_comparison_account,
)
from db.database import get_connection, initialize_database
from meta.client import MetaRequestError
from sync.growth_cycle import (
    GrowthMediaItem,
    GrowthProfile,
    GrowthStepResult,
    GrowthTarget,
    fetch_growth_profile,
    friday_start,
    load_growth_progress,
    tick_growth_cycle,
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
COMPETITORS = (
    "hoog.design",
    "bestinteriors_official",
    "excellent.magazine",
    "vtwonen",
)


def _setup(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "growth_cycle.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *args, **kwargs: False)
    initialize_database(db_path)
    with get_connection() as conn:
        own = upsert_comparison_account(
            conn,
            external_id="ig-own",
            username="theartofliving.online",
            name="TAOL",
            comparison_role=ROLE_OWN,
            is_discovery_host=True,
        )
        for index, username in enumerate(COMPETITORS, start=1):
            upsert_comparison_account(
                conn,
                external_id=f"ig-{index}",
                username=username,
                name=username,
                comparison_role=ROLE_COMPETITOR,
            )
        save_preferences(conn, own_account_id=own, competitors=list(COMPETITORS))
        conn.commit()


def _friday() -> datetime:
    start = friday_start(date(2026, 11, 6))
    assert start.weekday() == 4
    assert start.hour == 0
    return start


class _Script:
    def __init__(self, outcomes: dict[str, list]) -> None:
        self.outcomes = {key: list(value) for key, value in outcomes.items()}
        self.calls: list[str] = []
        self.steps: list[str] = []

    def __call__(self, target: GrowthTarget):
        self.calls.append(target.username)
        self.steps.append(target.step)
        if target.step != "followers":
            return GrowthStepResult(
                posts=(
                    GrowthMediaItem(
                        external_id=f"post-{target.username}",
                        like_count=3,
                        comments_count=1,
                        published_at="2026-11-06T00:00:00+00:00",
                    ),
                ),
                posts_done=True,
            )
        queue = self.outcomes.get(target.username) or []
        outcome = queue.pop(0) if queue else GrowthProfile(followers_count=100)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _measurements() -> list[tuple]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.username, m.id, m.measured_at, m.followers_count
            FROM account_measurements m
            JOIN accounts a ON a.id = m.account_id
            ORDER BY m.id
            """
        ).fetchall()
    return [(row["username"], row["id"], row["measured_at"], row["followers_count"]) for row in rows]


def test_friday_midnight_follows_amsterdam_dst() -> None:
    winter = friday_start(date(2026, 1, 16))
    summer = friday_start(date(2026, 7, 3))
    assert winter.weekday() == 4
    assert summer.weekday() == 4
    assert winter.utcoffset() == timedelta(hours=1)
    assert summer.utcoffset() == timedelta(hours=2)
    assert winter.astimezone(timezone.utc) == datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)
    assert summer.astimezone(timezone.utc) == datetime(2026, 7, 2, 22, 0, tzinfo=timezone.utc)


def test_progress_lists_last_measurement_before_a_cycle(
    tmp_path: Path, monkeypatch
) -> None:
    _setup(tmp_path, monkeypatch)
    moment = datetime(2026, 10, 16, 8, 0, tzinfo=AMSTERDAM)
    with get_connection() as conn:
        account = conn.execute(
            "SELECT id FROM accounts WHERE username = 'theartofliving.online'"
        ).fetchone()
        run_id = start_fetch_run(
            conn,
            trigger_source="manual",
            measured_at=moment.astimezone(timezone.utc).isoformat(),
            period_start=moment.astimezone(timezone.utc).isoformat(),
            period_end=moment.astimezone(timezone.utc).isoformat(),
        )
        upsert_account_measurement(
            conn,
            account_id=int(account["id"]),
            fetch_run_id=run_id,
            measured_at=moment.astimezone(timezone.utc).isoformat(),
            followers_count=122766,
            media_count=None,
        )
        conn.commit()
    progress = load_growth_progress(now=datetime(2026, 11, 5, 10, 0, tzinfo=AMSTERDAM))
    assert progress.cycle_status == "none"
    assert progress.total_count == 5
    own = next(row for row in progress.accounts if row.username == "theartofliving.online")
    assert own.last_followers == 122766
    assert own.status_label == "wachtend"
    assert own.week_growth.label == INSUFFICIENT_GROWTH
    assert "verouderd" in progress.message
    assert "16-10-2026" in progress.message


def test_five_accounts_step_once_per_hour(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script({})
    start = _friday()
    expected = ["theartofliving.online", *COMPETITORS]
    moment = start
    for index, username in enumerate(expected):
        followers = tick_growth_cycle(now=moment, fetcher=script)
        assert followers.action == "stored"
        assert followers.username == username
        paused = tick_growth_cycle(now=moment + timedelta(minutes=30), fetcher=script)
        assert paused.action == "wait"
        moment += timedelta(hours=1)
        posts = tick_growth_cycle(now=moment, fetcher=script)
        assert posts.action == "stored"
        assert posts.username == username
        moment += timedelta(hours=1)
        if index < len(expected) - 1:
            early = tick_growth_cycle(now=moment - timedelta(minutes=30), fetcher=script)
            assert early.action == "wait"

    assert len(_measurements()) == 5
    progress = load_growth_progress(now=moment)
    assert progress.complete_count == 5
    assert progress.total_count == 5
    assert progress.cycle_status == "complete"
    assert script.steps.count("followers") == 5
    assert script.steps.count("posts") == 5


def test_rate_limit_wait_overrides_the_hour(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    start = _friday()
    blocked = (start + timedelta(hours=6)).astimezone(timezone.utc).isoformat()
    script = _Script(
        {
            "theartofliving.online": [
                MetaRequestError(
                    "application limit",
                    error_code=4,
                    is_rate_limited=True,
                    retry_after_seconds=6 * 3600,
                    blocked_until=blocked,
                ),
                GrowthProfile(followers_count=50),
            ]
        }
    )
    first = tick_growth_cycle(now=start, fetcher=script)
    assert first.action == "rate_limit"
    assert script.calls == ["theartofliving.online"]
    assert _measurements() == []

    held = tick_growth_cycle(now=start + timedelta(hours=1), fetcher=script)
    assert held.action == "wait"
    assert script.calls == ["theartofliving.online"]

    resumed = tick_growth_cycle(now=start + timedelta(hours=6), fetcher=script)
    assert resumed.action == "stored"
    assert resumed.username == "theartofliving.online"
    assert script.calls == ["theartofliving.online", "theartofliving.online"]
    stored = _measurements()
    assert len(stored) == 1
    assert stored[0][3] == 50
    with get_connection() as conn:
        status = conn.execute(
            """
            SELECT status, attempts FROM growth_cycle_accounts
            WHERE username = 'theartofliving.online'
            """
        ).fetchone()
    assert status["status"] == "waiting"
    assert int(status["attempts"]) == 0
    posts = tick_growth_cycle(now=start + timedelta(hours=7), fetcher=script)
    assert posts.action == "stored"
    assert posts.username == "theartofliving.online"


def test_restart_keeps_the_original_measurement(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script({})
    start = _friday()
    tick_growth_cycle(now=start, fetcher=script)
    before = _measurements()
    assert len(before) == 1
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE growth_cycle_accounts
            SET status = 'waiting', step = 'fetch_profile'
            WHERE username = 'theartofliving.online'
            """
        )
        conn.commit()

    repaired = tick_growth_cycle(now=start + timedelta(hours=1), fetcher=script)
    assert repaired.action == "local"
    assert script.calls == ["theartofliving.online"]
    after = _measurements()
    assert after == before

    posts = tick_growth_cycle(now=start + timedelta(hours=2), fetcher=script)
    assert posts.username == "theartofliving.online"
    assert script.steps[-1] == "posts"
    own_rows = [row for row in _measurements() if row[0] == "theartofliving.online"]
    assert own_rows == before


def test_second_trigger_does_not_fetch_again(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script({})
    start = _friday()
    first = tick_growth_cycle(now=start, fetcher=script)
    second = tick_growth_cycle(now=start, fetcher=script)
    assert first.action == "stored"
    assert second.action == "wait"
    assert script.calls == ["theartofliving.online"]
    assert len(_measurements()) == 1


def test_parallel_ticks_fetch_once(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script({})
    start = _friday()
    from threading import Thread

    results: list[str] = []

    def _run() -> None:
        result = tick_growth_cycle(now=start, fetcher=script)
        results.append(result.action)

    threads = [Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert script.calls == ["theartofliving.online"]
    assert results.count("stored") == 1
    assert len(_measurements()) == 1


def test_invalid_token_does_not_stop_other_accounts(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script(
        {
            COMPETITORS[0]: [
                MetaRequestError("bad token", error_code=190),
            ]
        }
    )
    start = _friday()
    moment = start
    own_followers = tick_growth_cycle(now=moment, fetcher=script)
    moment += timedelta(hours=1)
    own_posts = tick_growth_cycle(now=moment, fetcher=script)
    assert own_followers.username == own_posts.username == "theartofliving.online"
    moment += timedelta(hours=1)
    broken = tick_growth_cycle(now=moment, fetcher=script)
    assert broken.username == COMPETITORS[0]
    assert broken.action == "error"
    for username in COMPETITORS[1:]:
        moment += timedelta(hours=1)
        followers = tick_growth_cycle(now=moment, fetcher=script)
        moment += timedelta(hours=1)
        posts = tick_growth_cycle(now=moment, fetcher=script)
        assert followers.username == posts.username == username
        assert posts.action == "stored"
    names = [row[0] for row in _measurements()]
    assert COMPETITORS[0] not in names
    assert set(names) == {"theartofliving.online", *COMPETITORS[1:]}
    progress = load_growth_progress(now=moment)
    assert progress.cycle_status == "incomplete"
    failed = next(row for row in progress.accounts if row.username == COMPETITORS[0])
    assert failed.status_label == "fout"
    assert "token" in (failed.error_message or "").lower()


def test_missing_followers_are_not_stored_as_zero(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script({COMPETITORS[1]: [GrowthProfile(followers_count=None)] * 3})
    start = _friday()
    # own, hoog, then bestinteriors fails three times, then the rest.
    moment = start
    for _ in range(2):
        tick_growth_cycle(now=moment, fetcher=script)
        moment += timedelta(hours=1)
    for _ in range(2):
        tick_growth_cycle(now=moment, fetcher=script)
        moment += timedelta(hours=1)
    for _ in range(3):
        result = tick_growth_cycle(now=moment, fetcher=script)
        assert result.username == COMPETITORS[1]
        moment += timedelta(hours=1)
    assert result.action == "error"
    tick_growth_cycle(now=moment, fetcher=script)
    moment += timedelta(hours=1)
    tick_growth_cycle(now=moment, fetcher=script)
    moment += timedelta(hours=1)
    tick_growth_cycle(now=moment, fetcher=script)
    moment += timedelta(hours=1)
    tick_growth_cycle(now=moment, fetcher=script)
    rows = _measurements()
    assert COMPETITORS[1] not in [row[0] for row in rows]
    assert all(row[3] != 0 or row[0] != COMPETITORS[1] for row in rows)
    assert all(row[3] is not None for row in rows)
    progress = load_growth_progress(now=moment)
    missing = next(row for row in progress.accounts if row.username == COMPETITORS[1])
    assert missing.week_growth.label == INSUFFICIENT_GROWTH
    assert missing.status_label == "fout"


def test_resume_after_friday_does_not_duplicate(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script({})
    start = _friday()
    tick_growth_cycle(now=start, fetcher=script)
    tick_growth_cycle(now=start + timedelta(hours=1), fetcher=script)
    tick_growth_cycle(now=start + timedelta(hours=2), fetcher=script)
    before = _measurements()
    assert [row[0] for row in before] == ["theartofliving.online", COMPETITORS[0]]

    saturday = start + timedelta(days=1, hours=1)
    resumed = tick_growth_cycle(now=saturday, fetcher=script)
    assert resumed.username == COMPETITORS[0]
    assert script.steps[-1] == "posts"
    kept = [row for row in _measurements() if row[0] == "theartofliving.online"]
    assert kept == [before[0]]
    progress = load_growth_progress(now=saturday)
    assert progress.cycle_status == "incomplete"
    assert "niet volledig" in progress.message
    assert progress.complete_count == 2


def test_other_meta_job_defers_growth(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script({})
    start = _friday()
    with get_connection() as conn:
        start_fetch_run(
            conn,
            trigger_source="schedule_afternoon_comparison",
            measured_at=start.astimezone(timezone.utc).isoformat(),
            period_start=start.astimezone(timezone.utc).isoformat(),
            period_end=start.astimezone(timezone.utc).isoformat(),
        )
        conn.commit()
    result = tick_growth_cycle(now=start, fetcher=script)
    assert result.action == "deferred"
    assert script.calls == []
    assert _measurements() == []


def test_profile_fetch_does_not_request_media(monkeypatch) -> None:
    monkeypatch.setattr("sync.growth_cycle.MetaClient.from_settings", lambda: object())
    seen: dict = {}

    def own(_client, ig_id):
        seen["own"] = ig_id
        return {"id": ig_id, "followers_count": 5, "username": "theartofliving.online"}

    def discovery(_client, host, username, **kwargs):
        seen["discovery"] = kwargs
        seen["host"] = host
        return {"id": "ig-x", "followers_count": 9, "username": username}, object()

    monkeypatch.setattr("sync.growth_cycle.fetch_own_account_profile", own)
    monkeypatch.setattr("sync.growth_cycle.fetch_business_discovery", discovery)
    own_profile = fetch_growth_profile(
        GrowthTarget(1, 1, "theartofliving.online", "own", "ig-own", "ig-host")
    )
    competitor = fetch_growth_profile(
        GrowthTarget(2, 2, "hoog.design", "competitor", None, "ig-host")
    )
    assert own_profile.followers_count == 5
    assert seen["own"] == "ig-own"
    assert competitor.followers_count == 9
    assert seen["discovery"]["include_media"] is False
    assert seen["host"] == "ig-host"


def test_week_growth_gap_and_insufficient() -> None:
    assert describe_week_growth([]).label == INSUFFICIENT_GROWTH
    assert (
        describe_week_growth(
            [WeekMeasurement("2026-11-06T00:00:00+00:00", 1000, "2026-11-06")]
        ).label
        == INSUFFICIENT_GROWTH
    )
    week = describe_week_growth(
        [
            WeekMeasurement("2026-11-06T00:10:00+00:00", 1000, "2026-11-06"),
            WeekMeasurement("2026-11-13T00:12:00+00:00", 1012, "2026-11-13"),
        ],
        expected_weeks=["2026-11-06", "2026-11-13"],
    )
    assert week.kind == "week"
    assert week.absolute == 12
    assert week.percent == pytest.approx(1.2)
    assert "Datagat" not in week.label

    gap = describe_week_growth(
        [
            WeekMeasurement("2026-10-16T06:59:41+00:00", 28645, None),
            WeekMeasurement("2026-11-06T00:10:00+00:00", 28700, "2026-11-06"),
        ],
        expected_weeks=["2026-10-30", "2026-11-06"],
    )
    assert gap.kind == "gap"
    assert gap.absolute == 55
    assert "Datagat" in gap.label
    assert "21 dagen" in gap.label
    assert gap.percent is not None

    missing_middle = describe_week_growth(
        [
            WeekMeasurement("2026-11-06T00:00:00+00:00", 10, "2026-11-06"),
            WeekMeasurement("2026-11-20T00:00:00+00:00", 14, "2026-11-20"),
        ],
        expected_weeks=["2026-11-06", "2026-11-13", "2026-11-20"],
    )
    assert missing_middle.kind == "gap"
    assert "14 dagen" in missing_middle.label

    zero_base = describe_week_growth(
        [
            WeekMeasurement("2026-11-06T00:00:00+00:00", 0, "2026-11-06"),
            WeekMeasurement("2026-11-13T00:00:00+00:00", 4, "2026-11-13"),
        ]
    )
    assert zero_base.percent is None
    assert "n.b." in zero_base.label


def test_missed_friday_creates_exactly_one_cycle(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script({})
    thursday = datetime(2026, 11, 5, 10, 0, tzinfo=AMSTERDAM)
    first = tick_growth_cycle(now=thursday, fetcher=script)
    second = tick_growth_cycle(now=thursday, fetcher=script)
    assert first.action == "stored"
    assert first.username == "theartofliving.online"
    assert second.action == "wait"
    assert script.calls == ["theartofliving.online"]

    friday_night = datetime(2026, 11, 6, 0, 30, tzinfo=AMSTERDAM)
    continued = tick_growth_cycle(now=friday_night, fetcher=script)
    assert continued.action == "stored"
    assert continued.username == "theartofliving.online"
    assert script.steps == ["followers", "posts"]
    with get_connection() as conn:
        weeks = [
            row["week_start"]
            for row in conn.execute("SELECT week_start FROM growth_cycles ORDER BY id")
        ]
    assert weeks == ["2026-10-30"]

    with get_connection() as conn:
        conn.execute("UPDATE growth_cycle_accounts SET status = 'complete', step = 'stored'")
        conn.execute("UPDATE growth_cycles SET status = 'complete', next_run_at = NULL")
        conn.commit()
    opened = tick_growth_cycle(now=friday_night, fetcher=script)
    again = tick_growth_cycle(now=friday_night, fetcher=script)
    assert opened.action == "stored"
    assert again.action == "wait"
    with get_connection() as conn:
        weeks = [
            row["week_start"]
            for row in conn.execute("SELECT week_start FROM growth_cycles ORDER BY id")
        ]
    assert weeks == ["2026-10-30", "2026-11-06"]


def test_missing_likes_stay_pending_until_a_later_step(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    calls: list[str] = []

    def fetcher(target: GrowthTarget):
        calls.append(target.step)
        if target.step == "followers":
            return GrowthProfile(followers_count=12)
        if calls.count("posts") == 1:
            return GrowthStepResult(
                posts=(GrowthMediaItem("post-1", None, 2, "2026-11-06T00:00:00+00:00"),),
                posts_done=True,
            )
        return GrowthStepResult(
            posts=(GrowthMediaItem("post-1", 4, 2, "2026-11-06T00:00:00+00:00"),),
            posts_done=True,
        )

    start = _friday()
    tick_growth_cycle(now=start, fetcher=fetcher)
    missing = tick_growth_cycle(now=start + timedelta(hours=1), fetcher=fetcher)
    assert missing.action == "retry"
    filled = tick_growth_cycle(now=start + timedelta(hours=2), fetcher=fetcher)
    assert filled.action == "stored"
    with get_connection() as conn:
        rows = conn.execute("SELECT external_id FROM posts").fetchall()
        likes = conn.execute("SELECT like_count, comments_count FROM post_measurements").fetchall()
        status = conn.execute(
            "SELECT status FROM growth_cycle_accounts WHERE username = 'theartofliving.online'"
        ).fetchone()
    assert [row["external_id"] for row in rows] == ["post-1"]
    assert likes[0]["like_count"] == 4
    assert likes[0]["comments_count"] == 2
    assert status["status"] == "complete"


def test_post_rate_limit_resumes_the_same_step(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    blocked = (_friday() + timedelta(hours=4)).astimezone(timezone.utc).isoformat()
    calls: list[str] = []

    def fetcher(target: GrowthTarget):
        calls.append(target.step)
        if target.step == "followers":
            return GrowthProfile(followers_count=12)
        if len([step for step in calls if step == "posts"]) == 1:
            raise MetaRequestError(
                "application limit",
                error_code=4,
                is_rate_limited=True,
                blocked_until=blocked,
            )
        return GrowthStepResult(
            posts=(GrowthMediaItem("post-1", 1, 1, "2026-11-06T00:00:00+00:00"),),
            posts_done=True,
        )

    start = _friday()
    tick_growth_cycle(now=start, fetcher=fetcher)
    limited = tick_growth_cycle(now=start + timedelta(hours=1), fetcher=fetcher)
    assert limited.action == "rate_limit"
    held = tick_growth_cycle(now=start + timedelta(hours=2), fetcher=fetcher)
    assert held.action == "wait"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, attempts FROM growth_cycle_accounts WHERE username = 'theartofliving.online'"
        ).fetchone()
    assert row["status"] == "waiting_api"
    assert int(row["attempts"]) == 0
    done = tick_growth_cycle(now=start + timedelta(hours=4), fetcher=fetcher)
    assert done.action == "stored"
    assert done.username == "theartofliving.online"
