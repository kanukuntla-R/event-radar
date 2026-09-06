#!/usr/bin/env python3
"""Local CLI for Event Radar account administration (V0.10C).

Passwords are NEVER accepted as command-line arguments - always entered
interactively via getpass (not echoed, never logged, never printed).

Usage:
    python user_admin.py create-user [--email EMAIL] [--display-name NAME] [--admin]
    python user_admin.py set-password --email EMAIL
    python user_admin.py list-users
"""
import argparse
import getpass
import sys

from db import get_db, init_db
from user_store import normalize_email, get_user_by_email, create_user, set_user_password


def _prompt_password(label="Password"):
    password = getpass.getpass(f"{label}: ")
    confirm = getpass.getpass(f"Confirm {label.lower()}: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        return None
    if not password:
        print("Password cannot be empty.", file=sys.stderr)
        return None
    return password


def cmd_create_user(args):
    email = args.email or input("Email: ")
    display_name = args.display_name
    if display_name is None:
        display_name = input("Display name (optional): ") or None
    password = _prompt_password()
    if password is None:
        return 1
    conn = get_db()
    try:
        user = create_user(conn, email, password, display_name=display_name, is_admin=args.admin)
        conn.commit()
    except ValueError as e:
        conn.close()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    conn.close()
    print(f"Created user {user['email']!r} (admin={'yes' if user['is_admin'] else 'no'}).")
    return 0


def cmd_set_password(args):
    email = args.email or input("Email: ")
    conn = get_db()
    user = get_user_by_email(conn, email)
    if user is None:
        conn.close()
        print(f"Error: no user with email {normalize_email(email)!r}", file=sys.stderr)
        return 1
    password = _prompt_password()
    if password is None:
        conn.close()
        return 1
    set_user_password(conn, user["id"], password)
    conn.commit()
    conn.close()
    print(f"Password updated for {user['email']!r}.")
    return 0


def cmd_list_users(args):
    conn = get_db()
    users = conn.execute(
        "SELECT email, display_name, is_admin, password_hash FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    print(f"{'email':<40} {'display name':<20} {'admin':<6} password set")
    for u in users:
        print(
            f"{u['email']:<40} {(u['display_name'] or ''):<20} "
            f"{'yes' if u['is_admin'] else 'no':<6} {'yes' if u['password_hash'] else 'no'}"
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Event Radar account administration")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-user", help="Create a new account (password entered interactively)")
    p_create.add_argument("--email")
    p_create.add_argument("--display-name")
    p_create.add_argument("--admin", action="store_true", help="Create as admin (default: regular user)")
    p_create.set_defaults(func=cmd_create_user)

    p_setpw = sub.add_parser("set-password", help="Set/reset a user's password (entered interactively)")
    p_setpw.add_argument("--email")
    p_setpw.set_defaults(func=cmd_set_password)

    p_list = sub.add_parser("list-users", help="List accounts (never shows password hashes)")
    p_list.set_defaults(func=cmd_list_users)

    args = parser.parse_args(argv)
    init_db()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
