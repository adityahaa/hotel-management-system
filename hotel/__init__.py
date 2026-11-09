"""Hotel Management System: a small Flask + SQLite web application."""
from __future__ import annotations

import hmac
import os
import secrets
from datetime import date, datetime
from pathlib import Path

from flask import Flask, abort, g, render_template, request, session

from . import db, services
from .billing import format_money, format_percent
from .validation import NotFoundError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_or_create_secret(path: Path) -> str:
    """Keep a random secret key in instance/ so sessions survive restarts."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        key = secrets.token_hex(32)
        path.write_text(key, encoding="utf-8")
        return key


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        instance_path=str(PROJECT_ROOT / "instance"),
        instance_relative_config=True,
    )
    app.config.from_mapping(
        DATABASE=os.environ.get("HOTEL_DB") or os.path.join(app.instance_path, "hotel.db"),
        SECRET_KEY=os.environ.get("HOTEL_SECRET_KEY"),
        CSRF_ENABLED=True,
        TODAY=None,  # tests can pin "today"; normally the computer's date is used
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_or_create_secret(Path(app.instance_path) / "secret_key")

    db.init_app(app)
    _register_security(app)
    _register_template_helpers(app)
    _register_error_handlers(app)

    from .routes import bp

    app.register_blueprint(bp)
    return app


# ----------------------------------------------------------------- security
def _register_security(app: Flask) -> None:
    def csrf_token() -> str:
        token = session.get("_csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf"] = token
        return token

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_csrf():
        # Every form that changes data is a POST and must carry the session's token.
        if request.method == "POST" and app.config.get("CSRF_ENABLED", True):
            sent = request.form.get("csrf_token", "")
            expected = session.get("_csrf", "")
            if not expected or not hmac.compare_digest(sent, expected):
                abort(400, description="The form has expired or is invalid. Go back, refresh the page and try again.")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


# ---------------------------------------------------------- template helpers
def _register_template_helpers(app: Flask) -> None:
    def settings() -> dict:
        if "settings" not in g:
            g.settings = services.get_settings(db.get_db())
        return g.settings

    @app.context_processor
    def inject_globals():
        return {
            "hotel": settings(),
            "status_labels": services.STATUS_LABELS,
            "current_year": date.today().year,
        }

    @app.template_filter("money")
    def money_filter(cents) -> str:
        return format_money(cents or 0, settings()["currency_symbol"])

    @app.template_filter("percent")
    def percent_filter(value) -> str:
        return format_percent(value)

    @app.template_filter("fmt_date")
    def fmt_date(value) -> str:
        if isinstance(value, str):
            value = date.fromisoformat(value)
        return value.strftime("%d %b %Y")

    @app.template_filter("fmt_day")
    def fmt_day(value) -> str:
        if isinstance(value, str):
            value = date.fromisoformat(value)
        return value.strftime("%a %d %b")

    @app.template_filter("fmt_datetime")
    def fmt_datetime(value) -> str:
        if not value:
            return ""
        return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")


# ------------------------------------------------------------ error handling
def _register_error_handlers(app: Flask) -> None:
    def page(code: int, title: str, message: str):
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(NotFoundError)
    def not_found_record(exc):
        return page(404, "Not found", str(exc))

    @app.errorhandler(404)
    def not_found(_exc):
        return page(404, "Page not found", "That page does not exist. Use the menu to find what you need.")

    @app.errorhandler(400)
    def bad_request(exc):
        return page(400, "Request not accepted", getattr(exc, "description", "") or "The request could not be processed.")

    @app.errorhandler(405)
    def not_allowed(_exc):
        return page(405, "Method not allowed", "That action is not available from this page.")

    @app.errorhandler(500)
    def server_error(_exc):
        return page(500, "Something went wrong", "An unexpected error occurred. The details are in the terminal window running the app.")
