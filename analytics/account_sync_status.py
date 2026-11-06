"""Laatste update for the weekly comparison cycle.

Pending while a step is waiting or being retried, including a Meta rate limit.
The pending tooltip names the current step and the next attempt. Mislukt only
after a permanent error or exhausted retries. A date appears when followers,
posts, likes and comments of that same cycle are stored.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

LABEL_PENDING = "Pending"
LABEL_FAILED = "Mislukt"
AMSTERDAM = ZoneInfo("Europe/Amsterdam")


@dataclass(frozen=True, slots=True)
class AccountSyncState:
    kind: str
    measured_at: str | None = None
    error_message: str | None = None
    step_label: str | None = None
    next_attempt: str | None = None
    waiting_on_limit: bool = False


def pending_title(state: AccountSyncState | None) -> str:
    step = (state.step_label if state is not None else None) or "wachten"
    if state is not None and state.waiting_on_limit:
        step = f"wachten op Meta-limiet ({step})"
    when = (state.next_attempt if state is not None else None) or "de eerstvolgende beurt"
    return f"Huidige stap: {step}. Volgende poging: {when}."


def load_account_sync_states(
    conn: sqlite3.Connection,
    account_ids: list[int],
) -> dict[int, AccountSyncState]:
    return {account_id: _state_for(conn, account_id) for account_id in account_ids}


def _state_for(conn: sqlite3.Connection, account_id: int) -> AccountSyncState:
    row = conn.execute(
        """
        SELECT gca.status, gca.error_message, gca.measured_at, gca.measurement_id,
               gca.step, gca.next_run_at, gc.next_run_at AS cycle_next_run_at
        FROM growth_cycle_accounts gca
        JOIN growth_cycles gc ON gc.id = gca.cycle_id
        WHERE gca.account_id = ?
        ORDER BY gc.id DESC, gca.id DESC
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    if row is None:
        return AccountSyncState(kind="pending", step_label="wachten")
    status = str(row["status"] or "")
    if status == "error":
        message = row["error_message"]
        return AccountSyncState(
            kind="failed",
            error_message=str(message) if message else "Ophalen mislukt",
        )
    if status == "complete" and _cycle_data_stored(conn, row["measurement_id"]):
        return AccountSyncState(kind="stored", measured_at=str(row["measured_at"] or ""))
    attempt = row["next_run_at"] or row["cycle_next_run_at"]
    return AccountSyncState(
        kind="pending",
        step_label=_step_label(row["step"]),
        next_attempt=_fmt_attempt(attempt),
        waiting_on_limit=status == "waiting_api",
        error_message=str(row["error_message"]) if row["error_message"] else None,
    )


def _step_label(step: str | None) -> str:
    if step in {"posts", "engagement"}:
        return "postpagina ophalen"
    if step == "stored":
        return "opgeslagen"
    return "volgers ophalen"


def _fmt_attempt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(AMSTERDAM).strftime("%d-%m-%Y %H:%M")


def _cycle_data_stored(conn: sqlite3.Connection, measurement_id: int | None) -> bool:
    if measurement_id is None:
        return False
    measurement = conn.execute(
        """
        SELECT fetch_run_id, followers_count
        FROM account_measurements
        WHERE id = ?
        """,
        (int(measurement_id),),
    ).fetchone()
    if measurement is None or measurement["followers_count"] is None:
        return False
    missing = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM post_measurements
        WHERE fetch_run_id = ?
          AND (like_count IS NULL OR comments_count IS NULL)
        """,
        (int(measurement["fetch_run_id"]),),
    ).fetchone()
    return int(missing["n"] or 0) == 0
