from __future__ import annotations

import sqlite3


def recalculate_tip_scores(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT t.id AS tip_id, t.tip_team, f.winner, f.status
        FROM tips t
        JOIN fixtures f ON f.id = t.fixture_id
        WHERE f.status = 'completed' AND f.winner IS NOT NULL
        """
    ).fetchall()

    updates = 0
    for row in rows:
        winner = row["winner"]
        points = 1 if row["tip_team"] == winner else 0
        conn.execute(
            "UPDATE tips SET points_awarded = ? WHERE id = ?",
            (points, row["tip_id"]),
        )
        updates += 1

    conn.commit()
    return updates

