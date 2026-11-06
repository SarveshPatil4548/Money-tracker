import calendar
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

db = SQLAlchemy()

CONNECT_TIMEOUT_SECONDS = 5

#: The database that holds real user data. Nothing may write to it while
#: running under TESTING.
PRODUCTION_DB_NAME = "money_tracker"

#: The database the test-suite is allowed to use.
TEST_DB_NAME = "money_tracker_test"

#: Truthy values for the TESTING environment flag.
_TRUTHY = {"1", "true", "yes", "on"}


class DatabaseUnavailable(RuntimeError):
    """Raised when the app cannot reach MySQL, or the config is unsafe."""


class DuplicateKeyError(RuntimeError):
    """Raised when a unique constraint is violated (e.g. duplicate category)."""


class SaveError(RuntimeError):
    """Raised when a write could not be committed. The change was NOT saved."""


class CategoryActionError(RuntimeError):
    """Raised when a category deletion/reassign/archive is not allowed."""


class CategoryNotFoundError(CategoryActionError):
    """Raised when a category does not exist (or is no longer active)."""


class PiggyError(RuntimeError):
    """Base class for piggy-bank validation and action failures."""


class PiggyNotFoundError(PiggyError):
    """Raised when a piggy bank or entry does not exist (or was deleted)."""


class PiggyValidationError(PiggyError):
    """Raised when piggy-bank input is missing or invalid."""


class PiggyBalanceError(PiggyValidationError):
    """Raised when a withdrawal would take the piggy bank below zero."""


class DuplicatePiggyDepositError(PiggyError):
    """Raised when a contributor already deposited on that date."""


#: Longest category name the UI and the server will accept.
CATEGORY_NAME_MAX = 40

#: Piggy-bank defaults. Money is always DECIMAL(12,2).
PIGGY_DAILY_DEFAULT = Decimal("50.00")
PIGGY_MAX_AMOUNT = Decimal("1000000000.00")
PIGGY_MIN_AMOUNT = Decimal("0.01")
PIGGY_NAME_MAX = 120
PIGGY_CONTRIBUTOR_MAX = 80
PIGGY_NOTE_MAX = 255
PIGGY_CONTRIBUTORS = ("me", "her")
PIGGY_ENTRY_TYPES = ("deposit", "withdrawal")

#: Budget limits are DECIMAL(12,2); keep a sensible ceiling for the UI/server.
MAX_BUDGET_AMOUNT = Decimal("1000000000.00")
MIN_BUDGET_AMOUNT = Decimal("0.01")
DEFAULT_ALERT_THRESHOLD = 80

#: Fallback categories that must always remain available for a type.
#: Names are compared case-insensitively after collapsing whitespace.
_PROTECTED_CATEGORY_NAMES = {"uncategorised", "uncategorized", "other"}

DEFAULT_CATEGORIES = [
    ("Salary", "income"),
    ("Freelance", "income"),
    ("Other Income", "income"),
    ("Rent", "expense"),
    ("Groceries", "expense"),
    ("Transport", "expense"),
    ("Utilities", "expense"),
    ("Entertainment", "expense"),
    ("Dining Out", "expense"),
    ("Shopping", "expense"),
    ("Health", "expense"),
    ("Other Expense", "expense"),
]

# Holds the active connection settings so error messages can be masked.
_ACTIVE_CONFIG = {}


def _utcnow():
    """Naive UTC timestamp - MySQL DATETIME columns have no timezone."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_protected_category(row):
    """True for a fallback category such as Other Income / Uncategorised."""
    if row is None:
        return False
    name = " ".join(str(row.name).strip().lower().split())
    if name in _PROTECTED_CATEGORY_NAMES:
        return True
    if row.type == "income" and name == "other income":
        return True
    if row.type == "expense" and name == "other expense":
        return True
    return False


# ---------------------------------------------------------------------------
# models (mirrors schema.sql exactly)
# ---------------------------------------------------------------------------

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(Integer, primary_key=True)
    email = db.Column(String(255), nullable=False, unique=True)
    name = db.Column(String(120), nullable=False)
    password_hash = db.Column(String(255), nullable=True)
    timezone = db.Column(String(64), nullable=False, default="UTC")
    base_currency = db.Column(String(3), nullable=False, default="INR")
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = db.Column(String(80), nullable=False)
    type = db.Column(String(16), nullable=False)
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)
    # Soft delete / archive. The row (and its transactions) always stay in
    # the database so old history and reports keep working.
    archived_at = db.Column(DateTime, nullable=True)
    deleted_at = db.Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_categories_user_name"),
        Index("idx_categories_user_id", "user_id"),
        Index("idx_categories_type_name", "type", "name"),
        Index("idx_categories_archived_deleted", "archived_at", "deleted_at"),
        CheckConstraint("type IN ('income', 'expense')", name="chk_categories_type"),
    )


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    category_id = db.Column(Integer, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True)
    amount = db.Column(Numeric(12, 2), nullable=False)
    description = db.Column(String(255), nullable=True)
    txn_date = db.Column(Date, nullable=False)
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)
    # Soft delete. A deleted transaction keeps its row so it can be undone
    # and is still covered by every backup.
    deleted_at = db.Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_transactions_user_id", "user_id"),
        Index("idx_transactions_date", "txn_date"),
        Index("idx_transactions_category_id", "category_id"),
        CheckConstraint("amount > 0", name="chk_transactions_amount"),
        CheckConstraint("category_id IS NOT NULL", name="chk_transactions_kind"),
    )

    category = db.relationship("Category", foreign_keys=[category_id])


class Budget(db.Model):
    __tablename__ = "budgets"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    # RESTRICT (never CASCADE): a category delete must never silently remove
    # a budget. The app reassigns or removes budgets explicitly first.
    category_id = db.Column(
        Integer,
        ForeignKey(
            "categories.id",
            ondelete="RESTRICT",
            name="fk_budgets_category",
        ),
        nullable=False,
    )
    month = db.Column(Date, nullable=False, default=lambda: date.today().replace(day=1))
    monthly_limit = db.Column(Numeric(12, 2), nullable=False)
    rollover = db.Column(Boolean, nullable=False, default=False)
    alert_threshold = db.Column(Integer, nullable=False, default=DEFAULT_ALERT_THRESHOLD)
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)
    deleted_at = db.Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("category_id", "month", name="uq_budgets_category_month"),
        Index("idx_budgets_user_id", "user_id"),
        Index("idx_budgets_month_deleted", "month", "deleted_at"),
        CheckConstraint("monthly_limit > 0", name="chk_budgets_limit"),
        CheckConstraint(
            "alert_threshold BETWEEN 1 AND 100",
            name="chk_budgets_threshold",
        ),
    )


class MonthlyBudget(db.Model):
    """The overall spending limit for one calendar month."""

    __tablename__ = "monthly_budgets"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    month = db.Column(Date, nullable=False, default=lambda: date.today().replace(day=1))
    monthly_limit = db.Column(Numeric(12, 2), nullable=False)
    alert_threshold = db.Column(Integer, nullable=False, default=DEFAULT_ALERT_THRESHOLD)
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)
    deleted_at = db.Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("month", name="uq_monthly_budgets_month"),
        Index("idx_monthly_budgets_user_id", "user_id"),
        Index("idx_monthly_budgets_month_deleted", "month", "deleted_at"),
        CheckConstraint("monthly_limit > 0", name="chk_monthly_budgets_limit"),
        CheckConstraint(
            "alert_threshold BETWEEN 1 AND 100",
            name="chk_monthly_budgets_threshold",
        ),
    )


class BudgetAlert(db.Model):
    """One notification event per budget (or overall budget) per month/level.

    The table is deliberately independent of the notification channel so an
    email/push worker can later read undelivered rows and mark them sent
    without changing the budget code.
    """

    __tablename__ = "budget_alerts"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    budget_key = db.Column(String(64), nullable=False)
    scope = db.Column(String(16), nullable=False)
    category_id = db.Column(Integer, nullable=True)
    month = db.Column(Date, nullable=False)
    level = db.Column(String(16), nullable=False)
    message = db.Column(String(255), nullable=False)
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)
    dismissed_at = db.Column(DateTime, nullable=True)
    email_sent_at = db.Column(DateTime, nullable=True)
    push_sent_at = db.Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("budget_key", "month", "level", name="uq_budget_alerts_key_month_level"),
        Index("idx_budget_alerts_dismissed", "dismissed_at", "created_at"),
        Index("idx_budget_alerts_month", "month"),
        CheckConstraint("scope IN ('category', 'overall')", name="chk_budget_alerts_scope"),
        CheckConstraint("level IN ('threshold', 'over')", name="chk_budget_alerts_level"),
    )


class CategoryDeletion(db.Model):
    """Audit/undo trail for a soft-deleted category.

    It records exactly which transactions were reassigned so Undo can safely
    move only those rows back, and enough budget detail to put the old budget
    back without touching budgets the user created afterwards.
    """

    __tablename__ = "category_deletions"

    id = db.Column(Integer, primary_key=True)
    category_id = db.Column(
        Integer, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False
    )
    target_category_id = db.Column(
        Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    action = db.Column(String(16), nullable=False)
    transaction_ids = db.Column(Text, nullable=True)
    budget_id = db.Column(Integer, nullable=True)
    budget_moved = db.Column(Boolean, nullable=False, default=False)
    budget_limit = db.Column(Numeric(12, 2), nullable=True)
    target_budget_id = db.Column(Integer, nullable=True)
    target_budget_before = db.Column(Numeric(12, 2), nullable=True)
    budget_changes = db.Column(Text, nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)
    undone_at = db.Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_category_deletions_category", "category_id"),
    )


class PiggyBank(db.Model):
    """One shared piggy bank with editable contributor names.

    The piggy bank is deliberately separate from transactions, budgets and the
    dashboard totals: nothing in the normal money tracker reads these rows.
    """

    __tablename__ = "piggy_banks"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = db.Column(String(PIGGY_NAME_MAX), nullable=False, default="Our Piggy Bank")
    daily_amount = db.Column(
        Numeric(12, 2), nullable=False, default=PIGGY_DAILY_DEFAULT
    )
    contributor_me = db.Column(String(PIGGY_CONTRIBUTOR_MAX), nullable=False, default="Me")
    contributor_her = db.Column(
        String(PIGGY_CONTRIBUTOR_MAX), nullable=False, default="Her"
    )
    currency = db.Column(String(3), nullable=False, default="INR")
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    # Soft delete only: the bank and its history always stay in the database.
    deleted_at = db.Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_piggy_banks_user_id", "user_id"),
        Index("idx_piggy_banks_deleted_at", "deleted_at"),
        CheckConstraint("daily_amount > 0", name="chk_piggy_banks_daily_amount"),
    )


class PiggyEntry(db.Model):
    """A deposit into or a withdrawal from the piggy bank.

    Rows are soft deleted; a withdrawal is rejected when the running balance
    would become negative at any point.
    """

    __tablename__ = "piggy_entries"

    id = db.Column(Integer, primary_key=True)
    piggy_bank_id = db.Column(
        Integer,
        ForeignKey(
            "piggy_banks.id",
            ondelete="RESTRICT",
            name="fk_piggy_entries_bank",
        ),
        nullable=False,
    )
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    contributor = db.Column(String(8), nullable=False)
    entry_type = db.Column(String(16), nullable=False)
    amount = db.Column(Numeric(12, 2), nullable=False)
    entry_date = db.Column(Date, nullable=False)
    note = db.Column(String(PIGGY_NOTE_MAX), nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at = db.Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_piggy_entries_bank_id", "piggy_bank_id"),
        Index("idx_piggy_entries_entry_date", "entry_date"),
        Index("idx_piggy_entries_bank_date", "piggy_bank_id", "entry_date"),
        Index("idx_piggy_entries_contributor_date", "contributor", "entry_date"),
        Index("idx_piggy_entries_deleted_at", "deleted_at"),
        CheckConstraint("amount > 0", name="chk_piggy_entries_amount"),
        CheckConstraint(
            "contributor IN ('me', 'her')", name="chk_piggy_entries_contributor"
        ),
        CheckConstraint(
            "entry_type IN ('deposit', 'withdrawal')",
            name="chk_piggy_entries_type",
        ),
    )

    piggy_bank = db.relationship("PiggyBank", foreign_keys=[piggy_bank_id])


class PiggyStatement(db.Model):
    """A generated PDF statement plus its sha256 for integrity checks."""

    __tablename__ = "piggy_statements"

    id = db.Column(Integer, primary_key=True)
    piggy_bank_id = db.Column(
        Integer,
        ForeignKey(
            "piggy_banks.id",
            ondelete="RESTRICT",
            name="fk_piggy_statements_bank",
        ),
        nullable=False,
    )
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    period_start = db.Column(Date, nullable=False)
    period_end = db.Column(Date, nullable=False)
    opening_balance = db.Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    closing_balance = db.Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    total_deposits = db.Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    total_withdrawals = db.Column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    entry_count = db.Column(Integer, nullable=False, default=0)
    file_name = db.Column(String(255), nullable=False)
    file_path = db.Column(String(512), nullable=False)
    file_sha256 = db.Column(String(64), nullable=False)
    generated_at = db.Column(DateTime, nullable=False, default=_utcnow)
    created_at = db.Column(DateTime, nullable=False, default=_utcnow)
    deleted_at = db.Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_piggy_statements_bank_period", "piggy_bank_id", "period_start", "period_end"),
        Index("idx_piggy_statements_hash", "file_sha256"),
        Index("idx_piggy_statements_deleted_at", "deleted_at"),
        CheckConstraint(
            "period_end >= period_start", name="chk_piggy_statements_period"
        ),
        CheckConstraint(
            "opening_balance >= 0 AND closing_balance >= 0",
            name="chk_piggy_statements_balances",
        ),
        CheckConstraint(
            "total_deposits >= 0 AND total_withdrawals >= 0",
            name="chk_piggy_statements_totals",
        ),
        CheckConstraint("entry_count >= 0", name="chk_piggy_statements_count"),
    )


# ---------------------------------------------------------------------------
# environment / connection
# ---------------------------------------------------------------------------

def _mask_secret(message, password=None):
    """Never let the password appear in any printed message."""
    if message is None:
        return message
    if password is None:
        password = _ACTIVE_CONFIG.get("password")
    masked = str(message)
    if password:
        masked = masked.replace(password, "***")
    # also mask credentials embedded in URLs, e.g. mysql+pymysql://user:pass@host
    masked = re.sub(r"(://[^:/@\s]+:)[^@\s]+@", r"\1***@", masked)
    return masked


def testing_enabled():
    """True when the TESTING env flag is set."""
    return str(os.getenv("TESTING", "")).strip().lower() in _TRUTHY


def _require_env():
    """Return (config, error_message). error_message names the var and file."""
    env_file = BASE_DIR / ".env"
    missing = []
    for var in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"):
        if os.getenv(var) is None:
            missing.append(var)

    testing = testing_enabled()
    name = os.getenv("DB_NAME")
    if name is None:
        if testing:
            # Tests default to the dedicated test database, never to prod.
            name = TEST_DB_NAME
        else:
            missing.append("DB_NAME")

    if missing:
        vars_text = ", ".join(missing)
        return None, (
            f"Missing environment variable(s): {vars_text}. "
            f"Copy .env.example to {env_file} and fill them in."
        )

    config = {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT") or 3306),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD") or "",
        "name": name,
    }
    return config, None


def _guard_database(config):
    """Refuse unsafe configurations. Returns an error string or None.

    - Under TESTING the production database is off limits.
    - The connection URL must be MySQL. There is no SQLite/in-memory
      fallback, by design.
    """
    if testing_enabled() and config["name"] == PRODUCTION_DB_NAME:
        return (
            f"TESTING is set but DB_NAME is '{PRODUCTION_DB_NAME}'. "
            f"Refusing to touch the production database. Point DB_NAME at "
            f"'{TEST_DB_NAME}' (or another non-production database) instead."
        )
    if not config["name"] or config["name"].lower() in ("test", "tmp", "temp"):
        return f"Refusing to use database name '{config['name']}'."
    return None


def build_database_url(config):
    """Build a SQLAlchemy URL. The password never leaves this function in logs."""
    return (
        f"mysql+pymysql://{quote_plus(config['user'])}:{quote_plus(config['password'])}"
        f"@{config['host']}:{config['port']}/{config['name']}"
    )


# ---------------------------------------------------------------------------
# startup: create what is missing, never remove anything
# ---------------------------------------------------------------------------

def _create_missing_tables():
    """Create ONLY the tables that do not exist yet.

    This never drops, truncates or empties an existing table, and never
    modifies existing rows. Returns the list of tables it created.
    """
    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    missing = [
        table for name, table in db.metadata.tables.items() if name not in existing
    ]
    if missing:
        db.metadata.create_all(bind=db.engine, tables=missing)
        return [t.name for t in missing]
    return []


def _table_exists(table_name):
    return db.session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
        ),
        {"t": table_name},
    ).scalar() > 0


def _column_exists(table_name, column_name):
    return db.session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table_name, "c": column_name},
    ).scalar() > 0


def _index_exists(table_name, index_name):
    return db.session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
        ),
        {"t": table_name, "i": index_name},
    ).scalar() > 0


def _column_nullable(table_name, column_name):
    value = db.session.execute(
        text(
            "SELECT IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table_name, "c": column_name},
    ).scalar()
    return str(value).upper() == "YES"


def _index_details(table_name):
    """Return {index_name: {'unique': bool, 'columns': [...]}} for a table."""
    rows = db.session.execute(
        text(
            "SELECT INDEX_NAME, NON_UNIQUE, COLUMN_NAME "
            "FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
            "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
        ),
        {"t": table_name},
    ).all()
    indexes = {}
    for name, non_unique, column in rows:
        entry = indexes.setdefault(name, {"unique": not bool(non_unique), "columns": []})
        entry["columns"].append(column)
    return indexes


def _backup_before_schema_upgrade():
    """Write a dump before adding/altering budget columns. Fail closed."""
    scripts_dir = BASE_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import backup as backup_tool

        target, _removed = backup_tool.backup_database()
        return str(target)
    except Exception as exc:  # noqa: BLE001 - never migrate without a backup
        raise DatabaseUnavailable(
            "The automatic backup before the budget schema upgrade failed "
            f"({_mask_secret(exc)}), so nothing was changed."
        ) from exc


def _budget_category_constraints():
    """Return every (constraint_name, delete_rule) for budgets -> categories."""
    rows = db.session.execute(
        text(
            "SELECT CONSTRAINT_NAME, DELETE_RULE "
            "FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'budgets' "
            "AND REFERENCED_TABLE_NAME = 'categories'"
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


def _ensure_piggy_schema():
    """Add any missing piggy-bank columns/indexes to existing tables.

    Brand-new tables are created from the models by _create_missing_tables()
    before this runs, so there is no data to protect. If an older/partial piggy
    table exists, a mysqldump is written BEFORE any ALTER is attempted.
    """
    column_defs = {
        "piggy_banks": {
            "name": "VARCHAR(120) NOT NULL DEFAULT 'Our Piggy Bank'",
            "daily_amount": "DECIMAL(12,2) NOT NULL DEFAULT 50.00",
            "contributor_me": "VARCHAR(80) NOT NULL DEFAULT 'Me'",
            "contributor_her": "VARCHAR(80) NOT NULL DEFAULT 'Her'",
            "currency": "CHAR(3) NOT NULL DEFAULT 'INR'",
            "updated_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "deleted_at": "DATETIME NULL",
        },
        "piggy_entries": {
            "piggy_bank_id": "INT NULL",
            "user_id": "INT NULL",
            "contributor": "VARCHAR(8) NOT NULL DEFAULT 'me'",
            "entry_type": "VARCHAR(16) NOT NULL DEFAULT 'deposit'",
            "amount": "DECIMAL(12,2) NOT NULL DEFAULT 0.00",
            "entry_date": "DATE NULL",
            "note": "VARCHAR(255) NULL",
            "updated_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "deleted_at": "DATETIME NULL",
        },
        "piggy_statements": {
            "period_start": "DATE NULL",
            "period_end": "DATE NULL",
            "opening_balance": "DECIMAL(12,2) NOT NULL DEFAULT 0.00",
            "closing_balance": "DECIMAL(12,2) NOT NULL DEFAULT 0.00",
            "total_deposits": "DECIMAL(12,2) NOT NULL DEFAULT 0.00",
            "total_withdrawals": "DECIMAL(12,2) NOT NULL DEFAULT 0.00",
            "entry_count": "INT NOT NULL DEFAULT 0",
            "file_name": "VARCHAR(255) NOT NULL DEFAULT ''",
            "file_path": "VARCHAR(512) NOT NULL DEFAULT ''",
            "file_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
            "generated_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "deleted_at": "DATETIME NULL",
        },
    }
    index_defs = {
        "piggy_banks": {
            "idx_piggy_banks_user_id": "user_id",
            "idx_piggy_banks_deleted_at": "deleted_at",
        },
        "piggy_entries": {
            "idx_piggy_entries_bank_id": "piggy_bank_id",
            "idx_piggy_entries_entry_date": "entry_date",
            "idx_piggy_entries_bank_date": "piggy_bank_id, entry_date",
            "idx_piggy_entries_contributor_date": "contributor, entry_date",
            "idx_piggy_entries_deleted_at": "deleted_at",
        },
        "piggy_statements": {
            "idx_piggy_statements_bank_period": "piggy_bank_id, period_start, period_end",
            "idx_piggy_statements_hash": "file_sha256",
            "idx_piggy_statements_deleted_at": "deleted_at",
        },
    }

    missing_columns = []
    missing_indexes = []
    for table, columns in column_defs.items():
        if not _table_exists(table):
            continue
        for column in columns:
            if not _column_exists(table, column):
                missing_columns.append((table, column))
        for index_name, columns_sql in index_defs[table].items():
            if not _index_exists(table, index_name):
                missing_indexes.append((table, index_name, columns_sql))

    if not missing_columns and not missing_indexes:
        return []

    if not testing_enabled():
        _backup_before_schema_upgrade()

    applied = []
    for table, column in missing_columns:
        db.session.execute(
            text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {column_defs[table][column]}")
        )
        applied.append(f"{table}.{column}")
    for table, index_name, columns_sql in missing_indexes:
        db.session.execute(
            text(f"ALTER TABLE `{table}` ADD INDEX `{index_name}` ({columns_sql})")
        )
        applied.append(f"{table}.{index_name}")
    return applied


def _ensure_schema():
    """Apply idempotent schema upgrades.

    Adds the soft-delete/archive columns and indexes, upgrades budgets to
    one row per category per month, adds the overall budget and alert tables,
    and makes the budgets foreign key RESTRICT (it used to be CASCADE). It
    never drops a column or table and never deletes existing rows.
    """
    applied = []
    if not _column_exists("transactions", "deleted_at"):
        db.session.execute(
            text("ALTER TABLE transactions ADD COLUMN deleted_at DATETIME NULL")
        )
        applied.append("transactions.deleted_at")
    if not _index_exists("transactions", "idx_transactions_deleted_at"):
        db.session.execute(
            text("ALTER TABLE transactions ADD INDEX idx_transactions_deleted_at (deleted_at)")
        )
        applied.append("transactions.idx_transactions_deleted_at")
    if not _index_exists("transactions", "idx_transactions_user_date_category"):
        db.session.execute(
            text(
                "ALTER TABLE transactions ADD INDEX "
                "idx_transactions_user_date_category (user_id, txn_date, category_id)"
            )
        )
        applied.append("transactions.idx_transactions_user_date_category")
    if not _index_exists("transactions", "idx_transactions_category_date"):
        db.session.execute(
            text(
                "ALTER TABLE transactions ADD INDEX "
                "idx_transactions_category_date (category_id, txn_date)"
            )
        )
        applied.append("transactions.idx_transactions_category_date")

    for column in ("archived_at", "deleted_at"):
        if not _column_exists("categories", column):
            db.session.execute(
                text(f"ALTER TABLE categories ADD COLUMN {column} DATETIME NULL")
            )
            applied.append(f"categories.{column}")
    if not _index_exists("categories", "idx_categories_archived_deleted"):
        db.session.execute(
            text(
                "ALTER TABLE categories ADD INDEX "
                "idx_categories_archived_deleted (archived_at, deleted_at)"
            )
        )
        applied.append("categories.idx_categories_archived_deleted")

    budget_columns = ("month", "rollover", "alert_threshold", "deleted_at")
    needs_budget_upgrade = (
        _column_exists("budgets", "category_id")
        and any(not _column_exists("budgets", column) for column in budget_columns)
    )
    if needs_budget_upgrade and not testing_enabled():
        _backup_before_schema_upgrade()

    if _column_exists("budgets", "category_id"):
        if not _column_exists("budgets", "month"):
            db.session.execute(text("ALTER TABLE budgets ADD COLUMN month DATE NULL"))
            applied.append("budgets.month")
        if not _column_exists("budgets", "rollover"):
            db.session.execute(
                text(
                    "ALTER TABLE budgets ADD COLUMN rollover TINYINT(1) "
                    "NOT NULL DEFAULT 0"
                )
            )
            applied.append("budgets.rollover")
        if not _column_exists("budgets", "alert_threshold"):
            db.session.execute(
                text(
                    "ALTER TABLE budgets ADD COLUMN alert_threshold "
                    "TINYINT UNSIGNED NOT NULL DEFAULT 80"
                )
            )
            applied.append("budgets.alert_threshold")
        if not _column_exists("budgets", "deleted_at"):
            db.session.execute(
                text("ALTER TABLE budgets ADD COLUMN deleted_at DATETIME NULL")
            )
            applied.append("budgets.deleted_at")

        first_of_month = date.today().replace(day=1)
        db.session.execute(
            text("UPDATE budgets SET month = :month WHERE month IS NULL"),
            {"month": first_of_month},
        )
        if _column_nullable("budgets", "month"):
            db.session.execute(
                text("ALTER TABLE budgets MODIFY COLUMN month DATE NOT NULL")
            )
            applied.append("budgets.month NOT NULL")

        budget_indexes = _index_details("budgets")
        has_month_unique = any(
            details["unique"] and details["columns"] == ["category_id", "month"]
            for details in budget_indexes.values()
        )
        if not has_month_unique:
            db.session.execute(
                text(
                    "ALTER TABLE budgets ADD CONSTRAINT uq_budgets_category_month "
                    "UNIQUE (category_id, month)"
                )
            )
            applied.append("budgets.uq_budgets_category_month")
        # The old single-column unique index also backs the foreign key. Add the
        # composite unique above first so MySQL can drop the old index safely.
        budget_indexes = _index_details("budgets")
        for name, details in budget_indexes.items():
            if details["unique"] and details["columns"] == ["category_id"]:
                db.session.execute(text(f"ALTER TABLE budgets DROP INDEX `{name}`"))
                applied.append(f"budgets.drop_index({name})")
        if not _index_exists("budgets", "idx_budgets_month_deleted"):
            db.session.execute(
                text(
                    "ALTER TABLE budgets ADD INDEX "
                    "idx_budgets_month_deleted (month, deleted_at)"
                )
            )
            applied.append("budgets.idx_budgets_month_deleted")

    if _column_exists("category_deletions", "category_id") and not _column_exists(
        "category_deletions", "budget_changes"
    ):
        db.session.execute(
            text("ALTER TABLE category_deletions ADD COLUMN budget_changes TEXT NULL")
        )
        applied.append("category_deletions.budget_changes")

    # MySQL names an unnamed FK differently on every install (budgets_ibfk_2,
    # fk_budgets_category, ...), so match the constraint by the columns it
    # protects instead of guessing its name.
    fk_constraints = _budget_category_constraints()
    fk_is_restrict = (
        len(fk_constraints) == 1
        and fk_constraints[0][0] == "fk_budgets_category"
        and str(fk_constraints[0][1]).upper() == "RESTRICT"
    )
    if not fk_is_restrict and (
        fk_constraints or _column_exists("budgets", "category_id")
    ):
        for constraint_name, _ in fk_constraints:
            db.session.execute(
                text(f"ALTER TABLE budgets DROP FOREIGN KEY `{constraint_name}`")
            )
        db.session.execute(
            text(
                "ALTER TABLE budgets ADD CONSTRAINT fk_budgets_category "
                "FOREIGN KEY (category_id) REFERENCES categories (id) "
                "ON DELETE RESTRICT"
            )
        )
        applied.append("budgets.fk_budgets_category (RESTRICT)")

    applied.extend(_ensure_piggy_schema())

    if applied:
        db.session.commit()
    return applied


def _seed_categories():
    """Seed the default categories ONLY when the table is completely empty."""
    if db.session.execute(select(func.count(Category.id))).scalar() != 0:
        return 0
    db.session.add_all(
        Category(user_id=None, name=name, type=kind) for name, kind in DEFAULT_CATEGORIES
    )
    _commit()
    return len(DEFAULT_CATEGORIES)


def table_row_counts():
    """Row counts of the three tables that matter, for the startup log."""
    return {
        "transactions": db.session.execute(
            select(func.count(Transaction.id))
            .where(Transaction.deleted_at.is_(None))
        ).scalar(),
        "transactions_total": db.session.execute(
            select(func.count(Transaction.id))
        ).scalar(),
        "categories": db.session.execute(select(func.count(Category.id))).scalar(),
        "budgets": db.session.execute(select(func.count(Budget.id))).scalar(),
    }


def init_db(app):
    """Validate env, connect (5s timeout), create missing tables, seed if empty.

    Never deletes, drops, truncates or resets any data.
    """
    config, error = _require_env()
    if error:
        raise DatabaseUnavailable(error)

    guard_error = _guard_database(config)
    if guard_error:
        raise DatabaseUnavailable(guard_error)

    app.config["SQLALCHEMY_DATABASE_URI"] = build_database_url(config)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }
    db.init_app(app)

    try:
        with app.app_context():
            # fails loudly if MySQL is unreachable (5s timeout, password masked)
            with db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            created = _create_missing_tables()
            upgraded = _ensure_schema()
            seeded = _seed_categories()
    except OperationalError as exc:
        raise DatabaseUnavailable(
            "Could not connect to MySQL at "
            f"{config['host']}:{config['port']} as user '{config['user']}' "
            f"database '{config['name']}'. "
            f"Underlying error: {_mask_secret(exc.orig, config['password'])}"
        ) from exc
    except SQLAlchemyError as exc:
        raise DatabaseUnavailable(
            f"MySQL error: {_mask_secret(exc, config['password'])}"
        ) from exc

    _ACTIVE_CONFIG.clear()
    _ACTIVE_CONFIG.update(config)

    print(f"MySQL connected to '{config['name']}' at {config['host']}:{config['port']}", flush=True)
    if created:
        print(f"  created missing tables : {', '.join(created)}", flush=True)
    if upgraded:
        print(f"  applied schema upgrades : {', '.join(upgraded)}", flush=True)
    if seeded:
        print(f"  seeded default categories: {seeded}", flush=True)
    return db


def startup_report():
    """The database name and the row counts, for logging at startup."""
    with db.engine.connect() as conn:
        name = conn.execute(text("SELECT DATABASE()")).scalar()
    counts = table_row_counts()
    lines = [
        f"  database                : {name}",
        f"  transactions (visible)  : {counts['transactions']}",
        f"  transactions (total)    : {counts['transactions_total']}",
        f"  categories              : {counts['categories']}",
        f"  budgets                 : {counts['budgets']}",
    ]
    return name, counts, "\n".join(lines)


def ping():
    """Verify the database is reachable right now. Returns (ok, db_name_or_error)."""
    config, error = _require_env()
    if error:
        return False, error
    try:
        db.session.execute(text("SELECT 1"))
        db.session.rollback()
        return True, config["name"]
    except Exception as exc:  # noqa: BLE001 - health check must not raise
        db.session.rollback()
        return False, _mask_secret(exc, config["password"])


# ---------------------------------------------------------------------------
# serialization (same dict shapes the templates already expect)
# ---------------------------------------------------------------------------

def serialize_category(row):
    return {
        "id": str(row.id),
        "name": row.name,
        "type": row.type,
        "archived_at": row.archived_at.isoformat() if row.archived_at else "",
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else "",
        "is_protected": _is_protected_category(row),
    }


def serialize_transaction(row):
    return {
        "id": str(row.id),
        "category_id": str(row.category_id),
        "amount": row.amount,
        "description": row.description or "",
        "txn_date": row.txn_date.isoformat(),
        "category": row.category.name,
        "type": row.category.type,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else "",
    }


def _as_date(value):
    """Accept 'YYYY-MM-DD' strings or date objects; raise ValueError when invalid."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def month_start(value=None):
    """Return the first day of a month from 'YYYY-MM', a date, or today."""
    if value is None or value == "":
        parsed = date.today()
    elif isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        raw = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}", raw):
            raw = f"{raw}-01"
        parsed = date.fromisoformat(raw)
    return parsed.replace(day=1)


def next_month_start(month):
    start = month_start(month)
    if start.month == 12:
        return date(start.year + 1, 1, 1)
    return date(start.year, start.month + 1, 1)


def previous_month_start(month):
    start = month_start(month)
    if start.month == 1:
        return date(start.year - 1, 12, 1)
    return date(start.year, start.month - 1, 1)


def month_key(month):
    return month_start(month).strftime("%Y-%m")


def _commit():
    """Commit the current unit of work as one DB transaction; roll back on error.

    Flask-SQLAlchemy 3.x sessions autobegin, so an explicit session.begin()
    would fail once anything has touched the session. All mutations in one
    function therefore run inside a single implicit transaction that this
    commit either applies completely or not at all.

    On failure the transaction is rolled back and SaveError is raised so the
    caller can tell the user that nothing was saved - never a silent failure.
    """
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise SaveError(
            f"the database rejected the change ({_mask_secret(exc)})"
        ) from exc


# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------

def _active_categories_stmt():
    return select(Category).where(
        Category.archived_at.is_(None), Category.deleted_at.is_(None)
    )


def list_categories(include_archived=False, include_deleted=False):
    """Active categories only, unless explicitly asked for the hidden ones."""
    stmt = select(Category)
    if not include_archived:
        stmt = stmt.where(Category.archived_at.is_(None))
    if not include_deleted:
        stmt = stmt.where(Category.deleted_at.is_(None))
    rows = db.session.execute(
        stmt.order_by(Category.type, Category.name)
    ).scalars().all()
    return [serialize_category(r) for r in rows]


def list_archived_categories():
    rows = db.session.execute(
        select(Category)
        .where(Category.archived_at.isnot(None), Category.deleted_at.is_(None))
        .order_by(Category.archived_at.desc(), Category.name)
    ).scalars().all()
    return [serialize_category(r) for r in rows]


def list_deleted_categories(limit=50):
    rows = db.session.execute(
        select(Category)
        .where(Category.deleted_at.isnot(None))
        .order_by(Category.deleted_at.desc(), Category.id.desc())
        .limit(limit)
    ).scalars().all()
    return [serialize_category(r) for r in rows]


def list_categories_by_type(kind):
    rows = db.session.execute(
        _active_categories_stmt()
        .where(Category.type == kind)
        .order_by(Category.name)
    ).scalars().all()
    return [serialize_category(r) for r in rows]


def get_category(category_id):
    """Return an active (not archived, not deleted) category, or None."""
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        return None
    row = db.session.get(Category, cid)
    if row is None or row.archived_at is not None or row.deleted_at is not None:
        return None
    return serialize_category(row)


def get_category_any(category_id):
    """Return a category regardless of its archived/deleted state."""
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        return None
    row = db.session.get(Category, cid)
    return serialize_category(row) if row else None


def add_category(name, kind):
    # MySQL treats NULLs as distinct in unique keys, so the unique index on
    # (user_id, name) does not cover the Flask app's NULL-user rows. Check
    # explicitly for duplicates, then rely on the unique key as a backstop.
    existing = db.session.execute(
        select(Category).where(Category.user_id.is_(None), Category.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateKeyError(f"Category '{name}' already exists.")
    db.session.add(Category(user_id=None, name=name, type=kind))
    try:
        _commit()
    except SaveError:
        raise DuplicateKeyError(f"Category '{name}' already exists.")


def validate_category_name(name):
    """Return (clean_name, error). Trims and enforces the shared length limit."""
    clean = "" if name is None else str(name).strip()
    if not clean:
        return "", "Enter a category name."
    if len(clean) > CATEGORY_NAME_MAX:
        return clean, f"Category name must be {CATEGORY_NAME_MAX} characters or fewer."
    return clean, None


def rename_category(category_id, new_name):
    """Rename one category in place.

    Only categories.name is written. The id, type, colour/icon (if a database
    adds them), budgets and every transaction stay exactly as they are -
    transactions are linked by category_id, so they pick up the new name
    automatically. The row is locked and committed as a single transaction;
    any failure rolls everything back through _commit().
    """
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        raise CategoryNotFoundError("That category does not exist.")

    clean, error = validate_category_name(new_name)
    if error:
        raise ValueError(error)

    row = db.session.execute(
        select(Category).where(Category.id == cid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        raise CategoryNotFoundError("That category does not exist.")

    # An unchanged name is a no-op: close the dialog without any write.
    if row.name == clean:
        return {"changed": False, "category": serialize_category(row)}

    if _is_protected_category(row):
        raise CategoryActionError(
            f"'{row.name}' is a fallback category and is locked so every "
            f"transaction always has somewhere to go."
        )

    # The app's unique key is (user_id, name) and its add form rejects the
    # same name in either type, so rename keeps the same rule. MySQL's
    # collation is case-insensitive; LOWER() makes that explicit for every
    # install. Archived/deleted rows are included to match add_category().
    duplicate = db.session.execute(
        select(Category.id)
        .where(
            Category.id != cid,
            Category.user_id.is_(None),
            func.lower(Category.name) == clean.lower(),
        )
        .limit(1)
    ).scalar_one_or_none()
    if duplicate is not None:
        raise DuplicateKeyError(f"A category named '{clean}' already exists.")

    row.name = clean
    _commit()
    return {"changed": True, "category": serialize_category(row)}


def category_usage(category_id):
    """Usage counts and safe actions for the delete dialog."""
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        return None
    row = db.session.get(Category, cid)
    if row is None or row.deleted_at is not None:
        return None

    transaction_count = db.session.execute(
        select(func.count(Transaction.id)).where(Transaction.category_id == cid)
    ).scalar()
    budget_count = db.session.execute(
        select(func.count(Budget.id)).where(Budget.category_id == cid)
    ).scalar()
    active_same_type = db.session.execute(
        select(func.count(Category.id)).where(
            Category.type == row.type,
            Category.archived_at.is_(None),
            Category.deleted_at.is_(None),
        )
    ).scalar()

    candidates = []
    if active_same_type > 1:
        rows = db.session.execute(
            _active_categories_stmt()
            .where(Category.type == row.type, Category.id != cid)
            .order_by(Category.name)
        ).scalars().all()
        for candidate in rows:
            budget = db.session.execute(
                select(Budget).where(
                    Budget.category_id == candidate.id,
                    Budget.month == month_start(),
                    Budget.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            candidates.append(
                {
                    "id": str(candidate.id),
                    "name": candidate.name,
                    "type": candidate.type,
                    "has_budget": budget is not None,
                    "budget_limit": str(budget.monthly_limit) if budget else None,
                }
            )

    protected = _is_protected_category(row)
    source_budget = db.session.execute(
        select(Budget).where(
            Budget.category_id == cid,
            Budget.month == month_start(),
            Budget.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    return {
        "category": serialize_category(row),
        "transaction_count": int(transaction_count or 0),
        "budget_count": int(budget_count or 0),
        "budget_limit": str(source_budget.monthly_limit) if source_budget else None,
        "active_same_type": int(active_same_type or 0),
        "is_protected": protected,
        "is_archived": row.archived_at is not None,
        "can_delete": not protected and active_same_type > 1,
        "can_archive": not protected and active_same_type > 1,
        "candidates": candidates,
    }


def _backup_before_reassign():
    """Write a mysqldump before moving data. Fail closed if it cannot."""
    scripts_dir = BASE_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import backup as backup_tool

        target, _removed = backup_tool.backup_database()
        return str(target)
    except Exception as exc:  # noqa: BLE001 - never reassign without a backup
        raise SaveError(
            "the automatic backup before reassigning failed "
            f"({_mask_secret(exc)}), so nothing was changed"
        ) from exc


def delete_category(category_id, action, target_category_id=None):
    """Soft-delete, archive, or reassign-and-soft-delete a category.

    Reassigning transactions and budgets, and marking the category deleted,
    all happen in one transaction. If anything fails, _commit rolls the whole
    unit back, so a half-moved category is impossible.
    """
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        raise CategoryActionError("That category does not exist.")

    action = str(action or "").strip().lower()
    if action not in ("delete", "archive", "reassign"):
        raise CategoryActionError("Choose what should happen to this category.")

    row = db.session.execute(
        select(Category).where(Category.id == cid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        raise CategoryActionError("That category does not exist.")

    transaction_count = db.session.execute(
        select(func.count(Transaction.id)).where(Transaction.category_id == cid)
    ).scalar()
    budget_count = db.session.execute(
        select(func.count(Budget.id)).where(Budget.category_id == cid)
    ).scalar()
    active_same_type = db.session.execute(
        select(func.count(Category.id)).where(
            Category.type == row.type,
            Category.archived_at.is_(None),
            Category.deleted_at.is_(None),
        )
    ).scalar()

    if _is_protected_category(row):
        raise CategoryActionError(
            f"'{row.name}' is a fallback category for {row.type} and cannot be deleted."
        )
    if active_same_type <= 1:
        raise CategoryActionError(
            f"'{row.name}' is the last {row.type} category. Add another "
            f"{row.type} category before deleting or archiving it."
        )

    now = _utcnow()
    if action == "archive":
        row.archived_at = now
        _commit()
        return {"action": "archive", "category": serialize_category(row)}

    if action == "delete":
        if transaction_count or budget_count:
            raise CategoryActionError(
                f"'{row.name}' is still used by {transaction_count} transaction(s) "
                f"and {budget_count} budget(s). Reassign or archive it instead."
            )
        row.deleted_at = now
        db.session.add(CategoryDeletion(category_id=cid, action="delete"))
        _commit()
        return {"action": "delete", "category": serialize_category(row)}

    # --- reassign ---
    if transaction_count == 0 and budget_count == 0:
        raise CategoryActionError(
            "This category is not in use, so it can be deleted directly."
        )

    try:
        target_id = int(target_category_id)
    except (TypeError, ValueError):
        raise CategoryActionError("Choose a category to move everything to.")

    target = db.session.execute(
        select(Category).where(Category.id == target_id).with_for_update()
    ).scalar_one_or_none()
    if target is None or target.deleted_at is not None:
        raise CategoryActionError("The target category does not exist.")
    if target.archived_at is not None:
        raise CategoryActionError("The target category is archived. Choose an active one.")
    if target.id == row.id:
        raise CategoryActionError("Choose a different category to reassign to.")
    if target.type != row.type:
        raise CategoryActionError(
            f"'{row.name}' is {row.type}, but '{target.name}' is {target.type}. "
            f"Reassign to a {row.type} category instead."
        )

    # The dump is written before any row is changed. If it fails we stop here.
    _backup_before_reassign()

    transactions = db.session.execute(
        select(Transaction)
        .where(Transaction.category_id == cid)
        .with_for_update()
    ).scalars().all()
    transaction_ids = [txn.id for txn in transactions]
    for txn in transactions:
        txn.category_id = target.id

    source_budgets = db.session.execute(
        select(Budget)
        .where(Budget.category_id == cid, Budget.deleted_at.is_(None))
        .with_for_update()
    ).scalars().all()
    target_budgets = {
        row.month: row
        for row in db.session.execute(
            select(Budget)
            .where(Budget.category_id == target.id, Budget.deleted_at.is_(None))
            .with_for_update()
        ).scalars().all()
    }

    budget_changes = []
    budget_id = None
    budget_moved = False
    budget_limit = None
    target_budget_id = None
    target_budget_before = None
    for source_budget in source_budgets:
        if budget_id is None:
            budget_id = source_budget.id
            budget_limit = source_budget.monthly_limit
        target_budget = target_budgets.get(source_budget.month)
        if target_budget is not None:
            # Both have a budget for this month: merge the limits into the target.
            before = target_budget.monthly_limit
            budget_changes.append(
                {
                    "action": "merge",
                    "source_id": source_budget.id,
                    "target_id": target_budget.id,
                    "target_before": str(before),
                    "source_limit": str(source_budget.monthly_limit),
                    "target_before_rollover": bool(target_budget.rollover),
                    "target_before_threshold": int(target_budget.alert_threshold),
                }
            )
            if target_budget_id is None:
                target_budget_id = target_budget.id
                target_budget_before = before
            target_budget.monthly_limit = before + source_budget.monthly_limit
            target_budget.rollover = bool(
                target_budget.rollover or source_budget.rollover
            )
            target_budget.alert_threshold = min(
                int(target_budget.alert_threshold),
                int(source_budget.alert_threshold),
            )
            source_budget.deleted_at = now
        else:
            budget_changes.append(
                {
                    "action": "move",
                    "source_id": source_budget.id,
                    "target_id": target.id,
                }
            )
            source_budget.category_id = target.id
            target_budgets[source_budget.month] = source_budget
            budget_moved = True

    row.deleted_at = now
    db.session.add(
        CategoryDeletion(
            category_id=cid,
            target_category_id=target.id,
            action="reassign",
            transaction_ids=json.dumps(transaction_ids),
            budget_id=budget_id,
            budget_moved=budget_moved,
            budget_limit=budget_limit,
            target_budget_id=target_budget_id,
            target_budget_before=target_budget_before,
            budget_changes=json.dumps(budget_changes) if budget_changes else None,
        )
    )
    _commit()
    return {
        "action": "reassign",
        "category": serialize_category(row),
        "target": serialize_category(target),
        "moved_transactions": len(transaction_ids),
    }


def restore_archived_category(category_id):
    """Un-archive a category. Returns True when something was restored."""
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        return False
    row = db.session.execute(
        select(Category).where(Category.id == cid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None or row.archived_at is None:
        return False
    row.archived_at = None
    _commit()
    return True


def undo_delete_category(category_id):
    """Undo a soft category delete, including the moves made by a reassign."""
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        return False
    row = db.session.execute(
        select(Category).where(Category.id == cid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is None:
        return False

    log = db.session.execute(
        select(CategoryDeletion)
        .where(
            CategoryDeletion.category_id == cid,
            CategoryDeletion.undone_at.is_(None),
        )
        .order_by(CategoryDeletion.id.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()

    if log is not None and log.action == "reassign" and log.target_category_id:
        target_id = log.target_category_id
        try:
            transaction_ids = json.loads(log.transaction_ids or "[]")
        except (TypeError, ValueError):
            transaction_ids = []
        if transaction_ids:
            moved = db.session.execute(
                select(Transaction)
                .where(
                    Transaction.id.in_(transaction_ids),
                    Transaction.category_id == target_id,
                )
                .with_for_update()
            ).scalars().all()
            for txn in moved:
                txn.category_id = cid

        try:
            budget_changes = json.loads(log.budget_changes or "[]")
        except (TypeError, ValueError):
            budget_changes = []

        if budget_changes:
            for change in budget_changes:
                try:
                    source = db.session.get(Budget, int(change.get("source_id")))
                except (TypeError, ValueError):
                    source = None
                if source is None:
                    continue
                existing = db.session.execute(
                    select(Budget).where(
                        Budget.category_id == cid,
                        Budget.month == source.month,
                        Budget.deleted_at.is_(None),
                        Budget.id != source.id,
                    )
                ).scalar_one_or_none()
                if change.get("action") == "merge":
                    try:
                        target = db.session.get(Budget, int(change.get("target_id")))
                    except (TypeError, ValueError):
                        target = None
                    if target is not None and target.deleted_at is None:
                        expected = Decimal(str(change.get("target_before", "0"))) + Decimal(
                            str(change.get("source_limit", "0"))
                        )
                        if target.monthly_limit == expected:
                            target.monthly_limit = Decimal(
                                str(change.get("target_before", "0"))
                            )
                            target.rollover = bool(
                                change.get("target_before_rollover", target.rollover)
                            )
                            target.alert_threshold = int(
                                change.get("target_before_threshold", target.alert_threshold)
                            )
                    if existing is None:
                        source.deleted_at = None
                elif change.get("action") == "move":
                    try:
                        moved_to = int(change.get("target_id"))
                    except (TypeError, ValueError):
                        moved_to = None
                    if source.category_id == moved_to and existing is None:
                        source.category_id = cid
                        source.deleted_at = None
        elif log.budget_moved and log.budget_id:
            budget = db.session.get(Budget, log.budget_id)
            existing = db.session.execute(
                select(Budget).where(
                    Budget.category_id == cid,
                    Budget.month == month_start(),
                    Budget.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if budget is not None and budget.category_id == target_id and existing is None:
                budget.category_id = cid
                budget.deleted_at = None
        elif log.budget_limit is not None:
            if log.target_budget_id and log.target_budget_before is not None:
                target_budget = db.session.get(Budget, log.target_budget_id)
                if target_budget is not None:
                    merged_limit = log.target_budget_before + log.budget_limit
                    if target_budget.monthly_limit == merged_limit:
                        target_budget.monthly_limit = log.target_budget_before
            existing = db.session.execute(
                select(Budget).where(
                    Budget.category_id == cid,
                    Budget.month == month_start(),
                    Budget.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if existing is None:
                db.session.add(
                    Budget(
                        user_id=None,
                        category_id=cid,
                        month=month_start(),
                        monthly_limit=log.budget_limit,
                    )
                )

    if log is not None:
        log.undone_at = _utcnow()
    row.deleted_at = None
    _commit()
    return True


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------

def _live_transactions():
    """Base select: transactions that have not been soft deleted."""
    return select(Transaction).where(Transaction.deleted_at.is_(None))


def list_transactions():
    rows = db.session.execute(
        _live_transactions()
        .join(Category)
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
    ).scalars().all()
    return [serialize_transaction(r) for r in rows]


def recent_transactions(limit=8):
    rows = db.session.execute(
        _live_transactions()
        .join(Category)
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .limit(limit)
    ).scalars().all()
    return [serialize_transaction(r) for r in rows]


def count_transactions():
    return db.session.execute(
        select(func.count(Transaction.id)).where(Transaction.deleted_at.is_(None))
    ).scalar()


def get_transaction(txn_id):
    try:
        tid = int(txn_id)
    except (TypeError, ValueError):
        return None
    row = db.session.get(Transaction, tid)
    if row is None or row.category_id is None or row.deleted_at is not None:
        return None
    return {
        "id": str(row.id),
        "category_id": str(row.category_id),
        "txn_date": row.txn_date.isoformat(),
        "amount": row.amount,
    }


def add_transaction(category_id, amount, description, txn_date):
    db.session.add(
        Transaction(
            user_id=None,
            category_id=int(category_id),
            amount=Decimal(str(amount)),
            description=description or "",
            txn_date=_as_date(txn_date),
        )
    )
    _commit()


def update_transaction(txn_id, category_id, amount, description, txn_date):
    row = db.session.get(Transaction, int(txn_id))
    if row is None or row.category_id is None or row.deleted_at is not None:
        return
    row.category_id = int(category_id)
    row.amount = Decimal(str(amount))
    row.description = description or ""
    row.txn_date = _as_date(txn_date)
    _commit()


def delete_transaction(txn_id):
    """SOFT delete: the row stays, only deleted_at is set. Can be undone."""
    try:
        tid = int(txn_id)
    except (TypeError, ValueError):
        return False
    row = db.session.get(Transaction, tid)
    if row is not None and row.category_id is not None and row.deleted_at is None:
        row.deleted_at = _utcnow()
        _commit()
        return True
    return False


def undo_transaction(txn_id):
    """Bring a soft deleted transaction back. Returns True when restored."""
    try:
        tid = int(txn_id)
    except (TypeError, ValueError):
        return False
    row = db.session.get(Transaction, tid)
    if row is not None and row.deleted_at is not None:
        row.deleted_at = None
        _commit()
        return True
    return False


def list_deleted_transactions(limit=50):
    """Recently deleted transactions, newest deletion first, for the Undo list."""
    rows = db.session.execute(
        select(Transaction)
        .join(Category)
        .where(Transaction.deleted_at.isnot(None))
        .order_by(Transaction.deleted_at.desc(), Transaction.id.desc())
        .limit(limit)
    ).scalars().all()
    return [serialize_transaction(r) for r in rows]


# ---------------------------------------------------------------------------
# aggregations used by the dashboard
# ---------------------------------------------------------------------------

def sum_by_type(kind, date_from=None, date_to=None):
    stmt = (
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .join(Category)
        .where(
            Category.type == kind,
            Transaction.category_id.isnot(None),
            Transaction.deleted_at.is_(None),
        )
    )
    if date_from is not None:
        stmt = stmt.where(Transaction.txn_date >= _as_date(date_from))
    if date_to is not None:
        stmt = stmt.where(Transaction.txn_date < _as_date(date_to))
    return db.session.execute(stmt).scalar()


def sum_for_category(category_id, date_from=None, date_to=None):
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        return Decimal("0")
    stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
        Transaction.category_id == cid,
        Transaction.deleted_at.is_(None),
    )
    if date_from is not None:
        stmt = stmt.where(Transaction.txn_date >= _as_date(date_from))
    if date_to is not None:
        stmt = stmt.where(Transaction.txn_date < _as_date(date_to))
    return db.session.execute(stmt).scalar()


def spent_for_category_in_month(category_id, month):
    """Expense spending for one category inside one calendar month."""
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        return Decimal("0")
    start = month_start(month)
    end = next_month_start(start)
    value = db.session.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.category_id == cid,
            Category.type == "expense",
            Transaction.deleted_at.is_(None),
            Transaction.txn_date >= start,
            Transaction.txn_date < end,
        )
    ).scalar()
    return value or Decimal("0")


def spending_by_category(date_from, date_to=None):
    """[{name, total}] for expense categories, biggest spender first."""
    stmt = (
        select(Category.name, func.sum(Transaction.amount).label("total"))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Category.type == "expense",
            Transaction.txn_date >= _as_date(date_from),
            Transaction.deleted_at.is_(None),
        )
        .group_by(Category.name)
        .order_by(func.sum(Transaction.amount).desc())
    )
    if date_to is not None:
        stmt = stmt.where(Transaction.txn_date < _as_date(date_to))
    rows = db.session.execute(stmt).all()
    return [{"name": r.name, "total": float(r.total)} for r in rows]


def spending_by_category_month(month, active_only=True):
    """[{id, name, total}] expense spending for a month (optionally active only)."""
    start = month_start(month)
    end = next_month_start(start)
    stmt = (
        select(
            Category.id,
            Category.name,
            func.sum(Transaction.amount).label("total"),
        )
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Category.type == "expense",
            Transaction.txn_date >= start,
            Transaction.txn_date < end,
            Transaction.deleted_at.is_(None),
        )
        .group_by(Category.id, Category.name)
        .order_by(func.sum(Transaction.amount).desc())
    )
    if active_only:
        stmt = stmt.where(
            Category.archived_at.is_(None), Category.deleted_at.is_(None)
        )
    rows = db.session.execute(stmt).all()
    return [
        {"id": str(r.id), "name": r.name, "total": r.total}
        for r in rows
    ]


def monthly_series(date_from):
    """[{ym: 'YYYY-MM', type, total}] for dashboard trend charts."""
    ym = func.date_format(Transaction.txn_date, "%Y-%m").label("ym")
    rows = db.session.execute(
        select(ym, Category.type, func.sum(Transaction.amount).label("total"))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.txn_date >= _as_date(date_from),
            Transaction.deleted_at.is_(None),
        )
        .group_by(ym, Category.type)
    ).all()
    return [
        {"ym": r.ym, "type": r.type, "total": float(r.total)}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# budgets
# ---------------------------------------------------------------------------

def _q2(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _clean_budget_amount(value):
    try:
        amount = _q2(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Enter a budget amount.") from exc
    if amount < MIN_BUDGET_AMOUNT:
        raise ValueError("Budget amount must be greater than 0.")
    if amount > MAX_BUDGET_AMOUNT:
        raise ValueError(
            f"Budget amount must be {MAX_BUDGET_AMOUNT:,.2f} or less."
        )
    return amount


def _clean_alert_threshold(value):
    if value is None or value == "":
        return DEFAULT_ALERT_THRESHOLD
    try:
        threshold = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Alert threshold must be a whole number.") from exc
    if threshold < 1 or threshold > 100:
        raise ValueError("Alert threshold must be between 1 and 100.")
    return threshold


def _budget_dict(budget, category):
    return {
        "id": str(budget.id),
        "name": category.name,
        "category_id": str(budget.category_id),
        "month": budget.month.isoformat(),
        "month_key": month_key(budget.month),
        "monthly_limit": budget.monthly_limit,
        "rollover": bool(budget.rollover),
        "alert_threshold": int(budget.alert_threshold or DEFAULT_ALERT_THRESHOLD),
        "deleted_at": budget.deleted_at.isoformat() if budget.deleted_at else "",
    }


def list_budgets(month=None, include_deleted=False):
    """Active category budgets for one month (current month by default)."""
    start = month_start(month)
    stmt = (
        select(Budget, Category)
        .join(Category, Budget.category_id == Category.id)
        .where(
            Budget.month == start,
            Category.archived_at.is_(None),
            Category.deleted_at.is_(None),
        )
        .order_by(Category.name)
    )
    if not include_deleted:
        stmt = stmt.where(Budget.deleted_at.is_(None))
    rows = db.session.execute(stmt).all()
    return [_budget_dict(budget, category) for budget, category in rows]


def get_budget(budget_id, include_deleted=False):
    try:
        bid = int(budget_id)
    except (TypeError, ValueError):
        return None
    row = db.session.execute(
        select(Budget, Category)
        .join(Category, Budget.category_id == Category.id)
        .where(Budget.id == bid)
    ).first()
    if row is None:
        return None
    budget, category = row
    if not include_deleted and budget.deleted_at is not None:
        return None
    return _budget_dict(budget, category)


def budget_for_category_month(category_id, month=None, include_deleted=False):
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        return None
    stmt = select(Budget).where(
        Budget.category_id == cid, Budget.month == month_start(month)
    )
    if not include_deleted:
        stmt = stmt.where(Budget.deleted_at.is_(None))
    row = db.session.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    category = db.session.get(Category, cid)
    return _budget_dict(row, category) if category else None


def _rollover_carry(category_id, month, limit, rollover, depth=0):
    """Unused money carried into `month` from the previous month(s)."""
    if not rollover or depth >= 24:
        return Decimal("0.00")
    previous = previous_month_start(month)
    row = db.session.execute(
        select(Budget).where(
            Budget.category_id == int(category_id),
            Budget.month == previous,
            Budget.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        return Decimal("0.00")
    earlier = _rollover_carry(
        category_id, previous, row.monthly_limit, bool(row.rollover), depth + 1
    )
    previous_effective = row.monthly_limit + earlier
    previous_spent = spent_for_category_in_month(category_id, previous)
    return max(previous_effective - previous_spent, Decimal("0.00"))


def _month_pace(month):
    start = month_start(month)
    end = next_month_start(start)
    days_in_month = (end - start).days
    today = date.today().replace(day=1)
    if start == today:
        days_elapsed = date.today().day
        days_left = days_in_month - date.today().day + 1
    elif start < today:
        days_elapsed = days_in_month
        days_left = 0
    else:
        days_elapsed = 0
        days_left = days_in_month
    return days_in_month, days_elapsed, days_left


def list_budgets_with_spent(month=None):
    start = month_start(month)
    budgets = list_budgets(start)
    days_in_month, days_elapsed, days_left = _month_pace(start)
    for budget in budgets:
        spent = spent_for_category_in_month(budget["category_id"], start)
        carry = _rollover_carry(
            budget["category_id"],
            start,
            budget["monthly_limit"],
            budget["rollover"],
        )
        effective = budget["monthly_limit"] + carry
        percent = float((spent / effective) * 100) if effective > 0 else 0.0
        remaining = effective - spent
        threshold = int(budget.get("alert_threshold") or DEFAULT_ALERT_THRESHOLD)
        if start == date.today().replace(day=1):
            projected = (
                (spent / days_elapsed) * days_in_month
                if days_elapsed
                else Decimal("0.00")
            )
        elif start < date.today().replace(day=1):
            projected = spent
        else:
            projected = Decimal("0.00")
        projected = _q2(projected)
        budget.update(
            {
                "spent": spent,
                "rollover_amount": carry,
                "effective_limit": effective,
                "remaining": remaining,
                "over_amount": max(-remaining, Decimal("0.00")),
                "percent": round(percent, 2),
                "status": "over" if percent >= 100 else ("warning" if percent >= threshold else "normal"),
                "projected": projected,
                "forecast_over": projected > effective and spent <= effective,
                "days_in_month": days_in_month,
                "days_elapsed": days_elapsed,
                "days_left": days_left,
            }
        )
    return budgets


def upsert_budget(
    category_id,
    monthly_limit,
    month=None,
    rollover=False,
    alert_threshold=DEFAULT_ALERT_THRESHOLD,
):
    cid = int(category_id)
    start = month_start(month)
    limit = _clean_budget_amount(monthly_limit)
    threshold = _clean_alert_threshold(alert_threshold)
    row = db.session.execute(
        select(Budget)
        .where(Budget.category_id == cid, Budget.month == start)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        row = Budget(
            user_id=None,
            category_id=cid,
            month=start,
            monthly_limit=limit,
            rollover=bool(rollover),
            alert_threshold=threshold,
        )
        db.session.add(row)
    else:
        row.monthly_limit = limit
        row.rollover = bool(rollover)
        row.alert_threshold = threshold
        row.deleted_at = None
    _commit()
    return str(row.id)


def update_budget(
    budget_id,
    monthly_limit=None,
    rollover=None,
    alert_threshold=None,
):
    try:
        bid = int(budget_id)
    except (TypeError, ValueError):
        return None
    row = db.session.execute(
        select(Budget).where(Budget.id == bid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        return None
    if monthly_limit is not None:
        row.monthly_limit = _clean_budget_amount(monthly_limit)
    if rollover is not None:
        row.rollover = bool(rollover)
    if alert_threshold is not None:
        row.alert_threshold = _clean_alert_threshold(alert_threshold)
    _commit()
    return get_budget(bid)


def _dismiss_alerts_for_budget(budget_key, month):
    """Hide alerts that no longer describe an existing budget."""
    rows = (
        db.session.execute(
            select(BudgetAlert).where(
                BudgetAlert.budget_key == budget_key,
                BudgetAlert.month == month_start(month),
                BudgetAlert.dismissed_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    now = _utcnow()
    for row in rows:
        row.dismissed_at = now
    return len(rows)


def delete_budget(budget_id):
    """Soft delete a budget. Transactions are never touched."""
    try:
        bid = int(budget_id)
    except (TypeError, ValueError):
        return False
    row = db.session.execute(
        select(Budget).where(Budget.id == bid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        return False
    row.deleted_at = _utcnow()
    _dismiss_alerts_for_budget(f"category:{row.id}", row.month)
    _commit()
    return True


def undo_delete_budget(budget_id):
    try:
        bid = int(budget_id)
    except (TypeError, ValueError):
        return False
    row = db.session.execute(
        select(Budget).where(Budget.id == bid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is None:
        return False
    row.deleted_at = None
    _commit()
    return True


def overall_budget(month=None, include_deleted=False):
    start = month_start(month)
    stmt = select(MonthlyBudget).where(MonthlyBudget.month == start)
    if not include_deleted:
        stmt = stmt.where(MonthlyBudget.deleted_at.is_(None))
    row = db.session.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    return {
        "id": str(row.id),
        "month": row.month.isoformat(),
        "month_key": month_key(row.month),
        "monthly_limit": row.monthly_limit,
        "alert_threshold": int(row.alert_threshold or DEFAULT_ALERT_THRESHOLD),
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else "",
    }


def upsert_overall_budget(
    month,
    monthly_limit,
    alert_threshold=DEFAULT_ALERT_THRESHOLD,
):
    start = month_start(month)
    limit = _clean_budget_amount(monthly_limit)
    threshold = _clean_alert_threshold(alert_threshold)
    row = db.session.execute(
        select(MonthlyBudget)
        .where(MonthlyBudget.month == start)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        row = MonthlyBudget(
            user_id=None,
            month=start,
            monthly_limit=limit,
            alert_threshold=threshold,
        )
        db.session.add(row)
    else:
        row.monthly_limit = limit
        row.alert_threshold = threshold
        row.deleted_at = None
    _commit()
    return str(row.id)


def delete_overall_budget(budget_id):
    try:
        bid = int(budget_id)
    except (TypeError, ValueError):
        return False
    row = db.session.execute(
        select(MonthlyBudget).where(MonthlyBudget.id == bid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        return False
    row.deleted_at = _utcnow()
    _dismiss_alerts_for_budget("overall", row.month)
    _commit()
    return True


def undo_delete_overall_budget(budget_id):
    try:
        bid = int(budget_id)
    except (TypeError, ValueError):
        return False
    row = db.session.execute(
        select(MonthlyBudget).where(MonthlyBudget.id == bid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is None:
        return False
    row.deleted_at = None
    _commit()
    return True


def budget_overview(month=None):
    start = month_start(month)
    end = next_month_start(start)
    budgets = list_budgets_with_spent(start)
    overall = overall_budget(start)
    total_spent = sum_by_type("expense", start, end)
    total_budgeted = sum(
        (b["effective_limit"] for b in budgets), Decimal("0.00")
    )
    if overall:
        overall_limit = overall["monthly_limit"]
        overall_remaining = overall_limit - total_spent
        overall_percent = (
            float((total_spent / overall_limit) * 100) if overall_limit > 0 else 0.0
        )
        overall_threshold = int(
            overall.get("alert_threshold") or DEFAULT_ALERT_THRESHOLD
        )
        overall.update(
            {
                "spent": total_spent,
                "remaining": overall_remaining,
                "over_amount": max(-overall_remaining, Decimal("0.00")),
                "percent": round(overall_percent, 2),
                "status": (
                    "over"
                    if overall_percent >= 100
                    else ("warning" if overall_percent >= overall_threshold else "normal")
                ),
            }
        )
    else:
        overall_limit = total_budgeted
        overall_remaining = total_budgeted - total_spent
        overall_percent = (
            float((total_spent / overall_limit) * 100) if overall_limit > 0 else 0.0
        )
        overall_threshold = DEFAULT_ALERT_THRESHOLD
    days_in_month, days_elapsed, days_left = _month_pace(start)
    safe_to_spend = (
        _q2(max(overall_remaining, Decimal("0.00")) / days_left)
        if days_left > 0
        else Decimal("0.00")
    )
    budgeted_ids = {b["category_id"] for b in budgets}
    spending = spending_by_category_month(start, active_only=True)
    unbudgeted = [row for row in spending if row["id"] not in budgeted_ids]
    over_budgets = [b for b in budgets if b["status"] == "over"]
    return {
        "month": start,
        "month_value": month_key(start),
        "month_label": start.strftime("%B %Y"),
        "previous_month": month_key(previous_month_start(start)),
        "next_month": month_key(next_month_start(start)),
        "budgets": budgets,
        "overall": overall,
        "overall_limit": overall_limit,
        "overall_spent": total_spent,
        "overall_remaining": overall_remaining,
        "overall_percent": round(overall_percent, 2),
        "overall_status": (
            "over"
            if overall_percent >= 100
            else ("warning" if overall_percent >= overall_threshold else "normal")
        ),
        "total_budgeted": total_budgeted,
        "total_spent": total_spent,
        "total_remaining": total_budgeted - total_spent,
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "days_left": days_left,
        "safe_to_spend": safe_to_spend,
        "unbudgeted": unbudgeted,
        "over_budgets": over_budgets,
    }


def preview_copy_budgets(target_month=None):
    target = month_start(target_month)
    source = previous_month_start(target)
    source_budgets = list_budgets(source)
    existing = {b["category_id"] for b in list_budgets(target)}
    items = []
    for budget in source_budgets:
        skip = "already has a budget this month" if budget["category_id"] in existing else ""
        items.append(
            {
                "name": budget["name"],
                "category_id": budget["category_id"],
                "monthly_limit": budget["monthly_limit"],
                "rollover": budget["rollover"],
                "alert_threshold": budget["alert_threshold"],
                "skip": skip,
            }
        )
    return {
        "source_month": month_key(source),
        "source_label": source.strftime("%B %Y"),
        "target_month": month_key(target),
        "target_label": target.strftime("%B %Y"),
        "items": items,
        "copy_count": len([item for item in items if not item["skip"]]),
    }


def _backup_before_bulk():
    scripts_dir = BASE_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import backup as backup_tool

        target, _removed = backup_tool.backup_database()
        return str(target)
    except Exception as exc:  # noqa: BLE001 - never bulk-write without a backup
        raise SaveError(
            "the automatic backup before copying budgets failed "
            f"({_mask_secret(exc)}), so nothing was changed"
        ) from exc


def copy_budgets_from_last_month(target_month=None):
    preview = preview_copy_budgets(target_month)
    to_copy = [item for item in preview["items"] if not item["skip"]]
    if not to_copy:
        return {"copied": 0, "preview": preview}
    _backup_before_bulk()
    start = month_start(target_month)
    for item in to_copy:
        db.session.add(
            Budget(
                user_id=None,
                category_id=int(item["category_id"]),
                month=start,
                monthly_limit=item["monthly_limit"],
                rollover=bool(item["rollover"]),
                alert_threshold=int(item["alert_threshold"]),
            )
        )
    _commit()
    return {"copied": len(to_copy), "preview": preview}


def _ensure_budget_alert(
    budget_key,
    scope,
    category_id,
    month,
    level,
    message,
):
    existing = db.session.execute(
        select(BudgetAlert).where(
            BudgetAlert.budget_key == budget_key,
            BudgetAlert.month == month,
            BudgetAlert.level == level,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    db.session.add(
        BudgetAlert(
            user_id=None,
            budget_key=budget_key,
            scope=scope,
            category_id=int(category_id) if category_id is not None else None,
            month=month,
            level=level,
            message=message,
        )
    )
    return True


def refresh_budget_alerts(month=None):
    """Create any missing threshold/over alerts. Idempotent per month."""
    start = month_start(month)
    created = []
    for budget in list_budgets_with_spent(start):
        key = f"category:{budget['id']}"
        if budget["percent"] >= 100:
            if _ensure_budget_alert(
                key,
                "category",
                budget["category_id"],
                start,
                "over",
                f"{budget['name']} is over its monthly budget.",
            ):
                created.append(key + ":over")
        elif budget["percent"] >= budget["alert_threshold"]:
            if _ensure_budget_alert(
                key,
                "category",
                budget["category_id"],
                start,
                "threshold",
                f"{budget['name']} has reached "
                f"{budget['alert_threshold']}% of its monthly budget.",
            ):
                created.append(key + ":threshold")
    overall = overall_budget(start)
    if overall:
        spent = sum_by_type("expense", start, next_month_start(start))
        limit = overall["monthly_limit"]
        percent = float((spent / limit) * 100) if limit > 0 else 0.0
        if percent >= 100:
            if _ensure_budget_alert(
                "overall",
                "overall",
                None,
                start,
                "over",
                "Your overall monthly budget is over its limit.",
            ):
                created.append("overall:over")
        elif percent >= overall["alert_threshold"]:
            if _ensure_budget_alert(
                "overall",
                "overall",
                None,
                start,
                "threshold",
                f"Your overall monthly budget has reached "
                f"{overall['alert_threshold']}% of its limit.",
            ):
                created.append("overall:threshold")
    if created:
        _commit()
    return created


def list_alerts(limit=50, include_dismissed=False):
    stmt = select(BudgetAlert).order_by(
        BudgetAlert.created_at.desc(), BudgetAlert.id.desc()
    )
    if not include_dismissed:
        stmt = stmt.where(BudgetAlert.dismissed_at.is_(None))
    rows = db.session.execute(stmt.limit(limit)).scalars().all()
    return [
        {
            "id": str(row.id),
            "budget_key": row.budget_key,
            "scope": row.scope,
            "category_id": str(row.category_id) if row.category_id else None,
            "month": row.month.isoformat(),
            "month_key": month_key(row.month),
            "level": row.level,
            "message": row.message,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }
        for row in rows
    ]


def dismiss_alert(alert_id):
    try:
        aid = int(alert_id)
    except (TypeError, ValueError):
        return False
    row = db.session.execute(
        select(BudgetAlert).where(BudgetAlert.id == aid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.dismissed_at is not None:
        return False
    row.dismissed_at = _utcnow()
    _commit()
    return True


def dismiss_all_alerts():
    rows = db.session.execute(
        select(BudgetAlert).where(BudgetAlert.dismissed_at.is_(None)).with_for_update()
    ).scalars().all()
    now = _utcnow()
    for row in rows:
        row.dismissed_at = now
    if rows:
        _commit()
    return len(rows)


# ---------------------------------------------------------------------------
# piggy bank
# ---------------------------------------------------------------------------

def _clean_piggy_amount(value, field="Amount"):
    try:
        amount = _q2(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PiggyValidationError(f"Enter a valid {field.lower()}.") from exc
    if amount < PIGGY_MIN_AMOUNT:
        raise PiggyValidationError(f"{field} must be greater than 0.")
    if amount > PIGGY_MAX_AMOUNT:
        raise PiggyValidationError(f"{field} must be {PIGGY_MAX_AMOUNT:,.2f} or less.")
    return amount


def _clean_piggy_name(value, field, max_length=PIGGY_NAME_MAX):
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        raise PiggyValidationError(f"Enter a {field.lower()}.")
    if len(clean) > max_length:
        raise PiggyValidationError(f"{field} must be {max_length} characters or fewer.")
    return clean


def _clean_piggy_contributor(value):
    key = str(value or "").strip().lower()
    if key not in PIGGY_CONTRIBUTORS:
        raise PiggyValidationError("Choose Me or Her for this entry.")
    return key


def _clean_piggy_entry_type(value):
    kind = str(value or "").strip().lower()
    if kind not in PIGGY_ENTRY_TYPES:
        raise PiggyValidationError("Choose deposit or withdrawal.")
    return kind


def _clean_piggy_date(value):
    if value is None or value == "":
        return date.today()
    try:
        return _as_date(value)
    except (TypeError, ValueError) as exc:
        raise PiggyValidationError("Date must be a valid calendar date.") from exc


def _clean_piggy_note(value):
    clean = str(value or "").strip()
    if len(clean) > PIGGY_NOTE_MAX:
        raise PiggyValidationError(
            f"Note must be {PIGGY_NOTE_MAX} characters or fewer."
        )
    return clean


def _piggy_bank_id(value):
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PiggyNotFoundError("That piggy bank does not exist.") from exc


def serialize_piggy_bank(row):
    return {
        "id": str(row.id),
        "name": row.name,
        "daily_amount": str(row.daily_amount),
        "contributor_me": row.contributor_me,
        "contributor_her": row.contributor_her,
        "currency": row.currency,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else "",
    }


def _piggy_contributor_name(bank, key):
    if bank is None:
        return "Me" if key == "me" else "Her"
    return bank.contributor_me if key == "me" else bank.contributor_her


def serialize_piggy_entry(row, bank=None):
    amount = _q2(row.amount)
    signed = amount if row.entry_type == "deposit" else -amount
    return {
        "id": str(row.id),
        "piggy_bank_id": str(row.piggy_bank_id),
        "contributor": row.contributor,
        "contributor_name": _piggy_contributor_name(bank, row.contributor),
        "entry_type": row.entry_type,
        "amount": str(amount),
        "signed_amount": str(_q2(signed)),
        "entry_date": row.entry_date.isoformat(),
        "note": row.note or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else "",
        "is_deleted": row.deleted_at is not None,
    }


def ensure_piggy_bank(create=True):
    """Return the active piggy bank, creating the default one on first use."""
    row = db.session.execute(
        select(PiggyBank)
        .where(PiggyBank.deleted_at.is_(None))
        .order_by(PiggyBank.id)
        .limit(1)
    ).scalar_one_or_none()
    if row is None and create:
        row = PiggyBank(
            user_id=None,
            name="Our Piggy Bank",
            daily_amount=PIGGY_DAILY_DEFAULT,
            contributor_me="Me",
            contributor_her="Her",
            currency="INR",
        )
        db.session.add(row)
        _commit()
    return row


def get_piggy_bank(include_deleted=False):
    stmt = select(PiggyBank).order_by(PiggyBank.id).limit(1)
    if not include_deleted:
        stmt = stmt.where(PiggyBank.deleted_at.is_(None))
    row = db.session.execute(stmt).scalar_one_or_none()
    return serialize_piggy_bank(row) if row else None


def update_piggy_settings(
    name=None,
    daily_amount=None,
    contributor_me=None,
    contributor_her=None,
):
    row = ensure_piggy_bank()
    if name is not None:
        row.name = _clean_piggy_name(name, "Piggy bank name")
    if daily_amount is not None:
        row.daily_amount = _clean_piggy_amount(daily_amount, "Daily amount")
    if contributor_me is not None:
        row.contributor_me = _clean_piggy_name(
            contributor_me, "First contributor name", PIGGY_CONTRIBUTOR_MAX
        )
    if contributor_her is not None:
        row.contributor_her = _clean_piggy_name(
            contributor_her, "Second contributor name", PIGGY_CONTRIBUTOR_MAX
        )
    if row.contributor_me.casefold() == row.contributor_her.casefold():
        raise PiggyValidationError("The two contributor names must be different.")
    _commit()
    return serialize_piggy_bank(row)


def _live_piggy_entries_stmt(bank_id):
    return select(PiggyEntry).where(
        PiggyEntry.piggy_bank_id == int(bank_id),
        PiggyEntry.deleted_at.is_(None),
    )


def _piggy_sum(bank_id, entry_type, date_from=None, date_to=None):
    stmt = select(func.coalesce(func.sum(PiggyEntry.amount), 0)).where(
        PiggyEntry.piggy_bank_id == int(bank_id),
        PiggyEntry.entry_type == entry_type,
        PiggyEntry.deleted_at.is_(None),
    )
    if date_from is not None:
        stmt = stmt.where(PiggyEntry.entry_date >= _as_date(date_from))
    if date_to is not None:
        stmt = stmt.where(PiggyEntry.entry_date <= _as_date(date_to))
    return _q2(db.session.execute(stmt).scalar())


def piggy_balance(bank_id=None, as_of=None):
    """All-time deposits minus withdrawals (optionally as of a date)."""
    if bank_id is None:
        bank = ensure_piggy_bank()
        bank_id = bank.id
    deposits = _piggy_sum(bank_id, "deposit", date_to=as_of)
    withdrawals = _piggy_sum(bank_id, "withdrawal", date_to=as_of)
    return _q2(deposits - withdrawals)


def _piggy_filtered_stmt(
    bank_id,
    month=None,
    date_from=None,
    date_to=None,
    contributor=None,
    entry_type=None,
    include_deleted=False,
):
    stmt = select(PiggyEntry).where(PiggyEntry.piggy_bank_id == int(bank_id))
    if not include_deleted:
        stmt = stmt.where(PiggyEntry.deleted_at.is_(None))
    if month not in (None, ""):
        start = month_start(month)
        stmt = stmt.where(
            PiggyEntry.entry_date >= start,
            PiggyEntry.entry_date < next_month_start(start),
        )
    if date_from not in (None, ""):
        stmt = stmt.where(PiggyEntry.entry_date >= _as_date(date_from))
    if date_to not in (None, ""):
        stmt = stmt.where(PiggyEntry.entry_date <= _as_date(date_to))
    if contributor not in (None, "", "all"):
        stmt = stmt.where(PiggyEntry.contributor == _clean_piggy_contributor(contributor))
    if entry_type not in (None, "", "all"):
        stmt = stmt.where(PiggyEntry.entry_type == _clean_piggy_entry_type(entry_type))
    return stmt


def list_piggy_entries(
    month=None,
    date_from=None,
    date_to=None,
    contributor=None,
    entry_type=None,
    include_deleted=False,
    newest_first=True,
    limit=None,
):
    bank = ensure_piggy_bank()
    stmt = _piggy_filtered_stmt(
        bank.id,
        month=month,
        date_from=date_from,
        date_to=date_to,
        contributor=contributor,
        entry_type=entry_type,
        include_deleted=include_deleted,
    )
    if newest_first:
        stmt = stmt.order_by(PiggyEntry.entry_date.desc(), PiggyEntry.id.desc())
    else:
        stmt = stmt.order_by(PiggyEntry.entry_date.asc(), PiggyEntry.id.asc())
    if limit:
        stmt = stmt.limit(int(limit))
    rows = db.session.execute(stmt).scalars().all()
    return [serialize_piggy_entry(row, bank) for row in rows]


def get_piggy_entry(entry_id, include_deleted=False):
    try:
        eid = int(entry_id)
    except (TypeError, ValueError):
        return None
    row = db.session.get(PiggyEntry, eid)
    if row is None:
        return None
    if not include_deleted and row.deleted_at is not None:
        return None
    bank = db.session.get(PiggyBank, row.piggy_bank_id)
    return serialize_piggy_entry(row, bank)


def _duplicate_deposit(
    bank_id, contributor, entry_date, exclude_entry_id=None
):
    stmt = select(PiggyEntry.id).where(
        PiggyEntry.piggy_bank_id == int(bank_id),
        PiggyEntry.contributor == contributor,
        PiggyEntry.entry_type == "deposit",
        PiggyEntry.entry_date == entry_date,
        PiggyEntry.deleted_at.is_(None),
    )
    if exclude_entry_id is not None:
        stmt = stmt.where(PiggyEntry.id != int(exclude_entry_id))
    return db.session.execute(stmt.limit(1)).scalar_one_or_none() is not None


def _assert_withdrawal_allowed(
    bank_id, entry_date, amount, exclude_entry_id=None
):
    """Simulate every live entry plus this withdrawal and reject a negative."""
    stmt = select(
        PiggyEntry.id,
        PiggyEntry.entry_date,
        PiggyEntry.entry_type,
        PiggyEntry.amount,
    ).where(
        PiggyEntry.piggy_bank_id == int(bank_id),
        PiggyEntry.deleted_at.is_(None),
    )
    if exclude_entry_id is not None:
        stmt = stmt.where(PiggyEntry.id != int(exclude_entry_id))
    rows = db.session.execute(stmt).all()
    items = [
        (row.entry_date, int(row.id), row.entry_type, _q2(row.amount))
        for row in rows
    ]
    next_id = max((item[1] for item in items), default=0) + 1
    items.append((entry_date, next_id, "withdrawal", _q2(amount)))
    items.sort(key=lambda item: (item[0], item[1]))

    balance = Decimal("0.00")
    for when, _entry_id, kind, value in items:
        balance += value if kind == "deposit" else -value
        if balance < 0:
            raise PiggyBalanceError(
                "That withdrawal would take the piggy bank below zero on "
                f"{when.strftime('%d %b %Y')}. Withdraw less or add a deposit first."
            )


def add_piggy_entry(
    contributor,
    entry_type,
    amount,
    entry_date=None,
    note="",
    allow_duplicate=False,
):
    bank = ensure_piggy_bank()
    contributor_key = _clean_piggy_contributor(contributor)
    kind = _clean_piggy_entry_type(entry_type)
    value = _clean_piggy_amount(amount)
    when = _clean_piggy_date(entry_date)
    clean_note = _clean_piggy_note(note)

    if kind == "deposit" and not allow_duplicate:
        if _duplicate_deposit(bank.id, contributor_key, when):
            raise DuplicatePiggyDepositError(
                f"{_piggy_contributor_name(bank, contributor_key)} already has a "
                f"deposit on {when.strftime('%d %b %Y')}. Add another anyway?"
            )
    if kind == "withdrawal":
        _assert_withdrawal_allowed(bank.id, when, value)

    row = PiggyEntry(
        piggy_bank_id=bank.id,
        user_id=None,
        contributor=contributor_key,
        entry_type=kind,
        amount=value,
        entry_date=when,
        note=clean_note,
    )
    db.session.add(row)
    _commit()
    return serialize_piggy_entry(row, bank)


def update_piggy_entry(
    entry_id,
    contributor=None,
    entry_type=None,
    amount=None,
    entry_date=None,
    note=None,
    allow_duplicate=False,
):
    try:
        eid = int(entry_id)
    except (TypeError, ValueError) as exc:
        raise PiggyNotFoundError("That piggy-bank entry does not exist.") from exc
    row = db.session.execute(
        select(PiggyEntry).where(PiggyEntry.id == eid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        raise PiggyNotFoundError("That piggy-bank entry does not exist.")

    bank = db.session.get(PiggyBank, row.piggy_bank_id)
    contributor_key = (
        row.contributor
        if contributor is None
        else _clean_piggy_contributor(contributor)
    )
    kind = row.entry_type if entry_type is None else _clean_piggy_entry_type(entry_type)
    value = _q2(row.amount) if amount is None else _clean_piggy_amount(amount)
    when = row.entry_date if entry_date in (None, "") else _clean_piggy_date(entry_date)
    clean_note = row.note or "" if note is None else _clean_piggy_note(note)

    if kind == "deposit" and not allow_duplicate:
        if _duplicate_deposit(bank.id, contributor_key, when, exclude_entry_id=eid):
            raise DuplicatePiggyDepositError(
                f"{_piggy_contributor_name(bank, contributor_key)} already has a "
                f"deposit on {when.strftime('%d %b %Y')}."
            )
    if kind == "withdrawal":
        _assert_withdrawal_allowed(bank.id, when, value, exclude_entry_id=eid)

    row.contributor = contributor_key
    row.entry_type = kind
    row.amount = value
    row.entry_date = when
    row.note = clean_note
    _commit()
    return serialize_piggy_entry(row, bank)


def delete_piggy_entry(entry_id):
    """SOFT delete: the row stays and can be undone."""
    try:
        eid = int(entry_id)
    except (TypeError, ValueError):
        return False
    row = db.session.execute(
        select(PiggyEntry).where(PiggyEntry.id == eid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        return False
    row.deleted_at = _utcnow()
    _commit()
    return True


def undo_delete_piggy_entry(entry_id):
    try:
        eid = int(entry_id)
    except (TypeError, ValueError):
        return False
    row = db.session.execute(
        select(PiggyEntry).where(PiggyEntry.id == eid).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.deleted_at is None:
        return False
    if row.entry_type == "withdrawal":
        _assert_withdrawal_allowed(
            row.piggy_bank_id, row.entry_date, row.amount, exclude_entry_id=eid
        )
    row.deleted_at = None
    _commit()
    return True


def list_deleted_piggy_entries(limit=50):
    bank = ensure_piggy_bank()
    rows = db.session.execute(
        select(PiggyEntry)
        .where(
            PiggyEntry.piggy_bank_id == bank.id,
            PiggyEntry.deleted_at.isnot(None),
        )
        .order_by(PiggyEntry.deleted_at.desc(), PiggyEntry.id.desc())
        .limit(int(limit))
    ).scalars().all()
    return [serialize_piggy_entry(row, bank) for row in rows]


def _piggy_month_entries(bank_id, start, end, include_deleted=False):
    stmt = select(PiggyEntry).where(
        PiggyEntry.piggy_bank_id == int(bank_id),
        PiggyEntry.entry_date >= start,
        PiggyEntry.entry_date < end,
    )
    if not include_deleted:
        stmt = stmt.where(PiggyEntry.deleted_at.is_(None))
    stmt = stmt.order_by(PiggyEntry.entry_date.asc(), PiggyEntry.id.asc())
    return db.session.execute(stmt).scalars().all()


def piggy_summary(month=None):
    """Everything the piggy page needs for one month, isolated from the rest."""
    bank = ensure_piggy_bank()
    start = month_start(month)
    end = next_month_start(start)
    days_in_month = (end - start).days

    all_rows = db.session.execute(
        _live_piggy_entries_stmt(bank.id).order_by(
            PiggyEntry.entry_date.asc(), PiggyEntry.id.asc()
        )
    ).scalars().all()

    total_deposits = Decimal("0.00")
    total_withdrawals = Decimal("0.00")
    opening = Decimal("0.00")
    month_deposits = Decimal("0.00")
    month_withdrawals = Decimal("0.00")
    contributor_totals = {
        key: {
            "key": key,
            "name": _piggy_contributor_name(bank, key),
            "deposits": Decimal("0.00"),
            "withdrawals": Decimal("0.00"),
            "count": 0,
            "month_deposits": Decimal("0.00"),
            "month_withdrawals": Decimal("0.00"),
            "month_count": 0,
        }
        for key in PIGGY_CONTRIBUTORS
    }
    entry_serialized = []

    for row in all_rows:
        amount = _q2(row.amount)
        in_month = start <= row.entry_date < end
        if row.entry_date < start:
            opening += amount if row.entry_type == "deposit" else -amount
        if row.entry_type == "deposit":
            total_deposits += amount
            if in_month:
                month_deposits += amount
        else:
            total_withdrawals += amount
            if in_month:
                month_withdrawals += amount
        bucket = contributor_totals[row.contributor]
        if row.entry_type == "deposit":
            bucket["deposits"] += amount
            if in_month:
                bucket["month_deposits"] += amount
        else:
            bucket["withdrawals"] += amount
            if in_month:
                bucket["month_withdrawals"] += amount
        bucket["count"] += 1
        if in_month:
            bucket["month_count"] += 1
            entry_serialized.append(serialize_piggy_entry(row, bank))

    opening = _q2(opening)
    total_deposits = _q2(total_deposits)
    total_withdrawals = _q2(total_withdrawals)
    month_deposits = _q2(month_deposits)
    month_withdrawals = _q2(month_withdrawals)
    closing = _q2(opening + month_deposits - month_withdrawals)

    by_date = {}
    for entry in entry_serialized:
        by_date.setdefault(entry["entry_date"], []).append(entry)

    running = opening
    days = []
    for day_number in range(1, days_in_month + 1):
        when = date(start.year, start.month, day_number)
        key = when.isoformat()
        day_entries = by_date.get(key, [])
        dep = sum(
            (Decimal(e["amount"]) for e in day_entries if e["entry_type"] == "deposit"),
            Decimal("0.00"),
        )
        wd = sum(
            (
                Decimal(e["amount"])
                for e in day_entries
                if e["entry_type"] == "withdrawal"
            ),
            Decimal("0.00"),
        )
        running = _q2(running + dep - wd)
        days.append(
            {
                "date": key,
                "day": day_number,
                "weekday": when.weekday(),
                "is_today": when == date.today(),
                "is_future": when > date.today(),
                "deposits": str(_q2(dep)),
                "withdrawals": str(_q2(wd)),
                "net": str(_q2(dep - wd)),
                "balance": str(running),
                "entry_count": len(day_entries),
                "entries": day_entries,
            }
        )

    first_weekday = start.weekday()
    padded = [None] * first_weekday + days
    while len(padded) % 7:
        padded.append(None)
    weeks = [padded[i:i + 7] for i in range(0, len(padded), 7)]

    contributors = []
    for key in PIGGY_CONTRIBUTORS:
        row = contributor_totals[key]
        row["balance"] = _q2(row["deposits"] - row["withdrawals"])
        row["month_net"] = _q2(row["month_deposits"] - row["month_withdrawals"])
        contributors.append(row)

    today = date.today()
    days_elapsed = (
        today.day
        if start == today.replace(day=1)
        else (days_in_month if start < today.replace(day=1) else 0)
    )
    expected_to_date = _q2(bank.daily_amount * days_elapsed * len(PIGGY_CONTRIBUTORS))
    recent = [serialize_piggy_entry(row, bank) for row in reversed(all_rows[-10:])]

    return {
        "bank": serialize_piggy_bank(bank),
        "month": start.isoformat(),
        "month_value": month_key(start),
        "month_label": start.strftime("%B %Y"),
        "previous_month": month_key(previous_month_start(start)),
        "next_month": month_key(next_month_start(start)),
        "is_current_month": start == today.replace(day=1),
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "opening_balance": str(opening),
        "closing_balance": str(closing),
        "total_deposits": str(total_deposits),
        "total_withdrawals": str(total_withdrawals),
        "balance": str(_q2(total_deposits - total_withdrawals)),
        "month_deposits": str(month_deposits),
        "month_withdrawals": str(month_withdrawals),
        "month_net": str(_q2(month_deposits - month_withdrawals)),
        "month_entry_count": len(entry_serialized),
        "entries": entry_serialized,
        "recent": recent,
        "days": days,
        "weeks": weeks,
        "contributors": contributors,
        "daily_amount": str(_q2(bank.daily_amount)),
        "expected_to_date": str(expected_to_date),
        "saved_to_date": str(_q2(month_deposits - month_withdrawals)),
    }


def piggy_month_series(end_month=None, months=6):
    """Deposits/withdrawals/balance for the last N months, oldest first."""
    bank = ensure_piggy_bank()
    start_month = month_start(end_month)
    keys = [start_month]
    for _ in range(max(int(months), 1) - 1):
        keys.append(previous_month_start(keys[-1]))
    keys.reverse()

    rows = db.session.execute(
        _live_piggy_entries_stmt(bank.id).order_by(
            PiggyEntry.entry_date.asc(), PiggyEntry.id.asc()
        )
    ).scalars().all()

    series = []
    for index, first in enumerate(keys):
        end = next_month_start(first)
        dep = Decimal("0.00")
        wd = Decimal("0.00")
        for row in rows:
            if first <= row.entry_date < end:
                if row.entry_type == "deposit":
                    dep += _q2(row.amount)
                else:
                    wd += _q2(row.amount)
        balance = Decimal("0.00")
        for row in rows:
            if row.entry_date < end:
                balance += _q2(row.amount) if row.entry_type == "deposit" else -_q2(row.amount)
        series.append(
            {
                "month_key": month_key(first),
                "label": first.strftime("%b"),
                "label_long": first.strftime("%B %Y"),
                "deposits": float(_q2(dep)),
                "withdrawals": float(_q2(wd)),
                "net": float(_q2(dep - wd)),
                "balance": float(_q2(balance)),
            }
        )
    return series


def piggy_overview():
    """Small read-only snapshot for the dashboard banner."""
    bank = ensure_piggy_bank()
    balance = piggy_balance(bank.id)
    start = month_start()
    end = next_month_start(start)
    contributors = []
    for key in PIGGY_CONTRIBUTORS:
        deposits = _q2(
            db.session.execute(
                select(func.coalesce(func.sum(PiggyEntry.amount), 0)).where(
                    PiggyEntry.piggy_bank_id == bank.id,
                    PiggyEntry.contributor == key,
                    PiggyEntry.entry_type == "deposit",
                    PiggyEntry.deleted_at.is_(None),
                )
            ).scalar()
        )
        withdrawals = _q2(
            db.session.execute(
                select(func.coalesce(func.sum(PiggyEntry.amount), 0)).where(
                    PiggyEntry.piggy_bank_id == bank.id,
                    PiggyEntry.contributor == key,
                    PiggyEntry.entry_type == "withdrawal",
                    PiggyEntry.deleted_at.is_(None),
                )
            ).scalar()
        )
        contributors.append(
            {
                "key": key,
                "name": _piggy_contributor_name(bank, key),
                "balance": str(_q2(deposits - withdrawals)),
            }
        )
    return {
        "bank": serialize_piggy_bank(bank),
        "balance": str(balance),
        "month_deposits": str(_piggy_sum(bank.id, "deposit", start, end)),
        "month_withdrawals": str(_piggy_sum(bank.id, "withdrawal", start, end)),
        "contributors": contributors,
    }


def serialize_piggy_statement(row):
    return {
        "id": str(row.id),
        "piggy_bank_id": str(row.piggy_bank_id),
        "period_start": row.period_start.isoformat() if row.period_start else "",
        "period_end": row.period_end.isoformat() if row.period_end else "",
        "opening_balance": str(_q2(row.opening_balance)),
        "closing_balance": str(_q2(row.closing_balance)),
        "total_deposits": str(_q2(row.total_deposits)),
        "total_withdrawals": str(_q2(row.total_withdrawals)),
        "entry_count": int(row.entry_count or 0),
        "file_name": row.file_name or "",
        "file_path": row.file_path or "",
        "file_sha256": row.file_sha256 or "",
        "generated_at": row.generated_at.isoformat() if row.generated_at else "",
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else "",
    }


def save_piggy_statement(
    period_start,
    period_end,
    opening_balance,
    closing_balance,
    total_deposits,
    total_withdrawals,
    entry_count,
    file_name,
    file_path,
    file_sha256,
):
    """Record a generated PDF statement and the hash of its bytes."""
    bank = ensure_piggy_bank()
    first = _as_date(period_start)
    last = _as_date(period_end)
    row = PiggyStatement(
        piggy_bank_id=bank.id,
        user_id=None,
        period_start=first,
        period_end=last,
        opening_balance=_q2(opening_balance),
        closing_balance=_q2(closing_balance),
        total_deposits=_q2(total_deposits),
        total_withdrawals=_q2(total_withdrawals),
        entry_count=int(entry_count or 0),
        file_name=str(file_name)[:255],
        file_path=str(file_path)[:512],
        file_sha256=str(file_sha256)[:64],
    )
    db.session.add(row)
    _commit()
    return serialize_piggy_statement(row)


def list_piggy_statements(limit=10):
    bank = ensure_piggy_bank()
    stmt = (
        select(PiggyStatement)
        .where(
            PiggyStatement.piggy_bank_id == bank.id,
            PiggyStatement.deleted_at.is_(None),
        )
        .order_by(PiggyStatement.generated_at.desc(), PiggyStatement.id.desc())
    )
    if limit:
        stmt = stmt.limit(int(limit))
    rows = db.session.execute(stmt).scalars().all()
    return [serialize_piggy_statement(row) for row in rows]


def get_piggy_statement(statement_id, include_deleted=False):
    try:
        sid = int(statement_id)
    except (TypeError, ValueError):
        return None
    row = db.session.get(PiggyStatement, sid)
    if row is None:
        return None
    if not include_deleted and row.deleted_at is not None:
        return None
    return serialize_piggy_statement(row)


__all__ = [
    "DatabaseUnavailable",
    "DuplicateKeyError",
    "SaveError",
    "CategoryActionError",
    "CategoryNotFoundError",
    "PiggyError",
    "PiggyNotFoundError",
    "PiggyValidationError",
    "PiggyBalanceError",
    "DuplicatePiggyDepositError",
    "CATEGORY_NAME_MAX",
    "PIGGY_DAILY_DEFAULT",
    "PIGGY_MAX_AMOUNT",
    "PIGGY_MIN_AMOUNT",
    "PIGGY_CONTRIBUTORS",
    "PIGGY_ENTRY_TYPES",
    "db",
    "User",
    "Category",
    "Transaction",
    "Budget",
    "MonthlyBudget",
    "BudgetAlert",
    "CategoryDeletion",
    "PiggyBank",
    "PiggyEntry",
    "PiggyStatement",
    "init_db",
    "ping",
    "testing_enabled",
    "table_row_counts",
    "startup_report",
    "list_categories",
    "list_archived_categories",
    "list_deleted_categories",
    "list_categories_by_type",
    "get_category",
    "get_category_any",
    "add_category",
    "validate_category_name",
    "rename_category",
    "category_usage",
    "delete_category",
    "restore_archived_category",
    "undo_delete_category",
    "list_transactions",
    "recent_transactions",
    "count_transactions",
    "get_transaction",
    "add_transaction",
    "update_transaction",
    "delete_transaction",
    "undo_transaction",
    "list_deleted_transactions",
    "sum_by_type",
    "sum_for_category",
    "spending_by_category",
    "monthly_series",
    "month_start",
    "next_month_start",
    "previous_month_start",
    "month_key",
    "list_budgets",
    "list_budgets_with_spent",
    "get_budget",
    "budget_for_category_month",
    "upsert_budget",
    "update_budget",
    "delete_budget",
    "undo_delete_budget",
    "overall_budget",
    "upsert_overall_budget",
    "delete_overall_budget",
    "undo_delete_overall_budget",
    "budget_overview",
    "preview_copy_budgets",
    "copy_budgets_from_last_month",
    "refresh_budget_alerts",
    "list_alerts",
    "dismiss_alert",
    "dismiss_all_alerts",
    "serialize_piggy_bank",
    "serialize_piggy_entry",
    "ensure_piggy_bank",
    "get_piggy_bank",
    "update_piggy_settings",
    "list_piggy_entries",
    "get_piggy_entry",
    "add_piggy_entry",
    "update_piggy_entry",
    "delete_piggy_entry",
    "undo_delete_piggy_entry",
    "list_deleted_piggy_entries",
    "piggy_balance",
    "piggy_summary",
    "piggy_month_series",
    "piggy_overview",
    "serialize_piggy_statement",
    "save_piggy_statement",
    "list_piggy_statements",
    "get_piggy_statement",
]
