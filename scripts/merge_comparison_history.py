"""Merge comparison measurements from a source SQLite DB into a target DB.

No Meta API calls. Preserves existing target measurements; imports only missing
real measurements with their original measured_at timestamps.

Dedupe keys:
  account_measurements — (platform, account.external_id, measured_at)
  post_measurements    — (platform, post.external_id, measured_at)

Usage:
  python scripts/merge_comparison_history.py \\
    --source path/to/v2_social_search.db \\
    --target path/to/live_social_search.db \\
    --backup-dir data/backups
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.database import initialize_database  # noqa: E402
from db.migrate import apply_comparison_schema  # noqa: E402


@dataclass
class MergeStats:
    fetch_runs_inserted: int = 0
    fetch_runs_skipped: int = 0
    accounts_upserted: int = 0
    posts_upserted: int = 0
    account_measurements_inserted: int = 0
    account_measurements_skipped: int = 0
    post_measurements_inserted: int = 0
    post_measurements_skipped: int = 0
    account_results_inserted: int = 0
    account_results_skipped: int = 0
    preferences_updated: bool = False


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _backup(path: Path, backup_dir: Path, label: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{label}_{stamp}.db"
    shutil.copy2(path, dest)
    return dest


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _ensure_account(target: sqlite3.Connection, src_row: sqlite3.Row, stats: MergeStats) -> int:
    existing = target.execute(
        """
        SELECT id FROM accounts
        WHERE platform = ? AND external_id = ?
        """,
        (src_row["platform"], src_row["external_id"]),
    ).fetchone()
    if existing:
        # Refresh useful comparison fields without wiping live data.
        target.execute(
            """
            UPDATE accounts SET
                name = COALESCE(?, name),
                username = COALESCE(?, username),
                page_id = COALESCE(?, page_id),
                comparison_role = COALESCE(?, comparison_role),
                is_discovery_host = COALESCE(?, is_discovery_host),
                access_status = COALESCE(?, access_status),
                last_access_error = COALESCE(?, last_access_error),
                updated_at = COALESCE(?, updated_at)
            WHERE id = ?
            """,
            (
                src_row["name"],
                src_row["username"],
                src_row["page_id"],
                src_row["comparison_role"] if "comparison_role" in src_row.keys() else None,
                src_row["is_discovery_host"] if "is_discovery_host" in src_row.keys() else None,
                src_row["access_status"] if "access_status" in src_row.keys() else None,
                src_row["last_access_error"] if "last_access_error" in src_row.keys() else None,
                src_row["updated_at"] if "updated_at" in src_row.keys() else None,
                existing["id"],
            ),
        )
        return int(existing["id"])

    cur = target.execute(
        """
        INSERT INTO accounts (
            platform, external_id, name, username, page_id,
            comparison_role, is_discovery_host, access_status,
            last_access_error, updated_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        """,
        (
            src_row["platform"],
            src_row["external_id"],
            src_row["name"],
            src_row["username"],
            src_row["page_id"],
            src_row["comparison_role"] if "comparison_role" in src_row.keys() else None,
            src_row["is_discovery_host"] if "is_discovery_host" in src_row.keys() else 0,
            src_row["access_status"] if "access_status" in src_row.keys() else "ok",
            src_row["last_access_error"] if "last_access_error" in src_row.keys() else None,
            src_row["updated_at"] if "updated_at" in src_row.keys() else None,
            src_row["created_at"] if "created_at" in src_row.keys() else None,
        ),
    )
    stats.accounts_upserted += 1
    return int(cur.lastrowid)


def _ensure_post(
    target: sqlite3.Connection,
    src_row: sqlite3.Row,
    account_id: int,
    stats: MergeStats,
) -> int:
    existing = target.execute(
        """
        SELECT id FROM posts
        WHERE platform = ? AND external_id = ?
        """,
        (src_row["platform"], src_row["external_id"]),
    ).fetchone()
    if existing:
        return int(existing["id"])

    cur = target.execute(
        """
        INSERT INTO posts (
            platform, external_id, account_id, content_type, text, permalink,
            media_url, thumbnail_url, media_type, published_at, raw_json,
            first_synced_at, last_synced_at, media_product_type
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP),
                COALESCE(?, CURRENT_TIMESTAMP), ?)
        """,
        (
            src_row["platform"],
            src_row["external_id"],
            account_id,
            src_row["content_type"],
            src_row["text"],
            src_row["permalink"],
            src_row["media_url"],
            src_row["thumbnail_url"],
            src_row["media_type"],
            src_row["published_at"],
            src_row["raw_json"],
            src_row["first_synced_at"],
            src_row["last_synced_at"],
            src_row["media_product_type"] if "media_product_type" in src_row.keys() else None,
        ),
    )
    stats.posts_upserted += 1
    return int(cur.lastrowid)


def _has_account_measurement(
    target: sqlite3.Connection,
    platform: str,
    external_id: str,
    measured_at: str,
) -> bool:
    row = target.execute(
        """
        SELECT 1
        FROM account_measurements am
        JOIN accounts a ON a.id = am.account_id
        WHERE a.platform = ? AND a.external_id = ? AND am.measured_at = ?
        LIMIT 1
        """,
        (platform, external_id, measured_at),
    ).fetchone()
    return row is not None


def _has_post_measurement(
    target: sqlite3.Connection,
    platform: str,
    external_id: str,
    measured_at: str,
) -> bool:
    row = target.execute(
        """
        SELECT 1
        FROM post_measurements pm
        JOIN posts p ON p.id = pm.post_id
        WHERE p.platform = ? AND p.external_id = ? AND pm.measured_at = ?
        LIMIT 1
        """,
        (platform, external_id, measured_at),
    ).fetchone()
    return row is not None


def _insert_fetch_run(target: sqlite3.Connection, src_run: sqlite3.Row) -> int:
    cur = target.execute(
        """
        INSERT INTO comparison_fetch_runs (
            started_at, finished_at, status, trigger_source,
            measured_at, period_start, period_end, error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            src_run["started_at"],
            src_run["finished_at"],
            src_run["status"],
            src_run["trigger_source"],
            src_run["measured_at"],
            src_run["period_start"],
            src_run["period_end"],
            src_run["error_message"],
        ),
    )
    return int(cur.lastrowid)


def merge_history(source: Path, target: Path, *, dry_run: bool = False) -> MergeStats:
    stats = MergeStats()
    initialize_database(target)

    with _connect(source) as src, _connect(target) as dst:
        apply_comparison_schema(dst)

        if not _table_exists(src, "comparison_fetch_runs"):
            raise SystemExit(f"Source has no comparison_fetch_runs: {source}")

        # Prefetch source accounts/posts used by measurements.
        src_accounts = {
            int(r["id"]): r
            for r in src.execute("SELECT * FROM accounts").fetchall()
        }
        src_posts = {
            int(r["id"]): r
            for r in src.execute("SELECT * FROM posts").fetchall()
        }

        account_id_map: dict[int, int] = {}
        post_id_map: dict[int, int] = {}

        def map_account(src_account_id: int) -> int:
            if src_account_id in account_id_map:
                return account_id_map[src_account_id]
            row = src_accounts[src_account_id]
            new_id = _ensure_account(dst, row, stats)
            account_id_map[src_account_id] = new_id
            return new_id

        def map_post(src_post_id: int) -> int:
            if src_post_id in post_id_map:
                return post_id_map[src_post_id]
            row = src_posts[src_post_id]
            account_id = map_account(int(row["account_id"]))
            new_id = _ensure_post(dst, row, account_id, stats)
            post_id_map[src_post_id] = new_id
            return new_id

        runs = src.execute(
            "SELECT * FROM comparison_fetch_runs ORDER BY id"
        ).fetchall()

        for run in runs:
            src_run_id = int(run["id"])
            account_meas = src.execute(
                "SELECT * FROM account_measurements WHERE fetch_run_id = ?",
                (src_run_id,),
            ).fetchall()
            post_meas = src.execute(
                "SELECT * FROM post_measurements WHERE fetch_run_id = ?",
                (src_run_id,),
            ).fetchall()
            account_results = []
            if _table_exists(src, "comparison_fetch_account_results"):
                account_results = src.execute(
                    "SELECT * FROM comparison_fetch_account_results WHERE fetch_run_id = ?",
                    (src_run_id,),
                ).fetchall()

            pending_account_meas = []
            for am in account_meas:
                acc = src_accounts[int(am["account_id"])]
                if _has_account_measurement(
                    dst, acc["platform"], acc["external_id"], am["measured_at"]
                ):
                    stats.account_measurements_skipped += 1
                else:
                    pending_account_meas.append(am)

            pending_post_meas = []
            for pm in post_meas:
                post = src_posts[int(pm["post_id"])]
                if _has_post_measurement(
                    dst, post["platform"], post["external_id"], pm["measured_at"]
                ):
                    stats.post_measurements_skipped += 1
                else:
                    pending_post_meas.append(pm)

            # Still import fetch-run metadata if we have account results to add
            # and the run itself is not already represented by any measurement.
            if not pending_account_meas and not pending_post_meas and not account_results:
                stats.fetch_runs_skipped += 1
                continue
            if not pending_account_meas and not pending_post_meas:
                # Only results left; skip if all result accounts already have a
                # result row for an equivalent measured_at run is hard — import
                # results only when the run inserts measurements, otherwise skip.
                stats.fetch_runs_skipped += 1
                continue

            new_run_id = _insert_fetch_run(dst, run)
            stats.fetch_runs_inserted += 1

            for am in pending_account_meas:
                target_account_id = map_account(int(am["account_id"]))
                dst.execute(
                    """
                    INSERT INTO account_measurements (
                        account_id, fetch_run_id, measured_at,
                        followers_count, media_count
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        target_account_id,
                        new_run_id,
                        am["measured_at"],
                        am["followers_count"],
                        am["media_count"],
                    ),
                )
                stats.account_measurements_inserted += 1

            for pm in pending_post_meas:
                target_post_id = map_post(int(pm["post_id"]))
                dst.execute(
                    """
                    INSERT INTO post_measurements (
                        post_id, fetch_run_id, measured_at,
                        like_count, comments_count
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        target_post_id,
                        new_run_id,
                        pm["measured_at"],
                        pm["like_count"],
                        pm["comments_count"],
                    ),
                )
                stats.post_measurements_inserted += 1

            for ar in account_results:
                target_account_id = map_account(int(ar["account_id"]))
                exists = dst.execute(
                    """
                    SELECT 1 FROM comparison_fetch_account_results
                    WHERE fetch_run_id = ? AND account_id = ?
                    """,
                    (new_run_id, target_account_id),
                ).fetchone()
                if exists:
                    stats.account_results_skipped += 1
                    continue
                dst.execute(
                    """
                    INSERT INTO comparison_fetch_account_results (
                        fetch_run_id, account_id, status, posts_fetched,
                        period_start, period_end, is_complete,
                        oldest_post_at, newest_post_at, error_message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_run_id,
                        target_account_id,
                        ar["status"],
                        ar["posts_fetched"],
                        ar["period_start"],
                        ar["period_end"],
                        ar["is_complete"],
                        ar["oldest_post_at"],
                        ar["newest_post_at"],
                        ar["error_message"],
                    ),
                )
                stats.account_results_inserted += 1

        # Preferences: only fill when target has none / empty competitors.
        if _table_exists(src, "comparison_preferences"):
            src_pref = src.execute(
                "SELECT * FROM comparison_preferences WHERE id = 1"
            ).fetchone()
            dst_pref = dst.execute(
                "SELECT * FROM comparison_preferences WHERE id = 1"
            ).fetchone()
            if src_pref is not None:
                own_id = None
                if src_pref["own_account_id"] is not None:
                    own_id = map_account(int(src_pref["own_account_id"]))
                if dst_pref is None:
                    dst.execute(
                        """
                        INSERT INTO comparison_preferences (
                            id, own_account_id, competitor_usernames_json, updated_at
                        )
                        VALUES (1, ?, ?, ?)
                        """,
                        (
                            own_id,
                            src_pref["competitor_usernames_json"],
                            src_pref["updated_at"],
                        ),
                    )
                    stats.preferences_updated = True
                else:
                    competitors = (dst_pref["competitor_usernames_json"] or "").strip()
                    if (
                        dst_pref["own_account_id"] is None and own_id is not None
                    ) or competitors in {"", "[]"}:
                        dst.execute(
                            """
                            UPDATE comparison_preferences
                            SET own_account_id = COALESCE(own_account_id, ?),
                                competitor_usernames_json = CASE
                                    WHEN competitor_usernames_json IS NULL
                                      OR competitor_usernames_json IN ('', '[]')
                                    THEN ?
                                    ELSE competitor_usernames_json
                                END,
                                updated_at = ?
                            WHERE id = 1
                            """,
                            (own_id, src_pref["competitor_usernames_json"], src_pref["updated_at"]),
                        )
                        stats.preferences_updated = True

        if dry_run:
            dst.rollback()
        else:
            dst.commit()

    return stats


def _summarize_db(label: str, path: Path) -> None:
    if not path.exists():
        print(f"{label}: MISSING {path}")
        return
    with _connect(path) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        print(f"=== {label}: {path} ===")
        for table in (
            "comparison_fetch_runs",
            "account_measurements",
            "post_measurements",
            "accounts",
            "posts",
        ):
            if table not in tables:
                print(f"  {table}: (absent)")
                continue
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count}")
        if "account_measurements" in tables:
            am_count = int(
                conn.execute("SELECT COUNT(*) FROM account_measurements").fetchone()[0]
            )
            if am_count:
                row = conn.execute(
                    """
                    SELECT MIN(measured_at), MAX(measured_at)
                    FROM account_measurements
                    """
                ).fetchone()
                print(f"  account_measurements range: {row[0]} -> {row[1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="V2 / history DB")
    parser.add_argument("--target", type=Path, required=True, help="Live / destination DB")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "data" / "backups",
        help="Directory for pre-merge backups",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute merge but roll back writes",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Do not copy target before writing",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    target = args.target.resolve()
    if not source.is_file():
        raise SystemExit(f"Source DB not found: {source}")
    if not target.is_file():
        raise SystemExit(f"Target DB not found: {target}")
    if source == target:
        raise SystemExit("Source and target must be different files")

    _summarize_db("SOURCE", source)
    _summarize_db("TARGET (before)", target)

    if not args.skip_backup:
        backup = _backup(target, args.backup_dir.resolve(), "pre_merge_target")
        print(f"backup_target={backup}")
        # Also keep a source snapshot next to it for auditability.
        src_backup = _backup(source, args.backup_dir.resolve(), "pre_merge_source")
        print(f"backup_source={src_backup}")

    stats = merge_history(source, target, dry_run=args.dry_run)
    print("dry_run" if args.dry_run else "merged", stats)
    if not args.dry_run:
        _summarize_db("TARGET (after)", target)


if __name__ == "__main__":
    main()
