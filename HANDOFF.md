# HANDOFF – Hotel Management System (re-implementation)

> If this session is interrupted, type **continue** and resume from the checklist below.
> This project is a **new implementation** written from the description on the owner's
> resume. It is NOT recovered original code.

## Stack
Python 3 + Flask, SQLite (stdlib `sqlite3`), plain HTML/CSS/JS. Only dependency: Flask (pytest for tests).

## Layout
- `app.py` – starts the server (`python app.py`)
- `manage.py` – `seed-demo`, `reset-demo`, `reset-empty`
- `hotel/` – `__init__.py` (app factory, CSRF), `db.py`, `schema.sql`, `billing.py` (pure money maths),
  `validation.py`, `services.py` (business rules), `routes.py`, `seed.py`, `templates/`, `static/`
- `tests/` – pytest suite

## Status checklist
- [x] Core backend (schema, billing, validation, services, seed) – found already written in the folder; reviewed
- [x] Routes, templates, CSS, JS – exist; need browser verification
- [x] Unit tests for billing / validation / services exist
- [x] Fix 2 wrong parametrised cases in `tests/test_services.py::test_overlapping_dates_are_rejected`
      (cases (2,3) and (1,3) are *adjacent* to a 3->5 booking, not overlapping)
- [x] Add `tests/test_workflow.py` (HTTP-level: add room+guest -> reserve -> conflict -> bill -> status changes, CSRF)
- [x] Run real server, exercise workflow, desktop + mobile screenshots, fix UI problems
- [x] README.md (Windows setup), `run_windows.bat`, requirements pinned sensibly
- [x] Build ZIP (exclude instance/, __pycache__, .pytest_cache)
- [x] GitHub description (in README)

## Design decisions (keep consistent)
- Money = integer minor units (`*_cents`); percentages = Decimal strings.
- Stays are half-open `[check_in, check_out)`; same-day turnover is allowed.
- Rate / tax / service % are **snapshotted on each reservation** when it is created.
- Bill: room = rate x nights; + extras = subtotal; service = subtotal x %; tax = (subtotal+service) x %.
- Statuses: confirmed -> checked_in -> checked_out; confirmed -> cancelled. Check-in only on/after arrival date.
- DB trigger is a second line of defence against overlapping active bookings.
- No payment processing anywhere; the bill must say so.

## Notes
- Cannot test on Windows from this Linux sandbox: Windows commands/.bat are written carefully but untested.

## Progress log
- Tests: 111 passing (billing, validation, services, HTTP workflow). Mutation-checked overlap + tax maths.
- Real browser check (Playwright, desktop + 390px mobile): workflow passes; fixed duplicate availability request in app.js.
- First run of `python app.py` now loads synthetic demo data (opt out: HOTEL_NO_DEMO=1).
- Clean-install test from the ZIP (new venv, pip install, 111 tests, server smoke test) passed on Linux/Python 3.13.
- STATUS: COMPLETE. Only optional ideas remain: edit reservation dates, CSV export, login, run_windows.bat tested on real Windows.
