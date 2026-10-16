"""Admin command line for user management (Phase 4 auth).

    python -m app.manage create-user alice --role admin     # prompts for a password
    python -m app.manage set-password alice
    python -m app.manage grant bob demo-project invoice-service
    python -m app.manage list-users
    python -m app.manage create-token alice --days 365      # long-lived token for scripts/Streamlit
"""
from __future__ import annotations

import argparse
import getpass
import sys

from app import auth
from db import database as db


def _password(args) -> str:
    if args.password:
        return args.password
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        sys.exit("Passwords don't match")
    if len(first) < 8:
        sys.exit("Use at least 8 characters")
    return first


def _user(username: str) -> dict:
    user = db.get_user_by_username(username)
    if not user:
        sys.exit(f"No user named '{username}'")
    return user


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.manage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user", help="create a user")
    p.add_argument("username")
    p.add_argument("--role", choices=auth.ROLES, default="viewer")
    p.add_argument("--password", help="omit to be prompted (recommended)")

    p = sub.add_parser("set-password", help="change a user's password")
    p.add_argument("username")
    p.add_argument("--password")

    p = sub.add_parser("grant", help="set which projects a viewer can see")
    p.add_argument("username")
    p.add_argument("projects", nargs="*", help="project names (none = revoke all)")

    sub.add_parser("list-users", help="list users")

    p = sub.add_parser("create-token", help="print a bearer token for a user")
    p.add_argument("username")
    p.add_argument("--days", type=int, default=30)

    args = parser.parse_args(argv)
    db.init_db()

    if args.command == "create-user":
        if db.get_user_by_username(args.username):
            sys.exit(f"User '{args.username}' already exists")
        db.create_user(args.username, auth.hash_password(_password(args)), args.role)
        print(f"Created {args.role} '{args.username}'")
    elif args.command == "set-password":
        user = _user(args.username)
        db.update_user(user["id"], password_hash=auth.hash_password(_password(args)))
        print(f"Password updated for '{args.username}'")
    elif args.command == "grant":
        user = _user(args.username)
        ids = []
        for name in args.projects:
            project = db.get_project_by_name(name)
            if not project:
                sys.exit(f"No project named '{name}'")
            ids.append(project["id"])
        db.set_user_projects(user["id"], ids)
        print(f"'{args.username}' can now see: {', '.join(args.projects) or 'no projects'}")
    elif args.command == "list-users":
        for user in db.list_users():
            names = [p["name"] for p in db.list_projects(db.user_project_ids(user["id"]))]
            scope = "all projects" if user["role"] == "admin" else (", ".join(names) or "no projects")
            print(f"{user['username']:20} {user['role']:7} {scope}")
    elif args.command == "create-token":
        from app import config as cfg

        if not cfg.AUTH_SECRET:
            sys.exit("Set AUTH_SECRET in .env first; tokens are signed with it")
        user = _user(args.username)
        print(auth.create_token(user["id"], ttl_hours=args.days * 24))


if __name__ == "__main__":
    main()
