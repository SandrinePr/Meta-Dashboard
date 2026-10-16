"""Tests for offline comparison-history merge (no Meta API)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.database import initialize_database  # noqa: E402


@pytest.fixture()
def merge_history(monkeypatch):
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *a, **k: False)
    from scripts.merge_comparison_history import merge_history as _merge

    return _merge


def _seed_source(db_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *a, **k: False)
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO accounts (
                platform, external_id, username, name, comparison_role
            ) VALUES ('instagram', 'ext-own', 'own.account', 'Own', 'own')
            """
        )
        conn.execute(
            """
            INSERT INTO comparison_fetch_runs (
                started_at, finished_at, status, trigger_source, measured_at
            ) VALUES ('2026-10-14T10:00:00+00:00', '2026-10-14T10:01:00+00:00',
                      'completed', 'manual', '2026-10-14T10:00:00+00:00')
            """
        )
        account_id = conn.execute(
            "SELECT id FROM accounts WHERE external_id = 'ext-own'"
        ).fetchone()[0]
        run_id = conn.execute(
            "SELECT id FROM comparison_fetch_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO account_measurements (
                account_id, fetch_run_id, measured_at, followers_count, media_count
            ) VALUES (?, ?, '2026-10-14T10:00:00+00:00', 100, 5)
            """,
            (account_id, run_id),
        )
        conn.execute(
            """
            INSERT INTO comparison_preferences (
                id, own_account_id, competitor_usernames_json, updated_at
            ) VALUES (1, ?, '["comp.one"]', '2026-10-14T10:00:00+00:00')
            """,
            (account_id,),
        )
        conn.commit()


def _seed_target_with_newer(db_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setattr("db.database.ensure_seed_database", lambda *a, **k: False)
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO accounts (
                platform, external_id, username, name, comparison_role
            ) VALUES ('instagram', 'ext-own', 'own.account', 'Own', 'own')
            """
        )
        conn.execute(
            """
            INSERT INTO comparison_fetch_runs (
                started_at, finished_at, status, trigger_source, measured_at
            ) VALUES ('2026-10-16T08:00:00+00:00', '2026-10-16T08:01:00+00:00',
                      'completed', 'daily', '2026-10-16T08:00:00+00:00')
            """
        )
        account_id = conn.execute(
            "SELECT id FROM accounts WHERE external_id = 'ext-own'"
        ).fetchone()[0]
        run_id = conn.execute(
            "SELECT id FROM comparison_fetch_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO account_measurements (
                account_id, fetch_run_id, measured_at, followers_count, media_count
            ) VALUES (?, ?, '2026-10-16T08:00:00+00:00', 120, 6)
            """,
            (account_id, run_id),
        )
        conn.commit()


def test_merge_imports_missing_keeps_live(tmp_path: Path, monkeypatch, merge_history) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _seed_source(source, monkeypatch)
    _seed_target_with_newer(target, monkeypatch)

    stats = merge_history(source, target)
    assert stats.account_measurements_inserted == 1
    assert stats.account_measurements_skipped == 0

    with sqlite3.connect(target) as conn:
        rows = conn.execute(
            """
            SELECT measured_at, followers_count
            FROM account_measurements
            ORDER BY measured_at
            """
        ).fetchall()
    assert rows == [
        ("2026-10-14T10:00:00+00:00", 100),
        ("2026-10-16T08:00:00+00:00", 120),
    ]


def test_merge_is_idempotent(tmp_path: Path, monkeypatch, merge_history) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _seed_source(source, monkeypatch)
    _seed_target_with_newer(target, monkeypatch)

    merge_history(source, target)
    stats = merge_history(source, target)
    assert stats.account_measurements_inserted == 0
    assert stats.account_measurements_skipped == 1

    with sqlite3.connect(target) as conn:
        count = conn.execute("SELECT COUNT(*) FROM account_measurements").fetchone()[0]
    assert count == 2
