"""One-time import of existing Money Tracker data into MySQL.

MANUAL ONLY. This script is never called by the app and must never be
called automatically: importing it as a module does nothing, and running it
without --yes only prints a preview of what would be imported.

Sources, checked in order:
  1. money_tracker.db  - the old SQLite database (project root)
  2. data/*.json       - optional JSON exports, shape:
                         {"categories": [...], "transactions": [...], "budgets": [...]}

Run from the project root:
    python scripts/import_to_mysql.py            # preview only, changes nothing
    python scripts/import_to_mysql.py --yes      # actually import

Safe to re-run: rows already present (matched on their natural key) are
skipped, so running it twice will not create duplicates. The MySQL password
is read only from .env and is never printed.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from flask import Flask  # noqa: E402

import db as app_db  # noqa: E402

SQLITE_PATH = BASE_DIR / "money_tracker.db"
JSON_DIR = BASE_DIR / "data"


def _dec(value):
    """SQLite REAL / JSON number -> Decimal(12,2)."""
    return Decimal(str(round(float(value or 0), 2)))


def _parse_date(value):
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    return date.fromisoformat(text)


def load_sqlite():
    """Return (categories, transactions, budgets) lists of dicts, or None."""
    if not SQLITE_PATH.exists():
        return None
    con = sqlite3.connect(SQLITE_PATH)
    con.row_factory = sqlite3.Row
    try:
        categories = [dict(r) for r in con.execute("SELECT * FROM categories")]
        transactions = [dict(r) for r in con.execute("SELECT * FROM transactions")]
        budgets = [dict(r) for r in con.execute("SELECT * FROM budgets")]
    finally:
        con.close()
    return categories, transactions, budgets


def load_json():
    """Merge any data/*.json exports; returns None when no JSON found."""
    if not JSON_DIR.is_dir():
        return None
    categories, transactions, budgets = [], [], []
    found = False
    for path in sorted(JSON_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! skipping {path.name}: {exc}")
            continue
        found = True
        categories.extend(payload.get("categories", []))
        transactions.extend(payload.get("transactions", []))
        budgets.extend(payload.get("budgets", []))
    if not found:
        return None
    return categories, transactions, budgets


def import_rows(categories, transactions, budgets):
    db = app_db.db
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    imported = {"categories": 0, "transactions": 0, "budgets": 0, "skipped": 0}

    with db.session.begin():
        # ---- categories: match on (user_id IS NULL, name) ----
        cat_id_map = {}
        existing_cats = {
            c.name: c.id
            for c in db.session.query(app_db.Category)
            .filter(app_db.Category.user_id.is_(None))
            .all()
        }
        for row in categories:
            name = (row.get("name") or "").strip()
            kind = (row.get("type") or "").strip()
            if not name or kind not in ("income", "expense"):
                continue
            if name in existing_cats:
                cat_id_map[row.get("id")] = existing_cats[name]
                continue
            new_cat = app_db.Category(user_id=None, name=name, type=kind, created_at=now_utc)
            db.session.add(new_cat)
            db.session.flush()
            cat_id_map[row.get("id")] = new_cat.id
            existing_cats[name] = new_cat.id
            imported["categories"] += 1

        # ---- transactions: skip exact duplicates ----
        seen = {
            (t.category_id, t.amount, t.txn_date, t.description)
            for t in db.session.query(app_db.Transaction).all()
        }
        for row in transactions:
            old_cat = row.get("category_id")
            new_cat = cat_id_map.get(old_cat)
            if new_cat is None:
                imported["skipped"] += 1
                continue
            amount = _dec(row.get("amount"))
            txn_date = _parse_date(row.get("txn_date"))
            description = row.get("description") or ""
            key = (new_cat, amount, txn_date, description)
            if key in seen:
                imported["skipped"] += 1
                continue
            created = row.get("created_at")
            if created:
                try:
                    created = datetime.fromisoformat(str(created))
                    if created.tzinfo is not None:
                        created = created.astimezone(timezone.utc).replace(tzinfo=None)
                except ValueError:
                    created = now_utc
            else:
                created = now_utc
            db.session.add(
                app_db.Transaction(
                    user_id=None,
                    category_id=new_cat,
                    amount=amount,
                    description=description,
                    txn_date=txn_date,
                    created_at=created,
                )
            )
            seen.add(key)
            imported["transactions"] += 1

        # ---- budgets: one per category, limit overwritten on re-import ----
        for row in budgets:
            new_cat = cat_id_map.get(row.get("category_id"))
            if new_cat is None:
                imported["skipped"] += 1
                continue
            limit = _dec(row.get("monthly_limit"))
            if limit <= 0:
                imported["skipped"] += 1
                continue
            existing = (
                db.session.query(app_db.Budget)
                .filter(app_db.Budget.category_id == new_cat)
                .with_for_update()
                .first()
            )
            if existing is None:
                db.session.add(
                    app_db.Budget(
                        user_id=None, category_id=new_cat,
                        monthly_limit=limit, created_at=now_utc,
                    )
                )
                imported["budgets"] += 1
            else:
                if existing.monthly_limit != limit:
                    existing.monthly_limit = limit
                    imported["budgets"] += 1

    return imported


def main():
    parser = argparse.ArgumentParser(
        description="Import existing Money Tracker data into MySQL (manual only)."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually perform the import (without it, nothing is written)",
    )
    args = parser.parse_args()

    # Belt and braces: this script must never run as part of app startup.
    if str(os.environ.get("MONEY_TRACKER_AUTO_IMPORT", "")).strip().lower() in (
        "1", "true", "yes", "on",
    ):
        raise SystemExit(
            "[FATAL] Automatic import is disabled. Run this script by hand."
        )

    mini_app = Flask("money-tracker-import")
    try:
        app_db.init_db(mini_app)
    except app_db.DatabaseUnavailable as exc:
        raise SystemExit(f"\n[FATAL] {exc}\n")

    source = load_sqlite()
    kind = "SQLite (money_tracker.db)"
    if source is None:
        source = load_json()
        kind = "JSON (data/*.json)"
    if source is None:
        print("No existing data found (money_tracker.db or data/*.json). Nothing to import.")
        return 0

    categories, transactions, budgets = source
    print(f"Source: {kind}")
    print(f"Importing from {kind}: "
          f"{len(categories)} categories, {len(transactions)} transactions, "
          f"{len(budgets)} budgets")

    if not args.yes:
        print("\nPREVIEW ONLY - nothing was written.")
        print("Re-run with --yes to perform the import.")
        return 0

    with mini_app.app_context():
        counts = import_rows(categories, transactions, budgets)
    print(
        "Done. New rows -> "
        f"categories: {counts['categories']}, "
        f"transactions: {counts['transactions']}, "
        f"budgets: {counts['budgets']} "
        f"(already present, skipped: {counts['skipped']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
