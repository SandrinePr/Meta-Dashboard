"""Run one of the three daily fetch jobs (morning / afternoon / evening)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from db.database import initialize_database
from sync.scheduler import (
    run_afternoon_comparison,
    run_evening_refresh,
    run_morning_growth,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a scheduled dashboard fetch job.")
    parser.add_argument(
        "slot",
        choices=("morning", "afternoon", "evening"),
        help="morning=growth, afternoon=social vergelijking, evening=full refresh",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(ROOT / ".env", override=True)
    initialize_database()
    if args.slot == "morning":
        run_morning_growth()
    elif args.slot == "afternoon":
        run_afternoon_comparison()
    else:
        run_evening_refresh()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
