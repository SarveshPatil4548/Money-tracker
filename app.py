import csv
import hmac
import io
import json
import os
import secrets
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, redirect, session, url_for, flash

# Load .env from next to this file, regardless of the working directory.
ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_FILE)

import db

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

#: Values that must never be used as a real session key.
_SECRET_PLACEHOLDERS = {
    "",
    "dev",
    "dev-secret-key-change-me",
    "replace-with-a-long-random-secret",
    "change-me",
}


def _load_or_create_secret_key():
    """Return a stable strong key, writing one into .env on first run.

    A missing/placeholder key used to leave session cookies signed with a weak
    or changing value, so the browser's cookie no longer matched the server
    and every CSRF token looked expired. This writes the new key once and
    never regenerates it afterwards.
    """
    key = (os.getenv("FLASK_SECRET_KEY") or "").strip()
    if key not in _SECRET_PLACEHOLDERS and len(key) >= 32:
        return key

    key = secrets.token_urlsafe(48)
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    indexes = [
        i for i, line in enumerate(lines)
        if line.strip().startswith("FLASK_SECRET_KEY=")
    ]
    if indexes:
        lines[indexes[0]] = f"FLASK_SECRET_KEY={key}"
        for i in reversed(indexes[1:]):
            del lines[i]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Auto-generated once; keep this key stable so sessions survive restarts.")
        lines.append(f"FLASK_SECRET_KEY={key}")

    try:
        ENV_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"\n[FATAL] Could not save a stable FLASK_SECRET_KEY to {ENV_FILE}: {exc}\n"
        )
    os.environ["FLASK_SECRET_KEY"] = key
    return key


app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
)


def csrf_token():
    """Per-session CSRF token for the category action JSON routes."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _csrf_ok():
    payload = request.get_json(silent=True) or {}
    sent = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or payload.get("csrf_token")
    )
    expected = session.get("_csrf_token")
    return bool(
        expected
        and sent
        and hmac.compare_digest(str(expected), str(sent))
    )


def _csrf_error(message="Your session expired. Refresh the page and try again."):
    return jsonify(ok=False, code="csrf_expired", error=message), 403


@app.before_request
def _session_and_host_guard():
    """Keep the session cookie alive and on one host.

    Use http://localhost:5000 as the canonical dev URL. Page requests that
    arrive on 127.0.0.1 are redirected there so the cookie is not split
    between two hostnames. JSON/fetch requests are left alone so an AJAX call
    never turns into a cross-origin redirect.
    """
    session.permanent = True
    if request.method not in ("GET", "HEAD"):
        return None
    if "application/json" in (request.headers.get("Accept") or ""):
        return None
    if request.host.startswith("127.0.0.1"):
        return redirect(request.url.replace("://127.0.0.1", "://localhost", 1), code=308)
    return None


@app.route("/csrf-token")
def get_csrf_token():
    """Return the current CSRF token so the JS can recover from an expired one."""
    response = jsonify(ok=True, csrf_token=csrf_token())
    response.headers["Cache-Control"] = "no-store"
    return response


app.jinja_env.globals["csrf_token"] = csrf_token


def format_inr(amount):
    """Format a number as Indian Rupees with Indian digit grouping (₹1,25,000.00)."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return "₹0.00"
    negative = amount < 0
    if negative:
        amount = -amount
    rupees, paise = f"{amount:.2f}".split(".")
    if len(rupees) > 3:
        head, tail = rupees[:-3], rupees[-3:]
        groups = []
        while head:
            groups.append(head[-2:])
            head = head[:-2]
        grouped = ",".join(reversed(groups)) + "," + tail
    else:
        grouped = rupees
    return ("-" if negative else "") + f"₹{grouped}.{paise}"


app.jinja_env.filters["inr"] = format_inr

# Connect to MySQL up front and fail loudly if it is unreachable. The app
# never falls back to an in-memory or SQLite store - the settings come from
# DB_* in .env (loaded from the file next to this one). Startup creates only
# the tables that are missing and seeds default categories only when the
# categories table is empty; it never deletes or resets data.
try:
    db.init_db(app)
except db.DatabaseUnavailable as exc:
    raise SystemExit(f"\n[FATAL] {exc}\n")


@app.context_processor
def inject_globals():
    try:
        alerts = db.list_alerts(limit=6)
    except Exception:  # noqa: BLE001 - a notification must never break a page
        app.logger.exception("could not load budget alerts")
        db.db.session.rollback()
        alerts = []
    return {
        "categories": db.list_categories(),
        "today": date.today().isoformat(),
        "budget_alerts": alerts,
        "budget_alert_count": len(alerts),
    }


def icon_for(category_name):
    """Lucide icon name for a known category (fallback: 'tag')."""
    return CATEGORY_ICONS.get(category_name, "tag")


# Lucide icon names for known categories (fallback: "tag").
CATEGORY_ICONS = {
    "Salary": "banknote",
    "Freelance": "laptop",
    "Other Income": "circle-dollar-sign",
    "Rent": "house",
    "Groceries": "shopping-cart",
    "Transport": "bus",
    "Utilities": "lightbulb",
    "Entertainment": "gamepad-2",
    "Dining Out": "utensils",
    "Shopping": "shopping-bag",
    "Health": "heart-pulse",
    "Other Expense": "package",
    "Books": "book-open",
    "Coffee": "coffee",
    "Tuition": "graduation-cap",
    "Snacks": "popcorn",
}

app.jinja_env.globals["icon_for"] = icon_for


def safe_redirect(target, endpoint):
    """Only ever bounce back to a local path."""
    if target and target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for(endpoint))


def save_or_report(label, action):
    """Run a write and turn any failure into a visible error message.

    Returns (saved, result). saved is False when the write failed; the
    database layer has already rolled back, so the user's data is never
    half-written and we tell them plainly that nothing was saved.
    """
    try:
        return True, action()
    except db.DuplicateKeyError as exc:
        flash(str(exc), "error")
        return False, None
    except db.SaveError as exc:
        flash(f"Could not save the {label} - your change was NOT saved. {exc}.", "error")
        return False, None
    except Exception:  # noqa: BLE001 - never show a bare 500 for a save
        db.db.session.rollback()
        flash(
            f"Could not save the {label} - your change was NOT saved. "
            f"Please check the database connection and try again.",
            "error",
        )
        app.logger.exception("failed to save %s", label)
        return False, None


def validate_transaction_form(form, allow_existing_category_id=None):
    """Return (values, error_message). values is None when invalid.

    Archived categories cannot be chosen for new entries, but an existing
    transaction may keep the archived category it already had when edited.
    """
    category_id = form.get("category_id", "").strip()
    amount = form.get("amount", "").strip()
    txn_date = form.get("txn_date", "").strip()
    description = form.get("description", "").strip()

    if not category_id or not amount or not txn_date:
        return None, "Amount, category and date are all required."

    try:
        amount_val = float(amount)
    except ValueError:
        return None, "Amount must be a number."
    if amount_val <= 0:
        return None, "Amount must be greater than 0."

    try:
        date.fromisoformat(txn_date)
    except ValueError:
        return None, "Date must be a valid calendar date."

    if not db.get_category(category_id):
        keeps_existing = (
            allow_existing_category_id is not None
            and str(category_id) == str(allow_existing_category_id)
            and db.get_category_any(category_id) is not None
        )
        if not keeps_existing:
            return None, "That category no longer exists."

    return {
        "category_id": category_id,
        "amount": round(amount_val, 2),
        "description": description,
        "txn_date": txn_date,
    }, None


def last_six_months(today=None):
    """[(year, month)] oldest → newest for the current month and the five before it."""
    today = today or date.today()
    months = []
    year, month = today.year, today.month
    for _ in range(6):
        months.append((year, month))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    months.reverse()
    return months


def group_transactions(txns):
    """Group transactions by date, newest first, with human labels and day totals."""
    groups = []
    index = {}
    today = date.today()
    yesterday = date.fromordinal(today.toordinal() - 1)
    for t in txns:
        key = t["txn_date"]
        if key not in index:
            try:
                parsed = date.fromisoformat(key)
            except ValueError:
                parsed = None
            if parsed == today:
                label = "Today"
            elif parsed == yesterday:
                label = "Yesterday"
            elif parsed is not None:
                label = parsed.strftime("%A, %d %B %Y")
            else:
                label = key
            index[key] = {"date": key, "label": label, "items": [], "net": Decimal("0")}
            groups.append(index[key])
        index[key]["items"].append(t)
        index[key]["net"] += t["amount"] if t["type"] == "expense" else -t["amount"]
    for g in groups:
        g["items"] = list(g["items"])
    return groups


@app.route("/health")
def health():
    """Liveness/readiness probe that confirms the MySQL connection."""
    ok, detail = db.ping()
    if ok:
        return jsonify({"status": "ok", "database": detail}), 200
    return jsonify({"status": "error", "error": detail}), 503


@app.route("/")
def dashboard():
    today = date.today()
    month_start = today.replace(day=1).isoformat()

    income = db.sum_by_type("income", month_start)
    expenses = db.sum_by_type("expense", month_start)

    all_time_income = db.sum_by_type("income")
    all_time_expenses = db.sum_by_type("expense")

    balance = all_time_income - all_time_expenses

    txn_count = db.count_transactions()

    spending = db.spending_by_category(month_start)

    budgets = db.list_budgets_with_spent(month_start)
    budget_overview = db.budget_overview(month_start)
    _refresh_alerts_safely(month_start)
    alerts = db.list_alerts(limit=6)

    recent = db.recent_transactions(8)

    try:
        piggy_overview = db.piggy_overview()
    except Exception:  # noqa: BLE001 - the piggy banner must never break the dashboard
        app.logger.exception("could not load the piggy bank overview")
        db.db.session.rollback()
        piggy_overview = None

    # Income vs expenses, one point per month for the last six months.
    series_start = date(last_six_months()[0][0], last_six_months()[0][1], 1).isoformat()
    rows = db.monthly_series(series_start)
    by_key = {(r["ym"], r["type"]): r["total"] for r in rows}
    months = last_six_months(today)
    chart_labels, chart_periods, series_income, series_expenses = [], [], [], []
    for year, month in months:
        key = f"{year:04d}-{month:02d}"
        first = date(year, month, 1)
        chart_labels.append(first.strftime("%b"))
        chart_periods.append(first.strftime("%B %Y"))
        series_income.append(round(by_key.get((key, "income"), 0), 2))
        series_expenses.append(round(by_key.get((key, "expense"), 0), 2))

    return render_template(
        "dashboard.html",
        income=income,
        expenses=expenses,
        balance=balance,
        txn_count=txn_count,
        spending_by_category=spending,
        budgets=budgets,
        budget_overview=budget_overview,
        budget_alerts=alerts,
        recent=recent,
        piggy=piggy_overview,
        chart_labels=chart_labels,
        chart_periods=chart_periods,
        series_income=series_income,
        series_expenses=series_expenses,
    )


@app.route("/transactions")
def transactions():
    all_txns = db.list_transactions()
    return render_template(
        "transactions.html",
        transactions=all_txns,
        groups=group_transactions(all_txns),
        deleted=db.list_deleted_transactions(),
    )


@app.route("/transactions/add", methods=["GET", "POST"])
def add_transaction():
    if request.method == "POST":
        values, error = validate_transaction_form(request.form)
        if error:
            flash(error, "error")
            if request.form.get("next"):
                return safe_redirect(request.form.get("next"), "transactions")
            return render_template(
                "add_transaction.html",
                today=date.today().isoformat(),
                form=request.form,
            )

        saved, _ = save_or_report(
            "transaction",
            lambda: db.add_transaction(
                values["category_id"],
                values["amount"],
                values["description"],
                values["txn_date"],
            ),
        )
        if not saved:
            return safe_redirect(request.form.get("next"), "transactions")
        _refresh_alerts_safely(values["txn_date"])
        flash("Transaction added.", "success")
        return safe_redirect(request.form.get("next"), "dashboard")

    return render_template(
        "add_transaction.html",
        today=date.today().isoformat(),
        form={},
    )


@app.route("/transactions/<txn_id>/edit", methods=["POST"])
def edit_transaction(txn_id):
    existing = db.get_transaction(txn_id)
    if not existing:
        flash("That transaction no longer exists.", "error")
        return safe_redirect(request.form.get("next"), "transactions")

    values, error = validate_transaction_form(
        request.form, allow_existing_category_id=existing.get("category_id")
    )
    if error:
        flash(error, "error")
        return safe_redirect(request.form.get("next"), "transactions")

    saved, _ = save_or_report(
        "transaction",
        lambda: db.update_transaction(
            txn_id,
            values["category_id"],
            values["amount"],
            values["description"],
            values["txn_date"],
        ),
    )
    if not saved:
        return safe_redirect(request.form.get("next"), "transactions")
    _refresh_alerts_safely(values["txn_date"])
    flash("Transaction updated.", "success")
    return safe_redirect(request.form.get("next"), "transactions")


@app.route("/transactions/<txn_id>/delete", methods=["POST"])
def delete_transaction(txn_id):
    """SOFT delete. The row stays in the table and can be undone."""
    existing = db.get_transaction(txn_id)
    ok, restored = save_or_report("deletion", lambda: db.delete_transaction(txn_id))
    if not ok:
        return safe_redirect(request.form.get("next"), "transactions")
    if restored:
        _refresh_alerts_safely(existing["txn_date"] if existing else None)
        flash("Transaction moved to Recently deleted. You can undo this.", "success")
    else:
        flash("That transaction was not found.", "error")
    return safe_redirect(request.form.get("next"), "transactions")


@app.route("/transactions/<txn_id>/undo", methods=["POST"])
def undo_transaction(txn_id):
    ok, restored = save_or_report("undo", lambda: db.undo_transaction(txn_id))
    if not ok:
        return safe_redirect(request.form.get("next"), "transactions")
    if restored:
        _refresh_alerts_safely()
        flash("Transaction restored.", "success")
    else:
        flash("Nothing to undo for that transaction.", "error")
    return safe_redirect(request.form.get("next"), "transactions")


def _budget_month(value=None):
    raw = value or request.values.get("month") or request.args.get("month")
    try:
        return db.month_key(raw)
    except (TypeError, ValueError):
        return db.month_key()


def _is_json_request():
    if request.is_json:
        return True
    if request.headers.get("X-Requested-With") == "fetch":
        return True
    return request.accept_mimetypes.best == "application/json"


def _budget_payload():
    return request.get_json(silent=True) or request.form


def _as_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _budget_failure(message, month, status=400):
    if _is_json_request():
        return jsonify(ok=False, error=message), status
    flash(message, "error")
    return redirect(url_for("budgets", month=month))


def _budget_ok(message, month, **extra):
    if _is_json_request():
        payload = {"ok": True, "message": message, "month": month}
        payload.update(extra)
        return jsonify(payload)
    flash(message, "success")
    return redirect(url_for("budgets", month=month))


def _refresh_alerts_safely(month=None):
    """Generate budget alerts after a write without ever failing that write."""
    try:
        return db.refresh_budget_alerts(month)
    except Exception:  # noqa: BLE001 - alerts are secondary to the saved data
        app.logger.exception("could not refresh budget alerts")
        db.db.session.rollback()
        return []


@app.route("/budgets")
def budgets():
    month = _budget_month()
    overview = db.budget_overview(month)
    _refresh_alerts_safely(month)
    alerts = db.list_alerts(limit=8)
    expense_categories = db.list_categories_by_type("expense")
    budgeted_ids = {b["category_id"] for b in overview["budgets"]}
    available_categories = [
        c for c in expense_categories if str(c["id"]) not in budgeted_ids
    ]
    copy_preview = db.preview_copy_budgets(month)
    return render_template(
        "budgets.html",
        overview=overview,
        budgets=overview["budgets"],
        month=month,
        month_label=overview["month_label"],
        previous_month=overview["previous_month"],
        next_month=overview["next_month"],
        categories=expense_categories,
        available_categories=available_categories,
        copy_preview=copy_preview,
        alerts=alerts,
        chart_labels=[b["name"] for b in overview["budgets"]],
        chart_spent=[float(b["spent"]) for b in overview["budgets"]],
        chart_limits=[float(b["effective_limit"]) for b in overview["budgets"]],
    )


@app.route("/budgets/add", methods=["POST"])
def add_budget():
    if not _csrf_ok():
        return _csrf_error()
    payload = _budget_payload()
    month = _budget_month(payload.get("month"))
    category_id = payload.get("category_id")
    monthly_limit = payload.get("monthly_limit")
    if not category_id or monthly_limit in (None, ""):
        return _budget_failure("Category and limit are required.", month)
    try:
        budget_id = db.upsert_budget(
            category_id,
            monthly_limit,
            month=month,
            rollover=_as_bool(payload.get("rollover")),
            alert_threshold=payload.get("alert_threshold"),
        )
    except ValueError as exc:
        return _budget_failure(str(exc), month)
    except db.SaveError as exc:
        return _budget_failure(f"Could not save the budget. {exc}.", month, 500)
    _refresh_alerts_safely(month)
    return _budget_ok("Budget saved.", month, id=budget_id)


@app.route("/budgets/<budget_id>/edit", methods=["POST"])
def edit_budget(budget_id):
    if not _csrf_ok():
        return _csrf_error()
    payload = _budget_payload()
    month = _budget_month(payload.get("month"))
    try:
        updated = db.update_budget(
            budget_id,
            monthly_limit=payload.get("monthly_limit"),
            rollover=_as_bool(payload.get("rollover")),
            alert_threshold=payload.get("alert_threshold"),
        )
    except ValueError as exc:
        return _budget_failure(str(exc), month)
    except db.SaveError as exc:
        return _budget_failure(f"Could not save the budget. {exc}.", month, 500)
    if updated is None:
        return _budget_failure("That budget no longer exists.", month, 404)
    _refresh_alerts_safely(updated["month"])
    return _budget_ok("Budget updated.", updated["month"], id=updated["id"])


@app.route("/budgets/<budget_id>/delete", methods=["POST"])
def delete_budget(budget_id):
    if not _csrf_ok():
        return _csrf_error()
    month = _budget_month(_budget_payload().get("month"))
    try:
        removed = db.delete_budget(budget_id)
    except db.SaveError as exc:
        return _budget_failure(f"Could not remove the budget. {exc}.", month, 500)
    if not removed:
        return _budget_failure("That budget no longer exists.", month, 404)
    return _budget_ok("Budget removed.", month)


@app.route("/budgets/<budget_id>/undo", methods=["POST"])
def undo_budget_delete(budget_id):
    if not _csrf_ok():
        return _csrf_error()
    month = _budget_month(_budget_payload().get("month"))
    try:
        restored = db.undo_delete_budget(budget_id)
    except db.SaveError as exc:
        return _budget_failure(f"Could not restore the budget. {exc}.", month, 500)
    if not restored:
        return _budget_failure("That budget could not be restored.", month, 404)
    _refresh_alerts_safely(month)
    return _budget_ok("Budget restored.", month)


@app.route("/budgets/overall", methods=["POST"])
def save_overall_budget():
    if not _csrf_ok():
        return _csrf_error()
    payload = _budget_payload()
    month = _budget_month(payload.get("month"))
    monthly_limit = payload.get("monthly_limit")
    if monthly_limit in (None, ""):
        return _budget_failure("Enter an overall monthly limit.", month)
    try:
        budget_id = db.upsert_overall_budget(
            month,
            monthly_limit,
            alert_threshold=payload.get("alert_threshold"),
        )
    except ValueError as exc:
        return _budget_failure(str(exc), month)
    except db.SaveError as exc:
        return _budget_failure(f"Could not save the overall budget. {exc}.", month, 500)
    _refresh_alerts_safely(month)
    return _budget_ok("Overall budget saved.", month, id=budget_id)


@app.route("/budgets/overall/<budget_id>/delete", methods=["POST"])
def delete_overall_budget(budget_id):
    if not _csrf_ok():
        return _csrf_error()
    month = _budget_month(_budget_payload().get("month"))
    try:
        removed = db.delete_overall_budget(budget_id)
    except db.SaveError as exc:
        return _budget_failure(f"Could not remove the overall budget. {exc}.", month, 500)
    if not removed:
        return _budget_failure("That overall budget no longer exists.", month, 404)
    return _budget_ok("Overall budget removed.", month)


@app.route("/budgets/overall/<budget_id>/undo", methods=["POST"])
def undo_overall_budget_delete(budget_id):
    if not _csrf_ok():
        return _csrf_error()
    month = _budget_month(_budget_payload().get("month"))
    try:
        restored = db.undo_delete_overall_budget(budget_id)
    except db.SaveError as exc:
        return _budget_failure(f"Could not restore the overall budget. {exc}.", month, 500)
    if not restored:
        return _budget_failure("That overall budget could not be restored.", month, 404)
    _refresh_alerts_safely(month)
    return _budget_ok("Overall budget restored.", month)


@app.route("/budgets/copy-last-month", methods=["GET", "POST"])
def copy_last_month_budgets():
    month = _budget_month(_budget_payload().get("month"))
    if request.method == "GET":
        return jsonify(ok=True, preview=db.preview_copy_budgets(month))
    if not _csrf_ok():
        return _csrf_error()
    try:
        result = db.copy_budgets_from_last_month(month)
    except db.SaveError as exc:
        return _budget_failure(f"Could not copy last month's budgets. {exc}.", month, 500)
    count = result["copied"]
    if not count:
        return _budget_ok("Every category already has a budget this month.", month, copied=0)
    return _budget_ok(
        f"Copied {count} budget{'s' if count != 1 else ''} from last month.",
        month,
        copied=count,
    )


@app.route("/budgets/alerts")
def budget_alerts_feed():
    return jsonify(ok=True, alerts=db.list_alerts(limit=20))


@app.route("/budgets/alerts/<alert_id>/dismiss", methods=["POST"])
def dismiss_budget_alert(alert_id):
    if not _csrf_ok():
        return _csrf_error()
    try:
        dismissed = db.dismiss_alert(alert_id)
    except db.SaveError as exc:
        return _budget_failure(f"Could not dismiss the alert. {exc}.", _budget_month(), 500)
    if not dismissed:
        return jsonify(ok=False, error="That alert is already dismissed."), 404
    return jsonify(ok=True)


@app.route("/budgets/alerts/dismiss-all", methods=["POST"])
def dismiss_all_budget_alerts():
    if not _csrf_ok():
        return _csrf_error()
    try:
        count = db.dismiss_all_alerts()
    except db.SaveError as exc:
        return _budget_failure(f"Could not dismiss alerts. {exc}.", _budget_month(), 500)
    return jsonify(ok=True, dismissed=count)


@app.route("/categories")
def categories():
    all_categories = db.list_categories()
    return render_template(
        "categories.html",
        categories=all_categories,
        archived_categories=db.list_archived_categories(),
        deleted_categories=db.list_deleted_categories(),
    )


@app.route("/categories/add", methods=["POST"])
def add_category():
    name = request.form.get("name", "").strip()
    cat_type = request.form.get("type")

    if not name or cat_type not in ("income", "expense"):
        flash("Valid name and type are required.", "error")
        return redirect(url_for("categories"))

    try:
        db.add_category(name, cat_type)
        flash("Category added.", "success")
    except db.DuplicateKeyError:
        flash("That category already exists.", "error")

    return redirect(url_for("categories"))


@app.route("/categories/<category_id>/edit", methods=["POST"])
def edit_category(category_id):
    """Rename a category (JSON). Only categories.name is ever written."""
    if not _csrf_ok():
        return _csrf_error()

    payload = request.get_json(silent=True) or request.form
    name = payload.get("name", "")

    try:
        result = db.rename_category(category_id, name)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc), field="name"), 400
    except db.CategoryNotFoundError as exc:
        db.db.session.rollback()
        return jsonify(ok=False, error=str(exc)), 404
    except db.DuplicateKeyError as exc:
        db.db.session.rollback()
        return jsonify(ok=False, error=str(exc), field="name"), 409
    except db.CategoryActionError as exc:
        db.db.session.rollback()
        return jsonify(ok=False, error=str(exc)), 409
    except db.SaveError as exc:
        db.db.session.rollback()
        return jsonify(
            ok=False,
            error=f"Nothing was changed. The category could not be renamed: {exc}.",
        ), 500
    except Exception:  # noqa: BLE001 - always tell the UI, never a bare 500 page
        db.db.session.rollback()
        app.logger.exception("failed to rename category %s", category_id)
        return jsonify(
            ok=False,
            error="Nothing was changed. Please check the database connection and try again.",
        ), 500

    category = result["category"]
    message = (
        f"Category renamed to '{category['name']}'."
        if result["changed"]
        else "No changes to save."
    )
    return jsonify(
        ok=True,
        changed=result["changed"],
        message=message,
        category=category,
        edit_url=url_for("edit_category", category_id=category_id, _external=True),
    )


def _category_action_urls(category_id):
    """Absolute URLs the browser needs to act on a category without a reload."""
    return {
        "edit_url": url_for("edit_category", category_id=category_id, _external=True),
        "usage_url": url_for("category_usage", category_id=category_id, _external=True),
        "delete_url": url_for("delete_category", category_id=category_id, _external=True),
        "undo_url": url_for("undo_category_delete", category_id=category_id, _external=True),
        "restore_url": url_for("restore_category", category_id=category_id, _external=True),
    }


@app.route("/categories/<category_id>/usage")
def category_usage(category_id):
    """JSON counts for the delete dialog (transactions + budgets)."""
    usage = db.category_usage(category_id)
    if usage is None:
        return jsonify(ok=False, error="That category does not exist."), 404
    usage.update(_category_action_urls(category_id))
    return jsonify(ok=True, usage=usage)


@app.route("/categories/<category_id>/delete", methods=["POST"])
def delete_category(category_id):
    """Soft-delete / archive / reassign-and-delete a category (JSON)."""
    if not _csrf_ok():
        return _csrf_error()

    payload = request.get_json(silent=True) or {}
    action = payload.get("action") or request.form.get("action") or "delete"
    target = payload.get("target_category_id") or request.form.get("target_category_id")

    try:
        result = db.delete_category(category_id, action, target)
    except db.CategoryActionError as exc:
        db.db.session.rollback()
        return jsonify(ok=False, error=str(exc)), 409
    except db.SaveError as exc:
        db.db.session.rollback()
        return jsonify(
            ok=False,
            error=f"Nothing was changed. The category could not be updated: {exc}.",
        ), 500
    except Exception:  # noqa: BLE001 - always tell the UI, never a bare 500 page
        db.db.session.rollback()
        app.logger.exception("failed to delete category %s", category_id)
        return jsonify(
            ok=False,
            error="Nothing was changed. Please check the database connection and try again.",
        ), 500

    message = {
        "delete": "Category moved to Recently deleted. You can undo this.",
        "archive": "Category archived. Old transactions keep their name.",
        "reassign": "Transactions and budgets were moved, then the category was deleted.",
    }[result["action"]]
    response = {
        "ok": True,
        "action": result["action"],
        "message": message,
        "category": result["category"],
        "target": result.get("target"),
        "moved_transactions": result.get("moved_transactions", 0),
    }
    response.update(_category_action_urls(category_id))
    return jsonify(**response)


@app.route("/categories/<category_id>/undo", methods=["POST"])
def undo_category_delete(category_id):
    if not _csrf_ok():
        return _csrf_error()
    try:
        restored = db.undo_delete_category(category_id)
    except db.SaveError as exc:
        return jsonify(ok=False, error=f"Nothing was changed. {exc}."), 500
    except Exception:  # noqa: BLE001
        db.db.session.rollback()
        app.logger.exception("failed to undo category delete %s", category_id)
        return jsonify(ok=False, error="Nothing was changed. Please try again."), 500
    if not restored:
        db.db.session.rollback()
        return jsonify(ok=False, error="Nothing to undo for that category."), 404
    response = {
        "ok": True,
        "message": "Category restored.",
        "category": db.get_category_any(category_id),
    }
    response.update(_category_action_urls(category_id))
    return jsonify(**response)


@app.route("/categories/<category_id>/restore", methods=["POST"])
def restore_category(category_id):
    if not _csrf_ok():
        return _csrf_error()
    try:
        restored = db.restore_archived_category(category_id)
    except db.SaveError as exc:
        return jsonify(ok=False, error=f"Nothing was changed. {exc}."), 500
    except Exception:  # noqa: BLE001
        db.db.session.rollback()
        app.logger.exception("failed to restore category %s", category_id)
        return jsonify(ok=False, error="Nothing was changed. Please try again."), 500
    if not restored:
        db.db.session.rollback()
        return jsonify(ok=False, error="That category is not archived."), 404
    response = {
        "ok": True,
        "message": "Category restored.",
        "category": db.get_category(category_id),
    }
    response.update(_category_action_urls(category_id))
    return jsonify(**response)


# ---------------------------------------------------------------------------
# piggy bank
# ---------------------------------------------------------------------------

def _piggy_payload():
    return request.get_json(silent=True) or request.form


def _piggy_failure(message, month=None, status=400, code=None):
    month = month or _budget_month()
    if _is_json_request():
        payload = {"ok": False, "error": message, "month": month}
        if code:
            payload["code"] = code
        return jsonify(payload), status
    flash(message, "error")
    return redirect(url_for("piggy", month=month))


def _piggy_ok(message, month=None, **extra):
    month = month or _budget_month()
    if _is_json_request():
        payload = {"ok": True, "message": message, "month": month}
        payload.update(extra)
        return jsonify(payload)
    flash(message, "success")
    return redirect(url_for("piggy", month=month))


def _piggy_entry_action(action, label, success_message, month=None):
    """Shared JSON/redirect wrapper for piggy entry writes."""
    try:
        result = action()
    except db.DuplicatePiggyDepositError as exc:
        return _piggy_failure(str(exc), month, 409, code="duplicate_deposit")
    except db.PiggyBalanceError as exc:
        return _piggy_failure(str(exc), month, 409, code="insufficient_balance")
    except db.PiggyNotFoundError as exc:
        return _piggy_failure(str(exc), month, 404, code="not_found")
    except db.PiggyValidationError as exc:
        return _piggy_failure(str(exc), month, 400, code="invalid")
    except db.SaveError as exc:
        return _piggy_failure(
            f"Could not save the {label} - your change was NOT saved. {exc}.",
            month,
            500,
            code="not_saved",
        )
    except Exception:  # noqa: BLE001 - never a bare 500 for a save
        app.logger.exception("failed to save piggy %s", label)
        db.db.session.rollback()
        return _piggy_failure(
            f"Could not save the {label} - your change was NOT saved. "
            "Please check the database connection and try again.",
            month,
            500,
            code="not_saved",
        )
    return _piggy_ok(success_message, month, **(result or {}))


@app.route("/piggy")
def piggy():
    month = _budget_month()
    contributor = request.args.get("contributor") or "all"
    entry_type = request.args.get("type") or "all"
    summary = db.piggy_summary(month)
    entries = db.list_piggy_entries(
        month=month,
        contributor=contributor,
        entry_type=entry_type,
        newest_first=True,
    )
    return render_template(
        "piggy.html",
        summary=summary,
        entries=entries,
        deleted=db.list_deleted_piggy_entries(),
        series=db.piggy_month_series(month),
        month=summary["month_value"],
        month_label=summary["month_label"],
        previous_month=summary["previous_month"],
        next_month=summary["next_month"],
        contributor=contributor,
        entry_type=entry_type,
        today=date.today().isoformat(),
    )


@app.route("/piggy/entries/add", methods=["POST"])
def add_piggy_entry():
    if not _csrf_ok():
        return _csrf_error()
    payload = _piggy_payload()
    month = _budget_month(payload.get("month"))
    kind = str(payload.get("entry_type") or "deposit")
    result = _piggy_entry_action(
        lambda: {
            "entry": db.add_piggy_entry(
                payload.get("contributor"),
                kind,
                payload.get("amount"),
                payload.get("entry_date"),
                payload.get("note"),
                allow_duplicate=_as_bool(payload.get("allow_duplicate")),
            )
        },
        "entry",
        "Deposit saved." if kind == "deposit" else "Withdrawal saved.",
        month,
    )
    return result


@app.route("/piggy/entries/<entry_id>/edit", methods=["POST"])
def edit_piggy_entry(entry_id):
    if not _csrf_ok():
        return _csrf_error()
    payload = _piggy_payload()
    month = _budget_month(payload.get("month"))
    return _piggy_entry_action(
        lambda: {
            "entry": db.update_piggy_entry(
                entry_id,
                contributor=payload.get("contributor"),
                entry_type=payload.get("entry_type"),
                amount=payload.get("amount"),
                entry_date=payload.get("entry_date"),
                note=payload.get("note"),
                allow_duplicate=_as_bool(payload.get("allow_duplicate")),
            )
        },
        "entry",
        "Entry updated.",
        month,
    )


@app.route("/piggy/entries/<entry_id>/delete", methods=["POST"])
def delete_piggy_entry(entry_id):
    if not _csrf_ok():
        return _csrf_error()
    month = _budget_month(_piggy_payload().get("month"))
    return _piggy_entry_action(
        lambda: {"deleted": db.delete_piggy_entry(entry_id)},
        "deletion",
        "Entry moved to Recently deleted. You can undo this.",
        month,
    )


@app.route("/piggy/entries/<entry_id>/undo", methods=["POST"])
def undo_piggy_entry(entry_id):
    if not _csrf_ok():
        return _csrf_error()
    month = _budget_month(_piggy_payload().get("month"))
    return _piggy_entry_action(
        lambda: {"restored": db.undo_delete_piggy_entry(entry_id)},
        "undo",
        "Entry restored.",
        month,
    )


@app.route("/piggy/settings", methods=["POST"])
def update_piggy_settings():
    if not _csrf_ok():
        return _csrf_error()
    payload = _piggy_payload()
    month = _budget_month(payload.get("month"))
    try:
        bank = db.update_piggy_settings(
            name=payload.get("name"),
            daily_amount=payload.get("daily_amount"),
            contributor_me=payload.get("contributor_me"),
            contributor_her=payload.get("contributor_her"),
        )
    except db.PiggyValidationError as exc:
        return _piggy_failure(str(exc), month, 400, code="invalid")
    except db.SaveError as exc:
        return _piggy_failure(
            f"Could not save the settings - your change was NOT saved. {exc}.",
            month,
            500,
            code="not_saved",
        )
    except Exception:  # noqa: BLE001
        app.logger.exception("failed to save piggy settings")
        db.db.session.rollback()
        return _piggy_failure(
            "Could not save the settings - your change was NOT saved. "
            "Please check the database connection and try again.",
            month,
            500,
            code="not_saved",
        )
    return _piggy_ok("Piggy bank settings saved.", month, bank=bank)


@app.route("/piggy/export.csv")
def export_piggy_csv():
    """Download the visible piggy entries as CSV (never touches transactions)."""
    month = request.args.get("month") or None
    contributor = request.args.get("contributor") or "all"
    entry_type = request.args.get("type") or "all"
    rows = db.list_piggy_entries(
        month=month,
        contributor=contributor,
        entry_type=entry_type,
        newest_first=False,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["date", "contributor", "type", "amount", "note", "created_at"])
    for entry in rows:
        writer.writerow(
            [
                entry["entry_date"],
                entry["contributor_name"],
                entry["entry_type"],
                entry["amount"],
                entry["note"],
                entry["created_at"],
            ]
        )
    stamp = date.today().strftime("%Y%m%d")
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="piggy_{stamp}.csv"'
        },
    )


# ---------------------------------------------------------------------------
# exports and backups
# ---------------------------------------------------------------------------

@app.route("/export/transactions.csv")
def export_transactions_csv():
    """Download every visible transaction as CSV."""
    rows = db.list_transactions()
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["id", "date", "category", "type", "amount", "description", "created_at"]
    )
    for t in rows:
        writer.writerow(
            [
                t["id"],
                t["txn_date"],
                t["category"],
                t["type"],
                f"{t['amount']:.2f}",
                t["description"],
                t["created_at"],
            ]
        )
    stamp = date.today().strftime("%Y%m%d")
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="transactions_{stamp}.csv"'
        },
    )


@app.route("/export/transactions.json")
def export_transactions_json():
    """Download every visible transaction as JSON."""
    payload = {
        "exported_at": date.today().isoformat(),
        "database": os.getenv("DB_NAME"),
        "transactions": [
            {
                "id": t["id"],
                "txn_date": t["txn_date"],
                "category": t["category"],
                "type": t["type"],
                "amount": str(t["amount"]),
                "description": t["description"],
                "created_at": t["created_at"],
            }
            for t in db.list_transactions()
        ],
    }
    stamp = date.today().strftime("%Y%m%d")
    return Response(
        json.dumps(payload, indent=2),
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="transactions_{stamp}.json"'
        },
    )


@app.route("/export/backup.sql")
def export_backup_sql():
    """Download a full mysqldump of the database."""
    import backup as backup_tool

    try:
        data = backup_tool.dump_to_bytes()
    except Exception as exc:  # noqa: BLE001 - report, never 500 silently
        flash(f"Could not create the backup: {exc}", "error")
        return redirect(url_for("transactions"))

    stamp = date.today().strftime("%Y%m%d_%H%M%S")
    name = os.getenv("DB_NAME", "money_tracker")
    return Response(
        data,
        mimetype="application/sql",
        headers={
            "Content-Disposition": f'attachment; filename="{name}_{stamp}.sql"'
        },
    )


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404,
                           message="That page doesn't exist."), 404


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500,
                           message="Something went wrong on our side."), 500


def run_startup_backup():
    """Take a backup every time the app starts. Never crashes the app."""
    if db.testing_enabled():
        print("Startup backup skipped (TESTING is set).")
        return None
    try:
        import backup as backup_tool

        target, removed = backup_tool.backup_database()
        print(f"Startup backup written: {target}")
        for old in removed:
            print(f"  removed old backup: {old.name}")
        return target
    except Exception as exc:  # noqa: BLE001 - a failed backup must not stop the app
        print(f"WARNING: startup backup FAILED: {exc}", flush=True)
        return None


with app.app_context():
    name, counts, report = db.startup_report()
    print("Startup database report:")
    print(report, flush=True)

run_startup_backup()

if __name__ == "__main__":
    app.run(debug=True)
