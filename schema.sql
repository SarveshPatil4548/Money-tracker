-- Money Tracker — MySQL 8 schema
-- Money is DECIMAL(12,2), never FLOAT. All DATETIMEs are UTC.
-- The app creates these tables automatically on startup (db.create_all());
-- this file exists so you can also build the schema by hand in MySQL Workbench:
--   run: USE money_tracker; then execute this file.

SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- users (used by the multi-user API; the Flask app is single-user and leaves
-- user_id NULL on its rows)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
    email         VARCHAR(255) NOT NULL,
    name          VARCHAR(120) NOT NULL,
    password_hash VARCHAR(255) NULL,
    timezone      VARCHAR(64)  NOT NULL DEFAULT 'UTC',
    base_currency CHAR(3)      NOT NULL DEFAULT 'INR',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- accounts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id         INT UNSIGNED   NOT NULL AUTO_INCREMENT,
    user_id    INT UNSIGNED   NOT NULL,
    name       VARCHAR(120)   NOT NULL,
    type       VARCHAR(16)    NOT NULL DEFAULT 'cash',
    currency   CHAR(3)        NOT NULL DEFAULT 'INR',
    balance    DECIMAL(12,2)  NOT NULL DEFAULT 0.00,
    created_at DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_accounts_user_name (user_id, name),
    KEY idx_accounts_user_id (user_id),
    CONSTRAINT fk_accounts_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT chk_accounts_type CHECK (
        type IN ('cash', 'bank', 'wallet', 'card', 'other')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- categories (Flask app rows have user_id NULL; uniqueness for those is
-- enforced in db.py because MySQL treats NULLs as distinct in unique keys)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    id          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id     INT UNSIGNED  NULL,
    name        VARCHAR(80)   NOT NULL,
    type        VARCHAR(16)   NOT NULL,
    created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at DATETIME      NULL,
    deleted_at  DATETIME      NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_categories_user_name (user_id, name),
    KEY idx_categories_user_id (user_id),
    KEY idx_categories_type_name (type, name),
    KEY idx_categories_archived_deleted (archived_at, deleted_at),
    CONSTRAINT fk_categories_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT chk_categories_type CHECK (type IN ('income', 'expense'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- transactions
-- Normal rows have category_id set. Transfer rows have category_id NULL and
-- both from_account_id / to_account_id set (enforced by CHECK below).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id         INT UNSIGNED  NULL,
    category_id     INT UNSIGNED  NULL,
    account_id      INT UNSIGNED  NULL,
    from_account_id INT UNSIGNED  NULL,
    to_account_id   INT UNSIGNED  NULL,
    amount          DECIMAL(12,2) NOT NULL,
    description     VARCHAR(255)  NULL,
    txn_date        DATE          NOT NULL,
    transfer_group  CHAR(36)      NULL,
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_transactions_user_id (user_id),
    KEY idx_transactions_date (txn_date),
    KEY idx_transactions_category_id (category_id),
    KEY idx_transactions_user_date_category (user_id, txn_date, category_id),
    KEY idx_transactions_category_date (category_id, txn_date),
    CONSTRAINT fk_transactions_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_transactions_category FOREIGN KEY (category_id)
        REFERENCES categories (id) ON DELETE RESTRICT,
    CONSTRAINT fk_transactions_account FOREIGN KEY (account_id)
        REFERENCES accounts (id) ON DELETE SET NULL,
    CONSTRAINT fk_transactions_from_account FOREIGN KEY (from_account_id)
        REFERENCES accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_transactions_to_account FOREIGN KEY (to_account_id)
        REFERENCES accounts (id) ON DELETE RESTRICT,
    CONSTRAINT chk_transactions_amount CHECK (amount > 0),
    CONSTRAINT chk_transactions_kind CHECK (
        category_id IS NOT NULL
        OR (from_account_id IS NOT NULL AND to_account_id IS NOT NULL)
    ),
    CONSTRAINT chk_transactions_transfer CHECK (
        from_account_id IS NULL OR to_account_id IS NULL
        OR from_account_id <> to_account_id
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- budgets (one budget per category per calendar month)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS budgets (
    id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id         INT UNSIGNED  NULL,
    category_id     INT UNSIGNED  NOT NULL,
    month           DATE          NOT NULL,
    monthly_limit   DECIMAL(12,2) NOT NULL,
    rollover        TINYINT(1)    NOT NULL DEFAULT 0,
    alert_threshold TINYINT UNSIGNED NOT NULL DEFAULT 80,
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at      DATETIME      NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_budgets_category_month (category_id, month),
    KEY idx_budgets_user_id (user_id),
    KEY idx_budgets_month_deleted (month, deleted_at),
    CONSTRAINT fk_budgets_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_budgets_category FOREIGN KEY (category_id)
        REFERENCES categories (id) ON DELETE RESTRICT,
    CONSTRAINT chk_budgets_limit CHECK (monthly_limit > 0),
    CONSTRAINT chk_budgets_threshold CHECK (alert_threshold BETWEEN 1 AND 100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- monthly_budgets (the overall limit for one calendar month)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS monthly_budgets (
    id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id         INT UNSIGNED  NULL,
    month           DATE          NOT NULL,
    monthly_limit   DECIMAL(12,2) NOT NULL,
    alert_threshold TINYINT UNSIGNED NOT NULL DEFAULT 80,
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at      DATETIME      NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_monthly_budgets_month (month),
    KEY idx_monthly_budgets_user_id (user_id),
    KEY idx_monthly_budgets_month_deleted (month, deleted_at),
    CONSTRAINT fk_monthly_budgets_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT chk_monthly_budgets_limit CHECK (monthly_limit > 0),
    CONSTRAINT chk_monthly_budgets_threshold CHECK (alert_threshold BETWEEN 1 AND 100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- budget_alerts (one threshold/over event per budget per month)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS budget_alerts (
    id            INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id       INT UNSIGNED  NULL,
    budget_key    VARCHAR(64)   NOT NULL,
    scope         VARCHAR(16)   NOT NULL,
    category_id   INT UNSIGNED  NULL,
    month         DATE          NOT NULL,
    level         VARCHAR(16)   NOT NULL,
    message       VARCHAR(255)  NOT NULL,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dismissed_at  DATETIME      NULL,
    email_sent_at DATETIME      NULL,
    push_sent_at  DATETIME      NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_budget_alerts_key_month_level (budget_key, month, level),
    KEY idx_budget_alerts_dismissed (dismissed_at, created_at),
    KEY idx_budget_alerts_month (month),
    CONSTRAINT fk_budget_alerts_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT chk_budget_alerts_scope CHECK (scope IN ('category', 'overall')),
    CONSTRAINT chk_budget_alerts_level CHECK (level IN ('threshold', 'over'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- category_deletions (undo trail for soft-deleted / reassigned categories)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS category_deletions (
    id                   INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    category_id          INT UNSIGNED  NOT NULL,
    target_category_id   INT UNSIGNED  NULL,
    action               VARCHAR(16)   NOT NULL,
    transaction_ids      TEXT          NULL,
    budget_id            INT UNSIGNED  NULL,
    budget_moved         TINYINT(1)    NOT NULL DEFAULT 0,
    budget_limit         DECIMAL(12,2) NULL,
    target_budget_id     INT UNSIGNED  NULL,
    target_budget_before DECIMAL(12,2) NULL,
    budget_changes       TEXT          NULL,
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    undone_at            DATETIME      NULL,
    PRIMARY KEY (id),
    KEY idx_category_deletions_category (category_id),
    CONSTRAINT fk_category_deletions_category FOREIGN KEY (category_id)
        REFERENCES categories (id) ON DELETE RESTRICT,
    CONSTRAINT fk_category_deletions_target FOREIGN KEY (target_category_id)
        REFERENCES categories (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- piggy_banks (separate savings pot; never part of income/expense totals)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS piggy_banks (
    id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id         INT UNSIGNED  NULL,
    name            VARCHAR(120)  NOT NULL DEFAULT 'Our Piggy Bank',
    daily_amount    DECIMAL(12,2) NOT NULL DEFAULT 50.00,
    contributor_me  VARCHAR(80)   NOT NULL DEFAULT 'Me',
    contributor_her VARCHAR(80)   NOT NULL DEFAULT 'Her',
    currency        CHAR(3)       NOT NULL DEFAULT 'INR',
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
                                  ON UPDATE CURRENT_TIMESTAMP,
    deleted_at      DATETIME      NULL,
    PRIMARY KEY (id),
    KEY idx_piggy_banks_user_id (user_id),
    KEY idx_piggy_banks_deleted_at (deleted_at),
    CONSTRAINT fk_piggy_banks_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT chk_piggy_banks_daily_amount CHECK (daily_amount > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- piggy_entries (deposits and withdrawals; soft delete only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS piggy_entries (
    id             INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    piggy_bank_id  INT UNSIGNED  NOT NULL,
    user_id        INT UNSIGNED  NULL,
    contributor    VARCHAR(8)    NOT NULL,
    entry_type     VARCHAR(16)   NOT NULL,
    amount         DECIMAL(12,2) NOT NULL,
    entry_date     DATE          NOT NULL,
    note           VARCHAR(255)  NULL,
    created_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP,
    deleted_at     DATETIME      NULL,
    PRIMARY KEY (id),
    KEY idx_piggy_entries_bank_id (piggy_bank_id),
    KEY idx_piggy_entries_entry_date (entry_date),
    KEY idx_piggy_entries_bank_date (piggy_bank_id, entry_date),
    KEY idx_piggy_entries_contributor_date (contributor, entry_date),
    KEY idx_piggy_entries_deleted_at (deleted_at),
    CONSTRAINT fk_piggy_entries_bank FOREIGN KEY (piggy_bank_id)
        REFERENCES piggy_banks (id) ON DELETE RESTRICT,
    CONSTRAINT fk_piggy_entries_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT chk_piggy_entries_amount CHECK (amount > 0),
    CONSTRAINT chk_piggy_entries_contributor CHECK (
        contributor IN ('me', 'her')
    ),
    CONSTRAINT chk_piggy_entries_type CHECK (
        entry_type IN ('deposit', 'withdrawal')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- piggy_statements (generated PDFs + sha256 for integrity verification)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS piggy_statements (
    id                INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    piggy_bank_id     INT UNSIGNED  NOT NULL,
    user_id           INT UNSIGNED  NULL,
    period_start      DATE          NOT NULL,
    period_end        DATE          NOT NULL,
    opening_balance   DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    closing_balance   DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    total_deposits    DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    total_withdrawals DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    entry_count       INT           NOT NULL DEFAULT 0,
    file_name         VARCHAR(255)  NOT NULL,
    file_path         VARCHAR(512)  NOT NULL,
    file_sha256       VARCHAR(64)   NOT NULL,
    generated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at        DATETIME      NULL,
    PRIMARY KEY (id),
    KEY idx_piggy_statements_bank_period (piggy_bank_id, period_start, period_end),
    KEY idx_piggy_statements_hash (file_sha256),
    KEY idx_piggy_statements_deleted_at (deleted_at),
    CONSTRAINT fk_piggy_statements_bank FOREIGN KEY (piggy_bank_id)
        REFERENCES piggy_banks (id) ON DELETE RESTRICT,
    CONSTRAINT fk_piggy_statements_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT chk_piggy_statements_period CHECK (period_end >= period_start),
    CONSTRAINT chk_piggy_statements_balances CHECK (
        opening_balance >= 0 AND closing_balance >= 0
    ),
    CONSTRAINT chk_piggy_statements_totals CHECK (
        total_deposits >= 0 AND total_withdrawals >= 0
    ),
    CONSTRAINT chk_piggy_statements_count CHECK (entry_count >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- recurring_rules
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recurring_rules (
    id             INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    user_id        INT UNSIGNED  NOT NULL,
    account_id     INT UNSIGNED  NULL,
    category_id    INT UNSIGNED  NOT NULL,
    name           VARCHAR(120)  NOT NULL,
    amount         DECIMAL(12,2) NOT NULL,
    frequency      VARCHAR(16)   NOT NULL,
    interval_count INT           NOT NULL DEFAULT 1,
    start_date     DATE          NOT NULL,
    end_date       DATE          NULL,
    next_due       DATE          NOT NULL,
    description    VARCHAR(255)  NULL,
    active         TINYINT(1)    NOT NULL DEFAULT 1,
    created_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_recurring_rules_user_id (user_id),
    KEY idx_recurring_rules_next_due (next_due),
    CONSTRAINT fk_recurring_rules_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_recurring_rules_account FOREIGN KEY (account_id)
        REFERENCES accounts (id) ON DELETE SET NULL,
    CONSTRAINT fk_recurring_rules_category FOREIGN KEY (category_id)
        REFERENCES categories (id) ON DELETE RESTRICT,
    CONSTRAINT chk_recurring_rules_amount CHECK (amount > 0),
    CONSTRAINT chk_recurring_rules_frequency CHECK (
        frequency IN ('daily', 'weekly', 'monthly', 'yearly')
    ),
    CONSTRAINT chk_recurring_rules_interval CHECK (interval_count >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- tags + join table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tags (
    id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id    INT UNSIGNED NOT NULL,
    name       VARCHAR(60)  NOT NULL,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tags_user_name (user_id, name),
    KEY idx_tags_user_id (user_id),
    CONSTRAINT fk_tags_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS transaction_tags (
    transaction_id INT UNSIGNED NOT NULL,
    tag_id         INT UNSIGNED NOT NULL,
    PRIMARY KEY (transaction_id, tag_id),
    KEY idx_transaction_tags_tag_id (tag_id),
    CONSTRAINT fk_transaction_tags_transaction FOREIGN KEY (transaction_id)
        REFERENCES transactions (id) ON DELETE CASCADE,
    CONSTRAINT fk_transaction_tags_tag FOREIGN KEY (tag_id)
        REFERENCES tags (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Default categories (same 12 the old Mongo/SQLite versions seeded)
-- ---------------------------------------------------------------------------
INSERT INTO categories (user_id, name, type)
SELECT NULL, s.name, s.type
FROM (
    SELECT 'Salary' AS name, 'income' AS type UNION ALL
    SELECT 'Freelance', 'income' UNION ALL
    SELECT 'Other Income', 'income' UNION ALL
    SELECT 'Rent', 'expense' UNION ALL
    SELECT 'Groceries', 'expense' UNION ALL
    SELECT 'Transport', 'expense' UNION ALL
    SELECT 'Utilities', 'expense' UNION ALL
    SELECT 'Entertainment', 'expense' UNION ALL
    SELECT 'Dining Out', 'expense' UNION ALL
    SELECT 'Shopping', 'expense' UNION ALL
    SELECT 'Health', 'expense' UNION ALL
    SELECT 'Other Expense', 'expense'
) AS s
WHERE NOT EXISTS (
    SELECT 1 FROM categories c
    WHERE c.name = s.name AND c.user_id IS NULL
);
