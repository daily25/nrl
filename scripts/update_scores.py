from __future__ import annotations

import argparse
import json
import threading

from nrl_tipping.config import AUTO_SCORE_CHECK_INTERVAL_SECONDS, AUTO_SCORE_MIN_AGE_HOURS
from nrl_tipping.score_worker import run_score_update_once, score_update_loop


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update completed NRL fixture scores from The Odds API and rescore tips."
    )
    parser.add_argument("--season-year", type=int, default=None, help="Target season year.")
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=AUTO_SCORE_MIN_AGE_HOURS,
        help="Only process fixtures at least this many hours after kickoff.",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=None,
        help="Optional override for scores daysFrom request window.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously (daemon mode) instead of one-time update.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=AUTO_SCORE_CHECK_INTERVAL_SECONDS,
        help="Loop interval when --loop is set.",
    )
    args = parser.parse_args()

    if args.loop:
        stop_event = threading.Event()
        try:
            score_update_loop(
                stop_event,
                interval_seconds=args.interval_seconds,
                season_year=args.season_year,
                min_age_hours=args.min_age_hours,
            )
        except KeyboardInterrupt:
            stop_event.set()
        return

    summary = run_score_update_once(
        season_year=args.season_year,
        min_age_hours=args.min_age_hours,
        days_back=args.days_back,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
