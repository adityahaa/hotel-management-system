"""Small command-line helper.

    python manage.py seed-demo     add synthetic demo rooms, guests and bookings
    python manage.py reset-demo    DELETE the database and start again with demo data
    python manage.py reset-empty   DELETE the database and start again with no data
"""
import argparse
import os
import sys
from datetime import date

from hotel import create_app, db
from hotel.seed import seed_demo


def database_path(app):
    return app.config["DATABASE"]


def run_seed(app):
    conn = db.connect(database_path(app))
    try:
        if seed_demo(conn, date.today()):
            print("Demo data added: 11 rooms, 10 guests, 9 reservations (all synthetic).")
        else:
            print("The database already contains rooms or guests, so nothing was added.")
            print("To start over, run:  python manage.py reset-demo")
    finally:
        conn.close()


def delete_database(app, assume_yes):
    path = database_path(app)
    if os.path.exists(path):
        if not assume_yes:
            answer = input(f"This permanently deletes {path}. Type YES to continue: ")
            if answer.strip() != "YES":
                print("Cancelled. Nothing was changed.")
                return False
        os.remove(path)
    return True


def main():
    parser = argparse.ArgumentParser(description="Hotel Management System helper")
    parser.add_argument("command", choices=["seed-demo", "reset-demo", "reset-empty"])
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args()

    app = create_app()
    if args.command == "seed-demo":
        run_seed(app)
    else:
        if not delete_database(app, args.yes):
            return 1
        app = create_app()  # recreates the empty database
        if args.command == "reset-demo":
            run_seed(app)
        else:
            print("Fresh empty database created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
