"""Run one fetch slot: a weekly-growth step, afternoon comparison, or evening refresh."""

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
    run_growth_tick,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a scheduled dashboard fetch job.")
    parser.add_argument(
        "slot",
        choices=("growth", "morning", "afternoon", "evening"),
        help="growth=één weekstap; morning is alleen diezelfde stap, geen tweede dagelijkse growth",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(ROOT / ".env", override=True)
    initialize_database()
    if args.slot in {"growth", "morning"}:
        run_growth_tick()
    elif args.slot == "afternoon":
        run_afternoon_comparison()
    else:
        run_evening_refresh()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
