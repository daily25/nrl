from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from nrl_tipping.config import TIP_LOCK_MINUTES
from nrl_tipping.utils import is_tip_locked, parse_iso_datetime, sydney_now, utc_now_iso


def get_round_numbers(conn: sqlite3.Connection, season_year: int | None = None) -> list[int]:
    if season_year is None:
        rows = conn.execute(
            """
            SELECT DISTINCT round_number
            FROM fixtures
            WHERE round_number IS NOT NULL
            ORDER BY round_number
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT round_number
            FROM fixtures
            WHERE round_number IS NOT NULL AND season_year = ?
            ORDER BY round_number
            """,
            (season_year,),
        ).fetchall()
    return [int(row["round_number"]) for row in rows]


def get_current_round(conn: sqlite3.Connection, season_year: int | None = None) -> int | None:
    target_season = season_year if season_year is not None else sydney_now().year
    now = utc_now_iso()
    upcoming = conn.execute(
        """
        SELECT round_number
        FROM fixtures
        WHERE round_number IS NOT NULL
          AND season_year = ?
          AND start_time_utc >= ?
        ORDER BY start_time_utc ASC
        LIMIT 1
        """,
        (target_season, now),
    ).fetchone()
    if upcoming:
        return int(upcoming["round_number"])

    first_round = conn.execute(
        """
        SELECT round_number
        FROM fixtures
        WHERE round_number IS NOT NULL AND season_year = ?
        ORDER BY round_number ASC
        LIMIT 1
        """,
        (target_season,),
    ).fetchone()
    if first_round and first_round["round_number"] is not None:
        return int(first_round["round_number"])

    global_first = conn.execute(
        """
        SELECT round_number
        FROM fixtures
        WHERE round_number IS NOT NULL
        ORDER BY start_time_utc ASC
        LIMIT 1
        """
    ).fetchone()
    if global_first and global_first["round_number"] is not None:
        return int(global_first["round_number"])
    return None


def get_round_fixtures(
    conn: sqlite3.Connection,
    round_number: int,
    season_year: int | None = None,
) -> list[sqlite3.Row]:
    if season_year is None:
        return conn.execute(
            """
            SELECT *
            FROM fixtures
            WHERE round_number = ?
            ORDER BY start_time_utc ASC
            """,
            (round_number,),
        ).fetchall()
    return conn.execute(
        """
        SELECT *
        FROM fixtures
        WHERE round_number = ? AND season_year = ?
        ORDER BY start_time_utc ASC
        """,
        (round_number, season_year),
    ).fetchall()


def pick_underdog_team(fixture: sqlite3.Row) -> str:
    home_team = str(fixture["home_team"])
    away_team = str(fixture["away_team"])

    def _as_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    home_price = _as_float(fixture["home_price"])
    away_price = _as_float(fixture["away_price"])

    # In decimal odds, higher price = longer odds = underdog.
    if home_price is not None and away_price is not None:
        if home_price > away_price:
            return home_team
        if away_price > home_price:
            return away_team
        return home_team
    if home_price is not None:
        return home_team
    if away_price is not None:
        return away_team
    return home_team


def apply_automatic_underdog_tips(
    conn: sqlite3.Connection,
    *,
    season_year: int | None = None,
    round_number: int | None = None,
    user_id: int | None = None,
    include_admin: bool = False,
    now: datetime | None = None,
) -> int:
    now_dt = now.astimezone(timezone.utc) if now is not None else sydney_now().astimezone(timezone.utc)
    now_iso = now_dt.isoformat()

    fixture_filters: list[str] = []
    fixture_params: list[object] = []
    if season_year is not None:
        fixture_filters.append("season_year = ?")
        fixture_params.append(season_year)
    if round_number is not None:
        fixture_filters.append("round_number = ?")
        fixture_params.append(round_number)

    fixture_where = ""
    if fixture_filters:
        fixture_where = "WHERE " + " AND ".join(fixture_filters)

    fixtures = conn.execute(
        f"""
        SELECT id, start_time_utc, home_team, away_team, home_price, away_price
        FROM fixtures
        {fixture_where}
        ORDER BY start_time_utc ASC
        """,
        tuple(fixture_params),
    ).fetchall()

    inserted = 0
    for fixture in fixtures:
        if not is_tip_locked(fixture["start_time_utc"], now=now_dt, lock_minutes=TIP_LOCK_MINUTES):
            continue

        underdog_team = pick_underdog_team(fixture)
        lock_deadline_iso = (
            parse_iso_datetime(fixture["start_time_utc"])
            - timedelta(minutes=max(0, int(TIP_LOCK_MINUTES)))
        ).isoformat()

        user_filters = ["u.created_at <= ?"]
        user_params: list[object] = [lock_deadline_iso]
        if not include_admin:
            user_filters.append("u.is_admin = 0")
        if user_id is not None:
            user_filters.append("u.id = ?")
            user_params.append(user_id)

        cursor = conn.execute(
            f"""
            INSERT INTO tips(user_id, fixture_id, tip_team, created_at, updated_at, points_awarded)
            SELECT u.id, ?, ?, ?, ?, NULL
            FROM users u
            WHERE {" AND ".join(user_filters)}
              AND NOT EXISTS (
                SELECT 1
                FROM tips t
                WHERE t.user_id = u.id AND t.fixture_id = ?
              )
            """,
            (
                int(fixture["id"]),
                underdog_team,
                now_iso,
                now_iso,
                *user_params,
                int(fixture["id"]),
            ),
        )
        inserted += max(int(cursor.rowcount or 0), 0)

    if inserted:
        conn.commit()
    return inserted


def get_user_tips_for_round(
    conn: sqlite3.Connection,
    user_id: int,
    round_number: int,
    season_year: int | None = None,
) -> dict[int, sqlite3.Row]:
    if season_year is None:
        rows = conn.execute(
            """
            SELECT t.*
            FROM tips t
            JOIN fixtures f ON f.id = t.fixture_id
            WHERE t.user_id = ? AND f.round_number = ?
            """,
            (user_id, round_number),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT t.*
            FROM tips t
            JOIN fixtures f ON f.id = t.fixture_id
            WHERE t.user_id = ? AND f.round_number = ? AND f.season_year = ?
            """,
            (user_id, round_number, season_year),
        ).fetchall()
    return {int(row["fixture_id"]): row for row in rows}


def get_dashboard_counts(conn: sqlite3.Connection, user_id: int) -> dict[str, int]:
    total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    total_fixtures = conn.execute("SELECT COUNT(*) AS c FROM fixtures").fetchone()["c"]
    total_tips = conn.execute("SELECT COUNT(*) AS c FROM tips WHERE user_id = ?", (user_id,)).fetchone()["c"]
    correct_tips = conn.execute(
        "SELECT COUNT(*) AS c FROM tips WHERE user_id = ? AND points_awarded = 1",
        (user_id,),
    ).fetchone()["c"]
    return {
        "users": int(total_users),
        "fixtures": int(total_fixtures),
        "tips": int(total_tips),
        "correct_tips": int(correct_tips),
    }


def get_leaderboard(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            u.id,
            u.display_name,
            COUNT(t.id) AS tips_made,
            COALESCE(SUM(CASE WHEN t.points_awarded = 1 THEN 1 ELSE 0 END), 0) AS correct_tips,
            COALESCE(SUM(t.points_awarded), 0) AS total_points
        FROM users u
        LEFT JOIN tips t ON t.user_id = u.id
        GROUP BY u.id, u.display_name
        ORDER BY total_points DESC, correct_tips DESC, tips_made DESC, u.display_name ASC
        """
    ).fetchall()


def get_round_leaderboard(
    conn: sqlite3.Connection,
    round_number: int,
    season_year: int | None = None,
) -> list[sqlite3.Row]:
    if season_year is None:
        return conn.execute(
            """
            SELECT
                u.id,
                u.display_name,
                COUNT(t.id) AS tips_made,
                COALESCE(SUM(CASE WHEN t.points_awarded = 1 THEN 1 ELSE 0 END), 0) AS correct_tips,
                COALESCE(SUM(t.points_awarded), 0) AS total_points
            FROM users u
            LEFT JOIN tips t ON t.user_id = u.id
            LEFT JOIN fixtures f ON f.id = t.fixture_id
            WHERE f.round_number = ?
            GROUP BY u.id, u.display_name
            ORDER BY total_points DESC, correct_tips DESC, tips_made DESC, u.display_name ASC
            """,
            (round_number,),
        ).fetchall()
    return conn.execute(
        """
        SELECT
            u.id,
            u.display_name,
            COUNT(t.id) AS tips_made,
            COALESCE(SUM(CASE WHEN t.points_awarded = 1 THEN 1 ELSE 0 END), 0) AS correct_tips,
            COALESCE(SUM(t.points_awarded), 0) AS total_points
        FROM users u
        LEFT JOIN tips t ON t.user_id = u.id
        LEFT JOIN fixtures f ON f.id = t.fixture_id
        WHERE f.round_number = ? AND f.season_year = ?
        GROUP BY u.id, u.display_name
        ORDER BY total_points DESC, correct_tips DESC, tips_made DESC, u.display_name ASC
        """,
        (round_number, season_year),
    ).fetchall()


def get_ladder(conn: sqlite3.Connection, season_year: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            team,
            COUNT(*) AS played,
            SUM(CASE WHEN result = 'W' THEN 1 ELSE 0 END) AS won,
            SUM(CASE WHEN result = 'L' THEN 1 ELSE 0 END) AS lost,
            SUM(CASE WHEN result = 'D' THEN 1 ELSE 0 END) AS drawn,
            SUM(pf) AS points_for,
            SUM(pa) AS points_against,
            SUM(pf) - SUM(pa) AS point_diff,
            SUM(CASE WHEN result = 'W' THEN 2 WHEN result = 'D' THEN 1 ELSE 0 END) AS comp_points,
            logo_url
        FROM (
            SELECT
                home_team AS team,
                home_score AS pf,
                away_score AS pa,
                home_logo_url AS logo_url,
                CASE
                    WHEN home_score > away_score THEN 'W'
                    WHEN home_score < away_score THEN 'L'
                    ELSE 'D'
                END AS result
            FROM fixtures
            WHERE status = 'completed' AND season_year = ?
              AND home_score IS NOT NULL AND away_score IS NOT NULL
            UNION ALL
            SELECT
                away_team AS team,
                away_score AS pf,
                home_score AS pa,
                away_logo_url AS logo_url,
                CASE
                    WHEN away_score > home_score THEN 'W'
                    WHEN away_score < home_score THEN 'L'
                    ELSE 'D'
                END AS result
            FROM fixtures
            WHERE status = 'completed' AND season_year = ?
              AND home_score IS NOT NULL AND away_score IS NOT NULL
        )
        GROUP BY team
        ORDER BY comp_points DESC, point_diff DESC, points_for DESC, team ASC
        """,
        (season_year, season_year),
    ).fetchall()
    return [
        {
            "team": row["team"],
            "played": int(row["played"]),
            "won": int(row["won"]),
            "lost": int(row["lost"]),
            "drawn": int(row["drawn"]),
            "points_for": int(row["points_for"]),
            "points_against": int(row["points_against"]),
            "point_diff": int(row["point_diff"]),
            "comp_points": int(row["comp_points"]),
            "logo_url": row["logo_url"],
        }
        for row in rows
    ]


def get_leaderboard_with_rounds(
    conn: sqlite3.Connection, season_year: int, round_numbers: list[int],
) -> list[dict[str, Any]]:
    overall = conn.execute(
        """
        SELECT
            u.id,
            u.display_name,
            u.avatar_url,
            COUNT(t.id) AS tips_made,
            COALESCE(SUM(CASE WHEN t.points_awarded = 1 THEN 1 ELSE 0 END), 0) AS correct_tips,
            COALESCE(SUM(t.points_awarded), 0) AS total_points
        FROM users u
        LEFT JOIN tips t ON t.user_id = u.id
        GROUP BY u.id, u.display_name, u.avatar_url
        ORDER BY total_points DESC, correct_tips DESC, tips_made DESC, u.display_name ASC
        """
    ).fetchall()

    round_scores: dict[int, dict[int, int]] = {}
    if round_numbers:
        placeholders = ",".join("?" for _ in round_numbers)
        rd_rows = conn.execute(
            f"""
            SELECT
                t.user_id,
                f.round_number,
                COALESCE(SUM(t.points_awarded), 0) AS round_points
            FROM tips t
            JOIN fixtures f ON f.id = t.fixture_id
            WHERE f.season_year = ? AND f.round_number IN ({placeholders})
            GROUP BY t.user_id, f.round_number
            """,
            (season_year, *round_numbers),
        ).fetchall()
        for rd in rd_rows:
            uid = int(rd["user_id"])
            rn = int(rd["round_number"])
            round_scores.setdefault(uid, {})[rn] = int(rd["round_points"])

    result = []
    for row in overall:
        uid = int(row["id"])
        user_rounds = round_scores.get(uid, {})
        result.append({
            "id": uid,
            "display_name": str(row["display_name"]),
            "avatar_url": str(row["avatar_url"]) if row["avatar_url"] else None,
            "tips_made": int(row["tips_made"]),
            "correct_tips": int(row["correct_tips"]),
            "total_points": int(row["total_points"]),
            "round_points": {rn: user_rounds.get(rn, 0) for rn in round_numbers},
        })
    return result


def get_recent_fixtures(conn: sqlite3.Connection, limit: int = 12) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM fixtures
        ORDER BY start_time_utc DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_next_fixtures(conn: sqlite3.Connection, limit: int = 12) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM fixtures
        WHERE start_time_utc >= ?
        ORDER BY start_time_utc ASC
        LIMIT ?
        """,
        (utc_now_iso(), limit),
    ).fetchall()


def save_tips(
    conn: sqlite3.Connection,
    user_id: int,
    picks: Iterable[tuple[int, str]],
) -> int:
    saved = 0
    now = utc_now_iso()
    for fixture_id, tip_team in picks:
        conn.execute(
            """
            INSERT INTO tips(user_id, fixture_id, tip_team, created_at, updated_at, points_awarded)
            VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(user_id, fixture_id)
            DO UPDATE SET tip_team = excluded.tip_team, updated_at = excluded.updated_at
            """,
            (user_id, fixture_id, tip_team, now, now),
        )
        saved += 1
    conn.commit()
    return saved


def get_round_tipsheet_data(
    conn: sqlite3.Connection,
    season_year: int,
    round_number: int,
    include_admin: bool = False,
) -> dict[str, Any]:
    fixtures = get_round_fixtures(conn, round_number=round_number, season_year=season_year)
    fixture_ids = [int(row["id"]) for row in fixtures]
    if not fixture_ids:
        return {
            "fixtures": fixtures,
            "participants": [],
            "tips_by_user_fixture": {},
            "all_submitted": False,
            "total_required": 0,
        }

    placeholders = ",".join("?" for _ in fixture_ids)
    where_clause = "1=1" if include_admin else "u.is_admin = 0"
    participant_rows = conn.execute(
        f"""
        SELECT
            u.id,
            u.display_name,
            u.avatar_url,
            u.is_admin,
            COUNT(t.id) AS tips_submitted
        FROM users u
        LEFT JOIN tips t
            ON t.user_id = u.id
           AND t.fixture_id IN ({placeholders})
        WHERE {where_clause}
        GROUP BY u.id, u.display_name, u.avatar_url, u.is_admin
        ORDER BY u.display_name COLLATE NOCASE ASC
        """,
        tuple(fixture_ids),
    ).fetchall()

    # Fallback to all users if no non-admin accounts exist.
    if not participant_rows and not include_admin:
        participant_rows = conn.execute(
            f"""
            SELECT
                u.id,
                u.display_name,
                u.avatar_url,
                u.is_admin,
                COUNT(t.id) AS tips_submitted
            FROM users u
            LEFT JOIN tips t
                ON t.user_id = u.id
               AND t.fixture_id IN ({placeholders})
            GROUP BY u.id, u.display_name, u.avatar_url, u.is_admin
            ORDER BY u.display_name COLLATE NOCASE ASC
            """,
            tuple(fixture_ids),
        ).fetchall()

    total_required = len(fixture_ids)
    participants: list[dict[str, Any]] = []
    for row in participant_rows:
        tips_submitted = int(row["tips_submitted"])
        participants.append(
            {
                "id": int(row["id"]),
                "display_name": str(row["display_name"]),
                "avatar_url": str(row["avatar_url"]) if row["avatar_url"] else None,
                "is_admin": int(row["is_admin"]) == 1,
                "tips_submitted": tips_submitted,
                "has_submitted": total_required > 0 and tips_submitted >= total_required,
            }
        )

    tip_rows = conn.execute(
        f"""
        SELECT user_id, fixture_id, tip_team, points_awarded
        FROM tips
        WHERE fixture_id IN ({placeholders})
        """,
        tuple(fixture_ids),
    ).fetchall()
    tips_by_user_fixture: dict[tuple[int, int], sqlite3.Row] = {}
    for row in tip_rows:
        key = (int(row["user_id"]), int(row["fixture_id"]))
        tips_by_user_fixture[key] = row

    all_submitted = bool(participants) and all(item["has_submitted"] for item in participants)
    return {
        "fixtures": fixtures,
        "participants": participants,
        "tips_by_user_fixture": tips_by_user_fixture,
        "all_submitted": all_submitted,
        "total_required": total_required,
    }
