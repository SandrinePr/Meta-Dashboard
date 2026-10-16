"""DEPRECATED helper — do not use for imports.

Earlier builds mixed V2 measurements with Render-log reconstructions that used
log wall-clock times as measured_at. That is not allowed.

Keep quarantine copies under data/backups/ for audit only.
Import path: merge V2 SQLite measurements into a secured live target via
scripts/merge_comparison_history.py (live-first, no overwrite).
"""

from __future__ import annotations


def main() -> None:
    raise SystemExit(
        "Deprecated: do not import log-time reconstructions. "
        "Use scripts/merge_comparison_history.py with V2 as --source and a "
        "backed-up live DB as --target."
    )


if __name__ == "__main__":
    main()
