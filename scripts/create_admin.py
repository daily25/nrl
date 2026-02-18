from __future__ import annotations

import argparse

from nrl_tipping import auth
from nrl_tipping.db import connect_db, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update an admin account.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    conn = connect_db()
    try:
        init_db(conn)
        existing = auth.get_user_by_email(conn, args.email)
        if existing:
            conn.execute(
                """
                UPDATE users
                SET display_name = ?, password_hash = ?, is_admin = 1
                WHERE id = ?
                """,
                (args.name, auth.hash_password(args.password), existing["id"]),
            )
            conn.commit()
            print(f"Updated admin user: {args.email}")
        else:
            auth.create_user(conn, args.email, args.name, args.password, is_admin=True)
            print(f"Created admin user: {args.email}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

