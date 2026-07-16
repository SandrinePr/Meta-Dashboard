"""Rebuild search_index for existing synced data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.database import get_connection, initialize_database
from db.repository import rebuild_search_index


def main() -> None:
    initialize_database()
    with get_connection() as conn:
        rebuild_search_index(conn)
        conn.commit()
    print("search_index rebuilt successfully.")


if __name__ == "__main__":
    main()
