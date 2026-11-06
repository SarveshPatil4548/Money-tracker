"""Restore a mysqldump backup into a MySQL database.

Usage:
    python scripts/restore.py backups/money_tracker_20261022_003000.sql --target money_tracker_test
    python scripts/restore.py <file> --target money_tracker --force     # dangerous
    python scripts/restore.py <file> --target money_tracker_test --dry-run

Safety rules:
  * The production database ('money_tracker') is refused unless --force is
    given, because restoring a dump REPLACES the tables it contains.
  * --dry-run shows what would happen without touching anything.
  * The MySQL password is read only from .env and is never printed.

A mysqldump taken with '--databases' contains CREATE DATABASE and USE
statements. Those are rewritten to point at the target database, so the same
backup file can be restored into a scratch database safely.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE_DIR / ".env")

from backup import find_tool, read_config  # noqa: E402
from db import PRODUCTION_DB_NAME  # noqa: E402


def _rewrite_target(sql_text, target_db):
    """Point the dump's CREATE DATABASE / USE statements at the target."""
    text = re.sub(
        r"CREATE DATABASE /\*!\d+ IF NOT EXISTS\*/ `[^`]+`",
        f"CREATE DATABASE /*!32312 IF NOT EXISTS*/ `{target_db}`",
        sql_text,
    )
    text = re.sub(
        r"^USE `[^`]+`;$",
        f"USE `{target_db}`;",
        text,
        flags=re.MULTILINE,
    )
    return text


def summarise(sql_text):
    """(tables, transaction rows) mentioned in a dump."""
    tables = set(re.findall(r"CREATE TABLE `([^`]+)`", sql_text))
    rows = 0
    for match in re.finditer(r"INSERT INTO `transactions` VALUES (.+?);\n", sql_text, re.S):
        # count the value tuples in this INSERT
        rows += match.group(1).count("),(") + 1
    return sorted(tables), rows


def main():
    parser = argparse.ArgumentParser(description="Restore a mysqldump backup.")
    parser.add_argument("backup", help="path to the .sql dump")
    parser.add_argument("--target", required=True, help="database to restore into")
    parser.add_argument(
        "--force",
        action="store_true",
        help=f"required to restore over the production database '{PRODUCTION_DB_NAME}'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would happen, change nothing",
    )
    args = parser.parse_args()

    dump_path = Path(args.backup)
    if not dump_path.exists():
        print(f"ERROR: backup file not found: {dump_path}")
        return 1

    target = args.target
    if target == PRODUCTION_DB_NAME and not args.force:
        print(
            f"REFUSED: restoring over the production database '{PRODUCTION_DB_NAME}' "
            f"replaces its tables and loses anything saved since the backup.\n"
            f"         Restore into a copy instead, or pass --force if you are sure."
        )
        return 1

    sql_text = dump_path.read_text(encoding="utf-8", errors="replace")
    tables, txn_rows = summarise(sql_text)
    print(f"Backup file : {dump_path}")
    print(f"Target      : {target}")
    print(f"Tables      : {', '.join(tables) or '(none found)'}")
    print(f"Transaction rows in dump : {txn_rows}")

    if args.dry_run:
        print("\nDRY RUN - nothing was changed.")
        return 0

    mysql = find_tool("mysql")
    if not mysql:
        print("ERROR: the mysql client was not found on this machine.")
        return 1

    config = read_config()
    env = dict(os.environ)
    env["MYSQL_PWD"] = config["password"]

    payload = _rewrite_target(sql_text, target)
    result = subprocess.run(
        [
            mysql,
            f"--host={config['host']}",
            f"--port={config['port']}",
            f"--user={config['user']}",
            "--default-character-set=utf8mb4",
        ],
        input=payload.encode("utf-8", errors="replace"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        if config["password"]:
            message = message.replace(config["password"], "***")
        print(f"RESTORE FAILED: {message}")
        return 1

    print(f"\nRestored into '{target}'.")
    print("Row counts after restore:")
    for table in tables:
        check = subprocess.run(
            [
                mysql,
                f"--host={config['host']}",
                f"--port={config['port']}",
                f"--user={config['user']}",
                "--batch",
                "--skip-column-names",
                "-e",
                f"SELECT COUNT(*) FROM `{target}`.`{table}`;",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        if check.returncode == 0:
            print(f"  {table:24} {check.stdout.decode('utf-8', 'replace').strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
