"""Create (or reset) a login user from the command line.

    python -m app.seed.create_user --username aftab --role admin --full-name "Aftab"
    python -m app.seed.create_user --username aftab --reset-password

The password is asked for interactively (not shown, not stored anywhere but as a hash).
Roles: admin (everything), engineer (create / edit), viewer (read only).
"""
import argparse
import getpass
import sys

from app.core.database import SessionLocal
from app.core.security import ROLES, hash_password, validate_new_password
from app.models.user import User


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", required=True)
    ap.add_argument("--role", choices=ROLES, default="viewer")
    ap.add_argument("--full-name", default=None)
    ap.add_argument("--reset-password", action="store_true", help="change the password of an existing user")
    args = ap.parse_args()

    username = args.username.strip().lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user and not args.reset_password:
            print(f"User '{username}' already exists (use --reset-password to change the password).")
            return 1
        if not user and args.reset_password:
            print(f"User '{username}' does not exist.")
            return 1
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat password: "):
            print("The two passwords are different.")
            return 1
        problem = validate_new_password(pw)
        if problem:
            print(problem)
            return 1
        if user:
            user.password_hash = hash_password(pw)
            user.is_active = True
            print(f"Password of '{username}' changed.")
        else:
            db.add(User(username=username, full_name=args.full_name, role=args.role, password_hash=hash_password(pw)))
            print(f"User '{username}' created with role '{args.role}'.")
        db.commit()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
