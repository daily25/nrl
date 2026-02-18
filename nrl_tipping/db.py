from __future__ import annotations

import sqlite3
from pathlib import Path

from nrl_tipping.config import DB_PATH


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            avatar_url TEXT,
            auth_provider TEXT NOT NULL DEFAULT 'local',
            facebook_id TEXT UNIQUE,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS fixtures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odds_event_id TEXT NOT NULL UNIQUE,
            start_time_utc TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            stadium_name TEXT,
            stadium_city TEXT,
            home_logo_url TEXT,
            away_logo_url TEXT,
            season_year INTEGER,
            round_number INTEGER,
            status TEXT NOT NULL DEFAULT 'scheduled',
            home_score INTEGER,
            away_score INTEGER,
            winner TEXT,
            home_price REAL,
            away_price REAL,
            raw_json TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fixture_id INTEGER NOT NULL,
            tip_team TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            points_awarded INTEGER,
            UNIQUE(user_id, fixture_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(fixture_id) REFERENCES fixtures(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS team_logos (
            normalized_name TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            logo_url TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_fixtures_start_time ON fixtures(start_time_utc);
        CREATE INDEX IF NOT EXISTS idx_fixtures_round ON fixtures(round_number);
        CREATE INDEX IF NOT EXISTS idx_tips_user ON tips(user_id);
        CREATE INDEX IF NOT EXISTS idx_tips_fixture ON tips(fixture_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
        """
    )
    _ensure_column(conn, "fixtures", "home_logo_url", "TEXT")
    _ensure_column(conn, "fixtures", "away_logo_url", "TEXT")
    _ensure_column(conn, "fixtures", "stadium_name", "TEXT")
    _ensure_column(conn, "fixtures", "stadium_city", "TEXT")
    _ensure_column(conn, "fixtures", "season_year", "INTEGER")
    _ensure_column(conn, "users", "avatar_url", "TEXT")
    _ensure_column(conn, "users", "auth_provider", "TEXT NOT NULL DEFAULT 'local'")
    _ensure_column(conn, "users", "facebook_id", "TEXT")
    conn.execute(
        """
        UPDATE users
        SET auth_provider = 'local'
        WHERE auth_provider IS NULL OR trim(auth_provider) = ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_facebook_id_unique
        ON users(facebook_id)
        WHERE facebook_id IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE fixtures
        SET season_year = CAST(substr(start_time_utc, 1, 4) AS INTEGER)
        WHERE season_year IS NULL
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fixtures_season_round ON fixtures(season_year, round_number)"
    )
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {row["name"] for row in rows}
    if column in existing:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()
