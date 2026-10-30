"""Command-line tools for accounts.

    python -m app.cli create-user --email you@company.com --name "Your Name" --role super_admin
    python -m app.cli seed-demo        # development only: six demo users, one shared password

There is no built-in account and no default password. The first Super Admin is created here.
Passwords are typed at a hidden prompt (or read from an environment variable) and are never
printed, except the one-off password that seed-demo generates for the demo users.
"""

import argparse
import getpass
import os
import secrets
import sys
from collections.abc import Sequence

from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.core.permissions import Role
from app.repositories import users as repo
from app.schemas.auth import UserCreate
from app.services import users as user_service

DEMO_USERS = [
    ("admin@apex.test", "Asha Admin", Role.SUPER_ADMIN, []),
    ("production.manager@apex.test", "Pranav Production", Role.DEPARTMENT_MANAGER, ["production"]),
    ("safety.manager@apex.test", "Sana Safety", Role.DEPARTMENT_MANAGER, ["safety"]),
    ("hr.manager@apex.test", "Hari HR", Role.DEPARTMENT_MANAGER, ["hr"]),
    ("production.employee@apex.test", "Esha Production", Role.EMPLOYEE, ["production"]),
    ("stores.employee@apex.test", "Sameer Stores", Role.EMPLOYEE, ["stores"]),
]


def _password(args: argparse.Namespace) -> str:
    if args.password_env:
        value = os.environ.get(args.password_env, "")
        if not value:
            raise SystemExit(f"Environment variable {args.password_env} is empty or not set.")
        return value
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("The passwords do not match.")
    return first


def _create(settings: Settings, args: argparse.Namespace) -> int:
    data = UserCreate(
        email=args.email,
        full_name=args.name,
        password=_password(args),
        role=Role(args.role),
        departments=[d for d in args.departments.split(",") if d],
    )
    with get_session_factory(settings.database_url)() as db:
        try:
            user = user_service.create_user(db, settings, None, data)
        except user_service.UserError as exc:
            print(f"Could not create the user: {exc.reason}", file=sys.stderr)
            return 1
    print(f"Created {user.role} {user.email}")
    return 0


def _seed_demo(settings: Settings) -> int:
    if settings.app_env == "production":
        print(
            "seed-demo is for development only and refuses to run in production.", file=sys.stderr
        )
        return 1
    password = secrets.token_urlsafe(12)  # 16 characters, above the minimum length
    created = []
    with get_session_factory(settings.database_url)() as db:
        for email, name, role, departments in DEMO_USERS:
            if repo.get_by_email(db, email):
                continue
            user_service.create_user(
                db,
                settings,
                None,
                UserCreate(
                    email=email,
                    full_name=name,
                    password=password,
                    role=role,
                    departments=departments,
                ),
            )
            created.append((email, role.value, ",".join(departments) or "all"))
    if not created:
        print("All demo users already exist. Nothing changed (existing passwords are kept).")
        return 0
    print("Demo users created. They all share this password. Shown once, so copy it now:\n")
    print(f"    {password}\n")
    for address, role_name, department_names in created:
        print(f"    {address:<34} {role_name:<20} {department_names}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-user", help="create one user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", required=True, choices=[r.value for r in Role])
    create.add_argument("--departments", default="", help="comma-separated, e.g. safety,hr")
    create.add_argument("--password-env", help="read the password from this environment variable")

    commands.add_parser("seed-demo", help="create demo users (development only)")

    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command == "create-user":
        return _create(settings, args)
    return _seed_demo(settings)


if __name__ == "__main__":
    raise SystemExit(main())
