"""Shell-only admin password recovery for PVE2Services.

Run on the host (or inside the container) with the same environment as the
service. The database target is resolved exactly like the app
(PVE2_DB_URL > PVE2_DB_MODE > PVE2_DB_PATH > fallback files).

Usage:
    python tools/reset_admin_password.py status
    python tools/reset_admin_password.py set [--username NAME]
        [--password PW | --password-stdin]
    python tools/reset_admin_password.py reset [--yes]

'set' overwrites the admin credentials. 'reset' removes them entirely so the
next web access goes through first-boot setup (/admin/setup).
"""

import argparse
import getpass
import sys
from pathlib import Path

# tools/ -> repo root, which contains the PVE2Services/ import package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bcrypt
from sqlalchemy import text
from sqlalchemy.engine import make_url

MIN_PASSWORD_LEN = 8


def _connect(db_url=None):
    from PVE2Services.libs.db_adapter import DBAdapter

    return DBAdapter(db_url=db_url)


def _display_url(db) -> str:
    try:
        url = make_url(db.db_url)
        if url.password:
            url = url._replace(password="****")
        return str(url)
    except Exception:
        return "<hidden>"


def _get_credentials(db):
    return db.get_setting("admin_user"), db.get_setting("admin_password_hash")


def _clear_rate_limits(db):
    with db.engine.begin() as conn:
        for table in ("rate_limit_events", "rate_limit_blocks"):
            try:
                conn.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass


def cmd_status(args) -> int:
    db = _connect(args.db_url)
    user, stored = _get_credentials(db)
    print(f"Database: {_display_url(db)}")
    if stored:
        kind = "bcrypt" if stored.startswith("$2b$") else "legacy hash"
        print(f"Admin credentials: SET (user={user!r}, {kind})")
        print("Use 'set' to overwrite, or 'reset' to re-enable first-boot setup.")
    else:
        print("Admin credentials: NOT SET")
        print("Next web access will require first-boot setup (/admin/setup).")
    return 0


def _read_new_password(args) -> str:
    if args.password is not None:
        print("Warning: a password passed on the command line may end up in shell history.")
        password = args.password
    elif args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("New admin password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.", file=sys.stderr)
            raise SystemExit(2)
    if len(password) < MIN_PASSWORD_LEN:
        print(
            f"Error: password must be at least {MIN_PASSWORD_LEN} characters.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return password


def cmd_set(args) -> int:
    db = _connect(args.db_url)
    current_user, _ = _get_credentials(db)
    username = args.username or current_user or "admin"
    password = _read_new_password(args)
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    print(f"Database: {_display_url(db)}")
    db.set_setting("admin_user", username)
    db.set_setting("admin_password_hash", password_hash)
    _clear_rate_limits(db)
    print(f"Admin credentials updated: user={username!r}")
    print(
        "Existing sessions remain valid; delete the .pve2_secret file "
        "(or PVE2_SECRET_FILE) to invalidate them all."
    )
    return 0


def cmd_reset(args) -> int:
    db = _connect(args.db_url)
    user, stored = _get_credentials(db)
    print(f"Database: {_display_url(db)}")
    if not stored and not user:
        print("Admin credentials are already unset.")
        return 0
    if not args.yes:
        answer = input(
            f"Remove credentials (user={user!r})? "
            "Next web access will require first-boot setup. [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1
    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM settings WHERE key IN ('admin_user', 'admin_password_hash')"))
    _clear_rate_limits(db)
    print("Admin credentials removed. Next web access will require /admin/setup.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reset_admin_password",
        description="Shell-only admin password recovery for PVE2Services.",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Override the database URL (defaults to PVE2_DB_URL / standard resolution)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show whether admin credentials are set")
    p_status.set_defaults(func=cmd_status)

    p_set = sub.add_parser("set", help="Set or overwrite the admin credentials")
    p_set.add_argument("--username", default=None, help="Admin username (default: current or 'admin')")
    p_set.add_argument("--password", default=None, help="New password (visible in shell history)")
    p_set.add_argument("--password-stdin", action="store_true", help="Read the password from stdin")
    p_set.set_defaults(func=cmd_set)

    p_reset = sub.add_parser("reset", help="Remove credentials; next access requires setup")
    p_reset.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    p_reset.set_defaults(func=cmd_reset)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
