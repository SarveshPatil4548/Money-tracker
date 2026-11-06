"""Test configuration.

The TESTING flag selects a separate database so the suite can never touch
the real money_tracker database. The flag is set BEFORE app.py or db.py is
imported, because both of them read DB_NAME at import time.

If DB_NAME is explicitly set to the production database in the environment,
the whole suite refuses to start.
"""

import os
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# --- 1. the TESTING flag --------------------------------------------------
os.environ["TESTING"] = "1"

# --- 2. refuse to run against the production database ---------------------
_PRODUCTION_NAMES = {"money_tracker", "prod", "production", "money_tracker_prod"}
_requested = (os.environ.get("DB_NAME") or "").strip().lower()
if _requested in _PRODUCTION_NAMES:
    raise RuntimeError(
        "Refusing to run the test suite against the production database "
        f"'{os.environ.get('DB_NAME')}'. Unset DB_NAME, or point it at "
        "'money_tracker_test'."
    )

TEST_DB_NAME = (os.environ.get("TEST_DB_NAME") or "money_tracker_test").strip()
if TEST_DB_NAME.lower() in _PRODUCTION_NAMES:
    raise RuntimeError(
        f"TEST_DB_NAME must not be the production database (got '{TEST_DB_NAME}')."
    )

# This wins over .env because python-dotenv never overrides an existing
# environment variable.
os.environ["DB_NAME"] = TEST_DB_NAME

import db as app_db  # noqa: E402


def _server_connect():
    """Connect to the MySQL server without selecting a database."""
    import pymysql

    config, error = app_db._require_env()
    if error:
        raise RuntimeError(error)
    return pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["user"],
        password=config["password"],
        charset="utf8mb4",
        autocommit=True,
    )


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Create money_tracker_test if it does not exist yet."""
    conn = _server_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{TEST_DB_NAME}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
    finally:
        conn.close()
    return TEST_DB_NAME


@pytest.fixture()
def app(test_database):
    """The Flask app, wired to money_tracker_test, with empty tables."""
    from app import app as flask_app

    flask_app.config.update(TESTING=True)
    with flask_app.app_context():
        _empty_tables()
        yield flask_app
        app_db.db.session.rollback()


def _empty_tables():
    """Wipe the TEST tables so each test starts from a known state."""
    from sqlalchemy import text

    conn = app_db.db.engine.connect()
    try:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in (
            "piggy_statements",
            "piggy_entries",
            "piggy_banks",
            "transactions",
            "budget_alerts",
            "monthly_budgets",
            "budgets",
            "category_deletions",
            "categories",
            "users",
        ):
            conn.execute(text(f"DELETE FROM `{table}`"))
            conn.execute(text(f"ALTER TABLE `{table}` AUTO_INCREMENT = 1"))
        conn.commit()
    finally:
        conn.close()
    app_db.db.session.rollback()


def cat_id(name):
    """Return the id of a seeded category by name (never hard-code ids)."""
    from helpers import cat_id as _cat_id

    return _cat_id(name)


@pytest.fixture()
def client(app):
    return app.test_client()
