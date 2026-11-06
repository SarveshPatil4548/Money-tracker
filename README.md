# MoneyWise — Personal Finance Tracker

A Flask-based personal finance application for tracking income, expenses, budgets, and account transfers with MySQL.

## Features

- Dashboard with monthly income, expenses, balance, spending trends, and recent activity
- Add, edit, soft-delete, and restore transactions
- Monthly budgets by category
- Income and expense categories
- Account-to-account transfers
- CSV, JSON, and SQL backup exports
- Automatic database backups
- Database health-check endpoint
- Tests for routes, budgets, categories, and data-safety behavior

## Tech stack

- Python 3.13
- Flask and Flask-SQLAlchemy
- SQLAlchemy
- MySQL 8
- HTML, CSS, and JavaScript

The `src/` directory contains a legacy Express prototype; the primary application is the Flask app in `app.py`.

## Requirements

- Python 3.13
- MySQL 8.0
- Node.js is only needed if you want to run the legacy prototype's tests/tools.

## Setup

1. Clone this repository:

   ```bash
   git clone https://github.com/SarveshPatil4548/Money-tracker.git
   cd Money-tracker
   ```

2. Create and activate a virtual environment:

   **Windows PowerShell**
   ```powershell
   py -3.13 -m venv .venv
   .venv\\Scripts\\Activate.ps1
   ```

   **macOS / Linux**
   ```bash
   python3.13 -m venv .venv
   source .venv/bin/activate
   ```

3. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Create a MySQL database:

   ```sql
   CREATE DATABASE money_tracker;
   ```

5. Copy the environment template and update your local database settings:

   **Windows PowerShell**
   ```powershell
   Copy-Item .env.example .env
   ```

   **macOS / Linux**
   ```bash
   cp .env.example .env
   ```

   Edit `.env` with your local MySQL host, username, password, and database name. Never commit your real `.env` file or credentials.

6. Start the application:

   ```bash
   python app.py
   ```

7. Open [http://localhost:5000](http://localhost:5000).

## Tests

The Python test suite uses a separate test database. Configure the test environment as required by `tests/conftest.py`, then run:

```bash
python -m pytest tests -v
```

Do not point the test suite at your real `money_tracker` database.

## Security notes

- Keep `.env`, database dumps, backups, and personal financial data out of Git.
- Use `.env.example` only as a template; put real secrets in your local `.env`.
- Do not upload real transaction exports or database backups to this public repository.

## License

No license has been selected yet. Unless a license is added, standard copyright applies and others do not automatically receive permission to reuse this code.
