# MoneyWise (Flask + MySQL)

A server-rendered money tracker built with Flask and MySQL 8. Money is stored
as `DECIMAL(12,2)` (never float), all timestamps are stored in UTC, and every
query goes through the SQLAlchemy ORM (parameterised SQL only — no
string-built queries anywhere).

## Features

- Dashboard: monthly income/expenses, balance, 6-month trend chart, spending
  by category, budgets, recent activity
- Transactions: add / edit / delete (delete is reversible), grouped by date,
  client-side search, filter and sort, day totals
- Budgets: one monthly limit per category, tracked against current-month spend
- Categories: income/expense labels with icons
- `/health` endpoint that pings the database
- Atomic account-to-account transfers (`db.transfer_funds`, one DB
  transaction with row locks)
- Exports: CSV, JSON and a full `.sql` dump, downloadable from the app
- Automatic backups: one at every startup, one every day via Windows Task
  Scheduler, and the last 14 dumps are kept in `backups/`

## Requirements

- Python 3.13 (Windows, macOS or Linux)
- MySQL 8.0 running locally (default port 3306)
- An empty database, e.g. in MySQL Workbench:
  ```sql
  CREATE DATABASE IF NOT EXISTS money_tracker;
  ```

## Setup

### 1. Install dependencies

```powershell
cd C:\Users\HP\Downloads\money_tracker
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure the environment

Copy the example file and edit it — set your real MySQL password there and
only there (the `.env` file is git-ignored):

```powershell
Copy-Item .env.example .env
notepad .env
```

```ini
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password_here   <- your real MySQL password
DB_NAME=money_tracker
```

`DB_PASSWORD` may be left empty if your MySQL root user has no password.
If a variable is missing, the app exits at startup and tells you which one
to set and that `.env` is the file to edit. The password is never written to
code, logs or this README, and it is masked in any error message.

### 3. Run

```powershell
python app.py
# open http://localhost:5000
```

Use **http://localhost:5000** (not `127.0.0.1`); page requests on `127.0.0.1`
are redirected to `localhost` so the session cookie stays on one host. On first
run the app also writes a strong `FLASK_SECRET_KEY` into `.env` if the key is
missing or still the placeholder, and keeps it stable so logins and CSRF tokens
survive restarts.

On startup you should see `MySQL connected to money_tracker at localhost:3306`,
followed by a short report of the database name and the number of rows in each
table. Tables are created automatically **if they do not exist yet** (the same
DDL lives in `schema.sql` if you prefer to run it by hand in MySQL
Workbench), and the 12 default categories are seeded **only when the
categories table is empty**. Startup never deletes, truncates or resets
anything — it only ever adds what is missing.

Check the live DB probe:

```powershell
curl http://localhost:5000/health
# 200 -> {"status": "ok", "database": "money_tracker"}
# 503 -> {"status": "error", "error": "..."} (details, password masked)
```

## Data safety

The rules the app now follows, so a stray script or a bad deploy can never
wipe your money again:

| Rule | Where it lives |
| --- | --- |
| Startup only **creates missing** tables/columns and seeds categories when the table is empty — it never deletes | `db.py` → `_create_missing_tables()`, `_ensure_schema()`, `_seed_categories()` |
| Deleting a transaction **soft-deletes** it (`deleted_at`); nothing is removed from the table, and you can undo it | `db.py` → `delete_transaction()`, `undo_transaction()` |
| Every write either commits or rolls back and reports the failure — a failed save never leaves half a row | `db.py` → `_commit()`, `SaveError` |
| There is **no SQLite / in-memory fallback**; if MySQL is unreachable the app exits loudly | `db.py` → `DatabaseUnavailable` |
| The database name is validated at startup, and under `TESTING` the production database is refused outright | `db.py` → `_guard_database()` |
| The password is read only from `.env`, never printed, never written to a dump, never passed on a command line | `db.py`, `scripts/backup.py` |
| The test suite refuses to run against `money_tracker` | `tests/conftest.py` |

## Backups

### Automatic backup on every startup

Every time the app starts it writes a timestamped dump to `backups/` (skipped
while testing). The last 14 dumps are kept, older ones are deleted.

### Manual backup

```powershell
python scripts\backup.py
# Backup written: ...\backups\money_tracker_20261022_013000.sql (8.1 KB)
```

Useful flags: `--db money_tracker_test` (back up another database),
`--keep 30` (keep more dumps), `--dir <folder>` (write somewhere else).

### Daily backup (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_backup.ps1
```

This registers a task called **MoneyTracker Daily Backup** that runs
`scripts\run_backup.bat` every day at 02:00 and appends a line to
`backups\scheduler.log`. Check it, run it, change the time or remove it:

```powershell
Get-ScheduledTask -TaskName "MoneyTracker Daily Backup" | Get-ScheduledTaskInfo
Start-ScheduledTask -TaskName "MoneyTracker Daily Backup"
powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_backup.ps1 -Time 03:30
powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_backup.ps1 -Remove
```

The installer first tries to register the task with the `S4U` logon type (it
then also runs while you are logged out); without administrator rights it
falls back to a plain user-level task that runs as you.

### Download a backup from the app

The transactions page has **Download backup / CSV / JSON** buttons:

| URL | What you get |
| --- | --- |
| `/export/backup.sql` | a full `mysqldump` of the current database |
| `/export/transactions.csv` | every transaction as CSV |
| `/export/transactions.json` | every transaction as JSON |

## Restoring a backup

Always restore into a **copy** first and compare the row counts.

```powershell
# 1. see what is inside the dump - changes nothing
python scripts\restore.py backups\money_tracker_20261022_013000.sql --target money_tracker_test --dry-run

# 2. restore it into a scratch database and compare
python scripts\restore.py backups\money_tracker_20261022_013000.sql --target money_tracker_test

# 3. only then, if you really mean it, restore over the real database
python scripts\restore.py backups\money_tracker_20261022_013000.sql --target money_tracker --force
```

- The script rewrites the dump's `CREATE DATABASE` / `USE` statements so it
  always lands in the target you asked for.
- Restoring over `money_tracker` is **refused** unless you pass `--force`,
  because a restore replaces the tables it contains and would lose anything
  saved since the backup was taken.
- The row counts of every restored table are printed afterwards, so you can
  check them against what you expected.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests -v
```

The suite runs against the separate `money_tracker_test` database and refuses
to start if `DB_NAME` points at `money_tracker`. It covers the safety rules
above (startup never deletes, deletes are reversible, writes roll back) and
the routes (add / edit / delete / undo, exports, `/health`).

## Import existing data (one-time)

If you still have data in the old SQLite file (`money_tracker.db`) or JSON
exports in `data/`, import it into MySQL:

```powershell
python scripts\import_to_mysql.py
```

This prints a **preview** and changes nothing. Run it with `--yes` to
actually import:

```powershell
python scripts\import_to_mysql.py --yes
```

The import is manual only — it is never triggered by the app, and it refuses
to run if `MONEY_TRACKER_AUTO_IMPORT` is set. Safe to re-run: rows already
present are skipped, never duplicated.

## Manual test checklist

Run this against the **real** database only after a fresh backup exists.

1. `python app.py` prints `MySQL connected to money_tracker at localhost:3306`
   and does not exit.
2. `GET /health` returns `{"status": "ok", "database": "money_tracker"}`.
3. Open `/`, click **Add transaction**, save — a success toast appears and you
   land back on the same page (Post/Redirect/Get).
4. Immediately add 4 more transactions in a row — every one works, no 405.
5. Refresh `/transactions` — all 5 entries are listed, grouped by date.
6. Edit one transaction (pencil icon), save — the row shows the new values.
7. Delete one (trash icon), confirm — the row moves to **Recently deleted**,
   and the row is still in the table (`deleted_at` is set). Click **Undo** and
   it comes back.
8. Add a budget on `/budgets`, add a matching expense — spend/remaining update.
9. Click **Download backup** — a `.sql` file with your rows arrives.
10. In MySQL Workbench, verify the rows:
    ```sql
    USE money_tracker;
    SELECT id, txn_date, description, amount, deleted_at
      FROM transactions ORDER BY txn_date DESC, id DESC;
    ```
11. Stop MySQL, restart the app — it exits with a clear fatal error naming
    the connection problem (password masked). It does **not** fall back to a
    local SQLite file.
12. Restart the app with MySQL up — the startup report shows the row counts
    and nothing was deleted.
13. `python scripts\backup.py` — a new dump appears in `backups/`.
14. `python scripts\restore.py <that dump> --target money_tracker_test --dry-run`
    — it lists the tables and transaction rows and changes nothing.

## Troubleshooting

- **"Missing environment variable(s): DB_..."** — copy `.env.example` to
  `.env` next to `app.py` and fill in the values.
- **"Could not connect to MySQL ... (2003)"** — MySQL isn't running or the
  host/port is wrong; check the MySQL service and `DB_HOST`/`DB_PORT` in `.env`.
- **"Access denied for user 'root'@'localhost'"** — wrong `DB_USER` or
  `DB_PASSWORD` in `.env`.

## Project structure

```
app.py                     Flask app: routes, templates, validation, exports
db.py                      SQLAlchemy models + all data access (ORM only)
schema.sql                 MySQL 8 DDL (also created automatically at startup)
scripts/backup.py          Timestamped mysqldump backups, keeps the last 14
scripts/restore.py         Restore a dump (refuses the prod DB without --force)
scripts/run_backup.bat     Batch wrapper used by the Task Scheduler job
scripts/install_scheduled_backup.ps1  Installs/removes that scheduled task
scripts/import_to_mysql.py One-time SQLite/JSON -> MySQL import (manual only)
tests/                     pytest suite, runs against money_tracker_test only
backups/                   Dumps (git-ignored), keep the last 14
templates/                 Jinja2 templates
static/                    CSS + JS (theme, dialogs, filters, charts)
```

## Notes

- Money: `DECIMAL(12,2)` end to end; Python `Decimal`, never float.
- Dates: calendar dates in `DATE` columns; timestamps in UTC.
- All POST routes use Post/Redirect/Get; every form posts to an absolute
  path; edit/delete are POST-only.
- The `src/` folder is a legacy Express prototype, not needed for this app.
- `reset_now.py` is the script that deleted real data on 2026-10-21. It is
  **disabled** — every statement that can change data is commented out and
  the file now only prints a warning. Do not restore its old contents.
