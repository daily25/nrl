from __future__ import annotations

import argparse
import json
from pathlib import Path

from nrl_tipping.db import connect_db, init_db
from nrl_tipping.sync import sync_nrl_season


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and sync NRL season fixtures from The Odds API.")
    parser.add_argument("--season-year", type=int, default=None, help="Target season year. Defaults to current UTC year.")
    parser.add_argument(
        "--days-back",
        type=int,
        default=30,
        help="Days of results to request from /scores endpoint (auto-falls back to supported values).",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=None,
        help="Optional output path for sync summary JSON.",
    )
    parser.add_argument(
        "--keep-other-seasons",
        action="store_true",
        help="Keep fixtures from non-target seasons instead of pruning them.",
    )
    args = parser.parse_args()

    conn = connect_db()
    try:
        init_db(conn)
        summary = sync_nrl_season(
            conn,
            season_year=args.season_year,
            days_back=args.days_back,
            prune_other_seasons=not args.keep_other_seasons,
        )
    finally:
        conn.close()

    print(json.dumps(summary, indent=2))
    if args.summary_file:
        args.summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Summary written to {args.summary_file}")


if __name__ == "__main__":
    main()
