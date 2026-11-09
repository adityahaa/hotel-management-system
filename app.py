"""Start the Hotel Management System:  python app.py

Then open http://127.0.0.1:5000 in your browser.
Press Ctrl+C in this window to stop it.

On the very first run (when the database is empty) a small set of SYNTHETIC
demo records is loaded so there is something to look at. To start with no data
at all, run  python manage.py reset-empty  or set HOTEL_NO_DEMO=1 first.
"""
import os
from datetime import date

from hotel import create_app, db
from hotel.seed import seed_demo

app = create_app()


def load_demo_data_if_first_run() -> None:
    if os.environ.get("HOTEL_NO_DEMO") == "1":
        return
    conn = db.connect(app.config["DATABASE"])
    try:
        if seed_demo(conn, date.today()):
            print("First run: loaded synthetic demo data (invented rooms, guests and reservations).")
    finally:
        conn.close()


if __name__ == "__main__":
    port = int(os.environ.get("HOTEL_PORT", "5000"))
    load_demo_data_if_first_run()
    print(f"Hotel Management System running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    # 127.0.0.1 means "only this computer can connect". Debug mode is off by default.
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("HOTEL_DEBUG") == "1")
