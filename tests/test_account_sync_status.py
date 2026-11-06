"""Laatste update follows one weekly cycle, not an older follower snapshot."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from analytics.account_sync_status import LABEL_FAILED, LABEL_PENDING, load_account_sync_states
from db.database import get_connection
from tests.test_growth_cycle import (
    _Script,
    _friday,
    _setup,
)
from sync.growth_cycle import tick_growth_cycle
from ui.comparison import _last_update_cell


def test_column_stays_pending_until_the_cycle_is_complete(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    script = _Script({})
    start = _friday()
    tick_growth_cycle(now=start, fetcher=script)
    with get_connection() as conn:
        account_id = int(
            conn.execute(
                "SELECT id FROM accounts WHERE username = 'theartofliving.online'"
            ).fetchone()["id"]
        )
        after_followers = load_account_sync_states(conn, [account_id])[account_id]
    assert after_followers.kind == "pending"
    pending = _last_update_cell(after_followers)
    assert LABEL_PENDING in pending
    assert "postpagina ophalen" in pending
    assert "06-11-2026 02:00" in pending

    finished = start + timedelta(hours=1)
    tick_growth_cycle(now=finished, fetcher=script)
    with get_connection() as conn:
        state = load_account_sync_states(conn, [account_id])[account_id]
    assert state.kind == "stored"
    assert state.measured_at is not None
    assert _last_update_cell(state) == "06-11-2026 02:00"


def test_rate_limit_is_pending_and_a_permanent_error_is_failed(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    from datetime import timezone
    from meta.client import MetaRequestError

    start = _friday()
    blocked = (start + timedelta(hours=3)).astimezone(timezone.utc).isoformat()
    script = _Script(
        {
            "theartofliving.online": [
                MetaRequestError(
                    "application limit",
                    error_code=4,
                    is_rate_limited=True,
                    blocked_until=blocked,
                )
            ],
            "hoog.design": [MetaRequestError("bad token", error_code=190)],
        }
    )
    tick_growth_cycle(now=start, fetcher=script)
    with get_connection() as conn:
        own_id = int(
            conn.execute(
                "SELECT id FROM accounts WHERE username = 'theartofliving.online'"
            ).fetchone()["id"]
        )
        own = load_account_sync_states(conn, [own_id])[own_id]
    assert own.kind == "pending"
    pending = _last_update_cell(own)
    assert LABEL_PENDING in pending
    assert "Meta-limiet" in pending
    assert "volgers ophalen" in pending
    assert "06-11-2026 04:00" in pending

    tick_growth_cycle(now=start + timedelta(hours=3), fetcher=script)
    tick_growth_cycle(now=start + timedelta(hours=4), fetcher=script)
    failed = tick_growth_cycle(now=start + timedelta(hours=5), fetcher=script)
    assert failed.action == "error"
    with get_connection() as conn:
        hoog_id = int(
            conn.execute("SELECT id FROM accounts WHERE username = 'hoog.design'").fetchone()["id"]
        )
        state = load_account_sync_states(conn, [hoog_id])[hoog_id]
    assert state.kind == "failed"
    assert "token" in (state.error_message or "").lower()
    assert "title=" in _last_update_cell(state)
    assert LABEL_FAILED in _last_update_cell(state)
