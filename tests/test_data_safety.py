"""Data-safety tests.

These lock in the guarantees that were missing when real data was lost:
startup never removes data, tests never touch the production database,
writes either commit or roll back, and a delete is reversible.
"""

import os

import pytest
from sqlalchemy import text

import db as app_db
from db import SaveError
from helpers import cat_id, seed_categories


# ---------------------------------------------------------------------------
# the production database is off limits under TESTING
# ---------------------------------------------------------------------------

def test_guard_refuses_production_database_under_testing():
    assert app_db.testing_enabled() is True
    error = app_db._guard_database({"name": "money_tracker"})
    assert error is not None
    assert "money_tracker" in error


def test_guard_allows_the_test_database():
    assert app_db._guard_database({"name": "money_tracker_test"}) is None


def test_tests_run_against_a_separate_database():
    assert os.environ["DB_NAME"] == "money_tracker_test"
    assert os.environ["DB_NAME"] != app_db.PRODUCTION_DB_NAME


# ---------------------------------------------------------------------------
# startup never removes anything
# ---------------------------------------------------------------------------

def test_startup_does_not_delete_existing_rows(app):
    seed_categories()
    app_db.db.session.add(
        app_db.Transaction(
            user_id=None,
            category_id=cat_id("Groceries"),
            amount=app_db.Decimal("12.34"),
            description="keep me",
            txn_date=app_db.date(2026, 1, 1),
        )
    )
    app_db.db.session.commit()
    before = app_db.count_transactions()

    # simulate a restart: create missing tables + apply upgrades
    app_db._create_missing_tables()
    app_db._ensure_schema()
    app_db._seed_categories()

    assert app_db.count_transactions() == before == 1
    assert app_db.db.session.execute(
        text("SELECT description FROM transactions")
    ).scalar() == "keep me"


def test_create_missing_tables_never_drops_anything(app):
    seed_categories()
    app_db.db.session.add(
        app_db.Transaction(
            user_id=None,
            category_id=cat_id("Groceries"),
            amount=app_db.Decimal("1.00"),
            description="still here",
            txn_date=app_db.date(2026, 1, 1),
        )
    )
    app_db.db.session.commit()

    inspector = app_db.inspect(app_db.db.engine)
    assert "transactions" in inspector.get_table_names()

    app_db._create_missing_tables()

    inspector = app_db.inspect(app_db.db.engine)
    assert "transactions" in inspector.get_table_names()
    assert app_db.count_transactions() == 1


def test_categories_are_seeded_only_when_the_table_is_empty(app):
    # the table is empty -> seeding fills it
    assert app_db.db.session.execute(
        text("SELECT COUNT(*) FROM categories")
    ).scalar() == 0
    app_db._seed_categories()
    seeded = app_db.db.session.execute(
        text("SELECT COUNT(*) FROM categories")
    ).scalar()
    assert seeded == len(app_db.DEFAULT_CATEGORIES)

    # now it is not empty -> seeding must do nothing
    app_db._seed_categories()
    again = app_db.db.session.execute(
        text("SELECT COUNT(*) FROM categories")
    ).scalar()
    assert again == seeded


def test_ensure_schema_is_idempotent(app):
    first = app_db._ensure_schema()
    second = app_db._ensure_schema()
    assert second == []
    assert isinstance(first, list)


def test_ensure_schema_makes_budget_category_fk_restrict(app):
    app_db._ensure_schema()
    constraints = app_db._budget_category_constraints()
    assert constraints == [("fk_budgets_category", "RESTRICT")]


# ---------------------------------------------------------------------------
# no SQLite / in-memory fallback
# ---------------------------------------------------------------------------

def test_connection_url_is_always_mysql():
    url = app_db.build_database_url(
        {"user": "u", "password": "p", "host": "h", "port": 3306, "name": "d"}
    )
    assert url.startswith("mysql+pymysql://")
    assert "sqlite" not in url
    assert ":memory:" not in url


def test_missing_env_fails_loudly(monkeypatch):
    for var in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    config, error = app_db._require_env()
    assert config is None
    assert error and "DB_HOST" in error


def test_startup_refuses_when_mysql_is_unreachable(monkeypatch):
    """A bad host must raise DatabaseUnavailable, never fall back to SQLite."""
    from sqlalchemy.exc import OperationalError

    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "1")
    monkeypatch.setenv("DB_USER", "nobody")
    monkeypatch.setenv("DB_PASSWORD", "nothing")
    monkeypatch.setenv("DB_NAME", "money_tracker_test")
    monkeypatch.delenv("TESTING", raising=False)

    from flask import Flask

    with pytest.raises(app_db.DatabaseUnavailable):
        app_db.init_db(Flask("unreachable"))


# ---------------------------------------------------------------------------
# writes commit or roll back, and say so
# ---------------------------------------------------------------------------

def test_failed_write_rolls_back_and_raises_save_error(app, monkeypatch):
    seed_categories()
    app_db.db.session.add(
        app_db.Transaction(
            user_id=None,
            category_id=cat_id("Groceries"),
            amount=app_db.Decimal("5.00"),
            description="doomed",
            txn_date=app_db.date(2026, 2, 2),
        )
    )

    def boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(app_db.db.session, "commit", boom)

    with pytest.raises(SaveError):
        app_db._commit()

    # nothing was persisted
    app_db.db.session.rollback()
    assert app_db.count_transactions() == 0


def test_transaction_add_and_persist(app):
    seed_categories()
    app_db.add_transaction(cat_id("Groceries"), "10.00", "lunch", "2026-03-03")
    assert app_db.count_transactions() == 1
    row = app_db.list_transactions()[0]
    assert row["description"] == "lunch"
    assert row["amount"] == app_db.Decimal("10.00")


# ---------------------------------------------------------------------------
# soft delete and undo
# ---------------------------------------------------------------------------

def test_delete_is_soft_and_undo_restores_it(app):
    seed_categories()
    app_db.add_transaction(
        cat_id("Groceries"), "40.00", "Potatoes", "2026-10-20"
    )
    txn_id = app_db.list_transactions()[0]["id"]

    total_before = app_db.db.session.execute(
        text("SELECT COUNT(*) FROM transactions")
    ).scalar()

    assert app_db.delete_transaction(txn_id) is True

    # the row is still physically there ...
    total_after = app_db.db.session.execute(
        text("SELECT COUNT(*) FROM transactions")
    ).scalar()
    assert total_after == total_before
    # ... but it is no longer visible
    assert app_db.count_transactions() == 0
    assert app_db.list_transactions() == []
    deleted = app_db.list_deleted_transactions()
    assert len(deleted) == 1
    assert deleted[0]["description"] == "Potatoes"

    # undo brings it back
    assert app_db.undo_transaction(txn_id) is True
    assert app_db.count_transactions() == 1
    assert app_db.list_transactions()[0]["description"] == "Potatoes"


def test_deleted_transactions_are_excluded_from_totals(app):
    seed_categories()
    app_db.add_transaction(cat_id("Salary"), "100.00", "income", "2026-04-01")
    app_db.add_transaction(cat_id("Groceries"), "30.00", "expense", "2026-04-01")
    income_id = next(
        t["id"] for t in app_db.list_transactions() if t["description"] == "income"
    )

    assert app_db.sum_by_type("income") == app_db.Decimal("100.00")

    app_db.delete_transaction(income_id)
    assert app_db.sum_by_type("income") == app_db.Decimal("0.00")
    assert app_db.sum_by_type("expense") == app_db.Decimal("30.00")


def test_undo_on_a_live_transaction_is_a_no_op(app):
    seed_categories()
    app_db.add_transaction(cat_id("Groceries"), "10.00", "live", "2026-04-01")
    txn_id = app_db.list_transactions()[0]["id"]
    assert app_db.undo_transaction(txn_id) is False
    assert app_db.count_transactions() == 1
