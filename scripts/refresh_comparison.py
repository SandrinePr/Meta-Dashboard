"""CLI: refresh Instagram comparison metrics (own + competitors).

Intended for manual runs or an external daily cron/scheduler.
This command does not start a background scheduler by itself.

Usage:
  python scripts/refresh_comparison.py
  python scripts/refresh_comparison.py --force
  python scripts/refresh_comparison.py --mode growth
  python scripts/refresh_comparison.py --resume-hoog
  python scripts/refresh_comparison.py --resume bestinteriors_official
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPTS_DIR.parent
for _path in (_SCRIPTS_DIR, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from dotenv import load_dotenv

from config import get_settings
from db.comparison_repository import (
    discovery_requests_blocked,
    find_active_fetch_run,
    get_discovery_host,
    get_preferences,
    list_comparison_accounts,
    list_open_discovery_checkpoints,
    normalize_username,
)
from db.database import get_connection, initialize_database
from sync.comparison import (
    _order_competitors,
    resume_account_fetch,
    resume_hoog_design_fetch,
    run_comparison_sync,
)


def run_preflight() -> int:
    """Validate paths/DB/overlap/checkpoints without any Meta API calls."""
    settings = get_settings()
    db_path = settings.database_path.resolve()
    print(f"preflight=ok meta_calls=none")
    print(f"cwd={Path.cwd()}")
    print(f"database_path={db_path}")
    print(f"database_exists={db_path.is_file()}")
    if not db_path.is_file():
        print("error: database file missing")
        return 1

    initialize_database()
    with get_connection() as conn:
        active = find_active_fetch_run(conn)
        blocked = discovery_requests_blocked(conn)
        host = get_discovery_host(conn)
        own_id, competitors = get_preferences(conn)
        open_cps = list_open_discovery_checkpoints(conn)
        accounts = list_comparison_accounts(conn)
        conn.commit()

    print(f"active_run={active}")
    print(f"retry_after_blocked={blocked is not None}")
    if blocked is not None:
        print(f"blocked_until_utc={blocked.blocked_until}")
    print(
        f"discovery_host="
        f"{None if host is None else (host.username or host.external_id)}"
    )
    print(f"own_account_id={own_id}")
    print(f"competitors={competitors}")

    open_names = {normalize_username(cp.username) for cp in open_cps}
    print(f"open_checkpoints={sorted(open_names)}")
    for cp in open_cps:
        print(
            f"  checkpoint @{cp.username}: posts={cp.posts_fetched} "
            f"pages={cp.pages_fetched} complete={cp.is_complete}"
        )

    planned_own = next(
        (a for a in accounts if own_id is not None and a.account_id == own_id),
        None,
    )
    planned = []
    if planned_own is not None and planned_own.username:
        planned.append(("own", normalize_username(planned_own.username), "fresh"))
    for username in _order_competitors(competitors):
        mode = "resume_incomplete" if username in open_names else "fresh_remeasure"
        planned.append(("competitor", username, mode))

    print("planned_targets:")
    for role, username, mode in planned:
        blocked_by_other_cp = False
        print(
            f"  {role} @{username}: {mode}"
            f"{' (other open checkpoints do not block this target)' if not blocked_by_other_cp else ''}"
        )

    other_fresh = [
        username
        for role, username, mode in planned
        if mode == "fresh_remeasure"
    ]
    if open_names and other_fresh:
        print(
            "checkpoint_isolation=ok "
            f"open={sorted(open_names)} still_planned_fresh={other_fresh}"
        )
    elif open_names:
        print("checkpoint_isolation=ok open_checkpoint_exists no_other_fresh_targets")
    else:
        print("checkpoint_isolation=ok no_open_checkpoints")

    if active is not None:
        print("overlap_guard=would_skip_run (active fetch run present)")
        return 2
    if blocked is not None:
        print("overlap_guard=would_skip_run (Retry-After still active)")
        return 2
    print("overlap_guard=clear")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh social comparison Instagram data.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Start even if another run is marked as running. Does not bypass Meta Retry-After.",
    )
    parser.add_argument(
        "--source",
        default="cli",
        help="Trigger source label stored on the fetch run (default: cli).",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Only fetch these usernames (repeatable). Use 'own' for eigen account.",
    )
    parser.add_argument(
        "--skip-own",
        action="store_true",
        help="Do not refresh the own comparison account.",
    )
    parser.add_argument(
        "--resume-hoog",
        action="store_true",
        help=(
            "Controlled resume: one Business Discovery request for @hoog.design only. "
            "No own-account sync, no automatic retries. Not run automatically."
        ),
    )
    parser.add_argument(
        "--resume",
        metavar="USERNAME",
        default=None,
        help=(
            "Controlled resume for one competitor username with open checkpoint. "
            "No own-account sync, no automatic retries."
        ),
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help=(
            "Validate working directory, DATABASE_PATH, overlap guards and "
            "checkpoint isolation without any Meta requests."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("growth", "full"),
        default="full",
        help="growth=follower snapshots only; full=profiles + posts (default).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(override=True)

    if args.preflight:
        return run_preflight()

    initialize_database()

    def progress(message: str, fraction: float | None = None) -> None:
        if fraction is None:
            print(message)
        else:
            print(f"[{fraction:0.0%}] {message}")

    if args.resume:
        result = resume_account_fetch(
            args.resume,
            trigger_source=(
                args.source
                if args.source != "cli"
                else f"cli_resume_{args.resume}"
            ),
            progress=progress,
        )
    elif args.resume_hoog:
        result = resume_hoog_design_fetch(
            trigger_source=args.source if args.source != "cli" else "cli_resume_hoog",
            progress=progress,
        )
    else:
        result = run_comparison_sync(
            trigger_source=args.source,
            progress=progress,
            force=args.force,
            only_usernames=args.only or None,
            include_own=not args.skip_own,
            mode=args.mode,
        )
    print(f"status={result.status} run_id={result.run_id}")
    if result.rate_limited:
        print("rate_limited=yes")
        if result.rate_limit_blocked_until:
            print(f"blocked_until_utc={result.rate_limit_blocked_until}")
        else:
            print("resetmoment=onbekend")
        if result.last_success_measured_at:
            print(f"last_success_measured_at={result.last_success_measured_at}")
    if result.skipped_reason:
        print(f"skipped: {result.skipped_reason}")
    if result.error_message:
        print(f"error: {result.error_message}")
    for account in result.accounts:
        completeness = "compleet" if account.is_complete else "onvolledig"
        print(
            f"  @{account.username}: {account.status} posts={account.posts_fetched} "
            f"{completeness}"
            + (f" err={account.error_message}" if account.error_message else "")
        )
        if account.status == "partial":
            print("    (pagina’s bewaard; hervatten mogelijk vanaf checkpoint)")
        if account.oldest_post_at or account.newest_post_at:
            print(
                f"    periode posts: {account.oldest_post_at} .. {account.newest_post_at}"
            )

    if result.rate_limited:
        return 3
    if result.status in {"completed", "completed_with_errors"}:
        return 0
    if result.status == "skipped":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
