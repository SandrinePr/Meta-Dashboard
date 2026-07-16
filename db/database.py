"""SQLite database helpers for schema initialization and connections."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from config import get_settings


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Create a SQLite connection with foreign keys enabled."""
    settings = get_settings()
    resolved_path = db_path or settings.database_path
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(resolved_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def initialize_database(db_path: Path | None = None, schema_path: Path | None = None) -> None:
    """Create all tables and indexes from schema.sql."""
    settings = get_settings()
    effective_db_path = db_path or settings.database_path
    effective_schema_path = schema_path or Path(__file__).with_name("schema.sql")

    schema_sql = effective_schema_path.read_text(encoding="utf-8")
    with get_connection(effective_db_path) as conn:
        conn.executescript(schema_sql)
