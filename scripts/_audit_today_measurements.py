"""No-Meta audit: schedule, rate-limit, today's measurements, active runs."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.comparison_repository import (
    discovery_requests_blocked,
    find_active_fetch_run,
    get_discovery_rate_limit,
    get_preferences,
    list_comparison_accounts,
)
from db.database import get_connection

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
TODAY = date(2026, 10, 16)
YESTERDAY = date(2026, 10, 15)


def _ams_day(ts: str | None) -> str | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(AMSTERDAM).date().isoformat()
    except ValueError:
        return None


def main() -> None:
    print("today_ams", TODAY.isoformat())
    logs = sorted((ROOT / "logs").glob("daily_comparison_*.log")) if (ROOT / "logs").exists() else []
    print("daily_logs", len(logs))
    for path in logs[-8:]:
        print("\n---", path.name, "---")
        text = path.read_text(encoding="utf-8", errors="replace")
        # Print key lines only
        for line in text.splitlines():
            low = line.lower()
            if any(
                key in low
                for key in (
                    "started_at",
                    "finished_at",
                    "exit_code",
                    "preflight",
                    "rate",
                    "status=",
                    "@",
                    "error",
                    "active",
                    "blocked",
                    "meta-aanvraag",
                    "hervat",
                    "ophalen",
                )
            ):
                print(line[:240])

    with get_connection() as conn:
        print("\nactive_run", find_active_fetch_run(conn))
        print("rate_blocked", discovery_requests_blocked(conn))
        state = get_discovery_rate_limit(conn)
        print("rate_state", state)
        if state and state.usage_json:
            try:
                print("usage_parsed", json.loads(state.usage_json))
            except json.JSONDecodeError:
                print("usage_raw", state.usage_json)

        runs = conn.execute(
            """
            SELECT id, started_at, finished_at, status, trigger_source, measured_at, error_message
            FROM comparison_fetch_runs
            ORDER BY id DESC LIMIT 12
            """
        ).fetchall()
        print("\nrecent_runs:")
        for r in runs:
            print(dict(r))

        own_id, competitors = get_preferences(conn)
        print("\nown_id", own_id, "competitors", competitors)
        accounts = list_comparison_accounts(conn)
        selected = []
        for a in accounts:
            if a.comparison_role == "own" and (own_id is None or a.account_id == own_id):
                selected.append(a)
            elif a.comparison_role == "competitor" and (a.username or "").casefold() in {
                u.casefold() for u in competitors
            }:
                selected.append(a)

        print("\n=== per account today/yesterday ===")
        for a in selected:
            aid = a.account_id
            fol = conn.execute(
                """
                SELECT measured_at, followers_count
                FROM account_measurements
                WHERE account_id = ?
                ORDER BY measured_at DESC, id DESC
                LIMIT 5
                """,
                (aid,),
            ).fetchall()
            post_days = conn.execute(
                """
                SELECT substr(pm.measured_at, 1, 10) AS d, COUNT(*) AS c
                FROM post_measurements pm
                JOIN posts p ON p.id = pm.post_id
                WHERE p.account_id = ?
                GROUP BY d
                ORDER BY d DESC
                LIMIT 5
                """,
                (aid,),
            ).fetchall()
            # Amsterdam day buckets for followers
            fol_ams: dict[str, list] = {}
            for row in fol:
                day = _ams_day(row["measured_at"])
                if day:
                    fol_ams.setdefault(day, []).append(dict(row))
            last_res = conn.execute(
                """
                SELECT ar.status, ar.is_complete, ar.posts_fetched, ar.error_message,
                       r.measured_at, r.id AS run_id, r.trigger_source
                FROM comparison_fetch_account_results ar
                JOIN comparison_fetch_runs r ON r.id = ar.fetch_run_id
                WHERE ar.account_id = ?
                ORDER BY ar.id DESC LIMIT 3
                """,
                (aid,),
            ).fetchall()
            cp = conn.execute(
                """
                SELECT pages_fetched, posts_fetched, is_complete, updated_at
                FROM comparison_discovery_checkpoints WHERE account_id = ?
                """,
                (aid,),
            ).fetchone()
            print(f"\n@{a.username} id={aid} role={a.comparison_role}")
            print("  follower_samples", [dict(x) for x in fol])
            print("  follower_days_ams", sorted(fol_ams.keys()))
            print("  has_followers_today", TODAY.isoformat() in fol_ams)
            print("  has_followers_yesterday", YESTERDAY.isoformat() in fol_ams)
            print("  post_metric_days_utc", [dict(x) for x in post_days])
            print("  last_results", [dict(x) for x in last_res])
            print("  checkpoint", dict(cp) if cp else None)

        # Running runs without finished_at
        open_runs = conn.execute(
            """
            SELECT id, started_at, status, trigger_source
            FROM comparison_fetch_runs
            WHERE finished_at IS NULL OR status = 'running'
            ORDER BY id DESC
            """
        ).fetchall()
        print("\nopen_or_running_runs", [dict(r) for r in open_runs])


if __name__ == "__main__":
    main()
