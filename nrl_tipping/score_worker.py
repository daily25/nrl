from __future__ import annotations

import sys
import threading
from typing import Any

from nrl_tipping.config import (
    AUTO_SCORE_CHECK_INTERVAL_SECONDS,
    AUTO_SCORE_MIN_AGE_HOURS,
    AUTO_SCORE_UPDATER_ENABLED,
)
from nrl_tipping.db import connect_db, init_db
from nrl_tipping.sync import update_completed_scores
from nrl_tipping.utils import sydney_now_iso


def run_score_update_once(
    *,
    season_year: int | None = None,
    min_age_hours: float = AUTO_SCORE_MIN_AGE_HOURS,
    days_back: int | None = None,
) -> dict[str, Any]:
    conn = connect_db()
    try:
        init_db(conn)
        return update_completed_scores(
            conn,
            season_year=season_year,
            min_age_hours=min_age_hours,
            days_back=days_back,
        )
    finally:
        conn.close()


def _log_summary(summary: dict[str, Any]) -> None:
    updated = int(summary.get("fixtures_updated") or 0)
    autofill = int(summary.get("auto_underdog_tips_added") or 0)
    pending = int(summary.get("pending_due_fixtures") or 0)
    if updated == 0 and autofill == 0:
        return
    print(
        "[auto-score]"
        f" {sydney_now_iso()} updated={updated}"
        f" auto_underdog={autofill}"
        f" pending_due={pending}",
        file=sys.stderr,
    )


def score_update_loop(
    stop_event: threading.Event,
    *,
    interval_seconds: int = AUTO_SCORE_CHECK_INTERVAL_SECONDS,
    season_year: int | None = None,
    min_age_hours: float = AUTO_SCORE_MIN_AGE_HOURS,
) -> None:
    interval = max(60, int(interval_seconds))
    print(
        f"[auto-score] started interval={interval}s min_age_hours={min_age_hours}",
        file=sys.stderr,
    )
    while not stop_event.is_set():
        try:
            summary = run_score_update_once(
                season_year=season_year,
                min_age_hours=min_age_hours,
            )
            _log_summary(summary)
        except Exception as exc:
            print(f"[auto-score] error: {exc}", file=sys.stderr)
        if stop_event.wait(interval):
            break


def start_score_update_worker(
    *,
    season_year: int | None = None,
    min_age_hours: float = AUTO_SCORE_MIN_AGE_HOURS,
    interval_seconds: int = AUTO_SCORE_CHECK_INTERVAL_SECONDS,
) -> tuple[threading.Thread, threading.Event] | None:
    if not AUTO_SCORE_UPDATER_ENABLED:
        print("[auto-score] disabled via AUTO_SCORE_UPDATER_ENABLED", file=sys.stderr)
        return None
    stop_event = threading.Event()
    thread = threading.Thread(
        target=score_update_loop,
        kwargs={
            "stop_event": stop_event,
            "interval_seconds": interval_seconds,
            "season_year": season_year,
            "min_age_hours": min_age_hours,
        },
        name="auto-score-updater",
        daemon=True,
    )
    thread.start()
    return thread, stop_event
