"""One place to ask "what is today's date?" so tests can pin it."""
from __future__ import annotations

from datetime import date

from flask import current_app


def today() -> date:
    return current_app.config.get("TODAY") or date.today()
