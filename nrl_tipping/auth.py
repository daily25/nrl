from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import timedelta
from uuid import uuid4

from nrl_tipping.config import SESSION_DURATION_HOURS
from nrl_tipping.utils import utc_now, utc_now_iso

PBKDF2_ALGO = "sha256"
PBKDF2_ITERATIONS = 260_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(PBKDF2_ALGO, password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_{PBKDF2_ALGO}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iteration_str, salt_hex, digest_hex = stored_hash.split("$", 3)
        if not algo.startswith("pbkdf2_"):
            return False
        hash_algo = algo.split("_", 1)[1]
        iterations = int(iteration_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except Exception:
        return False

    actual = hashlib.pbkdf2_hmac(hash_algo, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def create_user(
    conn: sqlite3.Connection,
    email: str,
    display_name: str,
    password: str,
    is_admin: bool = False,
    avatar_url: str | None = None,
    auth_provider: str = "local",
    facebook_id: str | None = None,
) -> int:
    password_hash = hash_password(password)
    cursor = conn.execute(
        """
        INSERT INTO users(email, display_name, password_hash, avatar_url, auth_provider, facebook_id, is_admin, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            email.lower().strip(),
            display_name.strip(),
            password_hash,
            avatar_url.strip() if avatar_url else None,
            auth_provider.strip() if auth_provider else "local",
            facebook_id.strip() if facebook_id else None,
            int(is_admin),
            utc_now_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (email.lower().strip(),),
    ).fetchone()


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_facebook_id(conn: sqlite3.Connection, facebook_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE facebook_id = ?",
        (facebook_id.strip(),),
    ).fetchone()


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    session_id = uuid4().hex
    now = utc_now()
    expires_at = (now + timedelta(hours=SESSION_DURATION_HOURS)).isoformat()
    conn.execute(
        """
        INSERT INTO sessions(id, user_id, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, user_id, expires_at, now.isoformat()),
    )
    conn.commit()
    return session_id


def purge_expired_sessions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now_iso(),))
    conn.commit()


def get_user_for_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    if not session_id:
        return None
    row = conn.execute(
        """
        SELECT u.*
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.id = ? AND s.expires_at > ?
        """,
        (session_id, utc_now_iso()),
    ).fetchone()
    return row


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


def delete_sessions_for_user(
    conn: sqlite3.Connection,
    user_id: int,
    except_session_id: str | None = None,
) -> int:
    if except_session_id:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND id != ?",
            (user_id, except_session_id),
        )
    else:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE user_id = ?",
            (user_id,),
        )
    conn.commit()
    return int(cursor.rowcount)


def set_user_password(conn: sqlite3.Connection, user_id: int, new_password: str) -> None:
    password_hash = hash_password(new_password)
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (password_hash, user_id),
    )
    conn.commit()


def set_user_avatar(conn: sqlite3.Connection, user_id: int, avatar_url: str | None) -> None:
    conn.execute(
        "UPDATE users SET avatar_url = ? WHERE id = ?",
        (avatar_url.strip() if avatar_url else None, user_id),
    )
    conn.commit()


def link_facebook_account(
    conn: sqlite3.Connection,
    user_id: int,
    facebook_id: str,
    avatar_url: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE users
        SET facebook_id = ?, auth_provider = 'facebook', avatar_url = COALESCE(?, avatar_url)
        WHERE id = ?
        """,
        (
            facebook_id.strip(),
            avatar_url.strip() if avatar_url else None,
            user_id,
        ),
    )
    conn.commit()


def generate_temp_password(length: int = 12) -> str:
    if length < 10:
        length = 10
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
