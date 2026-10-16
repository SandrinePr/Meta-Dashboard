"""Lightweight SQLite migrations for comparison features."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

ACCOUNT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("comparison_role", "TEXT"),
    ("is_discovery_host", "INTEGER NOT NULL DEFAULT 0"),
    ("access_status", "TEXT NOT NULL DEFAULT 'ok'"),
    ("last_access_error", "TEXT"),
    ("updated_at", "TEXT"),
)

POST_COLUMNS: tuple[tuple[str, str], ...] = (
    ("media_product_type", "TEXT"),
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def ensure_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    existing = _existing_columns(conn, table)
    for name, definition in columns:
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        logger.info("Added column %s.%s", table, name)


def apply_comparison_schema(conn: sqlite3.Connection) -> None:
    """Add comparison columns/tables without deleting existing data."""
    ensure_columns(conn, "accounts", ACCOUNT_COLUMNS)
    ensure_columns(conn, "posts", POST_COLUMNS)
    schema_path = Path(__file__).with_name("schema_comparison.sql")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
