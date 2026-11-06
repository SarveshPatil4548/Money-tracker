"""Create timestamped mysqldump backups of the Money Tracker database.

Usage:
    python scripts/backup.py                 # back up DB_NAME from .env
    python scripts/backup.py --db money_tracker_test
    python scripts/backup.py --keep 30

The MySQL password is read only from .env (or the environment) and is never
printed, never written to the dump and never passed on a command line.
"""

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE_DIR / ".env")

DEFAULT_BACKUP_DIR = BASE_DIR / "backups"
KEEP_LAST = 14

#: Windows and POSIX locations where the MySQL client tools usually live.
_TOOL_DIRS = [
    r"C:\Program Files\MySQL\MySQL Server 8.0\bin",
    r"C:\Program Files\MySQL\MySQL Server 8.4\bin",
    r"C:\Program Files\MySQL\MySQL Server 8.0",
    "/usr/local/mysql/bin",
    "/usr/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
]


def find_tool(name):
    """Locate a MySQL client executable (mysqldump, mysql, ...)."""
    exe = f"{name}.exe" if os.name == "nt" else name

    from shutil import which
    found = which(exe) or which(name)
    if found:
        return found

    for directory in _TOOL_DIRS:
        candidate = Path(directory) / exe
        if candidate.exists():
            return str(candidate)

    # last resort: any MySQL Server x.y install directory
    for pattern in (
        r"C:\Program Files\MySQL\MySQL Server *\bin",
        "/usr/local/mysql*/bin",
    ):
        for directory in glob.glob(pattern):
            candidate = Path(directory) / exe
            if candidate.exists():
                return str(candidate)
    return None


def read_config():
    """Connection settings from the environment (.env is loaded at import)."""
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": os.getenv("DB_PORT", "3306"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD") or "",
        "name": os.getenv("DB_NAME", "money_tracker"),
    }


def dump_to_bytes(db_name=None, config=None):
    """Run mysqldump and return the dump as bytes.

    The password is passed through the MYSQL_PWD environment variable so it
    never appears in the process command line.
    """
    config = config or read_config()
    db_name = db_name or config["name"]
    mysqldump = find_tool("mysqldump")
    if not mysqldump:
        raise RuntimeError(
            "mysqldump was not found. Install the MySQL Server client tools "
            "or add them to PATH."
        )

    cmd = [
        mysqldump,
        f"--host={config['host']}",
        f"--port={config['port']}",
        f"--user={config['user']}",
        "--single-transaction",
        "--quick",
        "--routines",
        "--triggers",
        "--events",
        "--set-gtid-purged=OFF",
        "--default-character-set=utf8mb4",
        "--databases",
        db_name,
    ]

    env = dict(os.environ)
    env["MYSQL_PWD"] = config["password"]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        if config["password"]:
            message = message.replace(config["password"], "***")
        raise RuntimeError(f"mysqldump failed: {message}")
    if not result.stdout.strip():
        raise RuntimeError("mysqldump produced an empty dump.")
    return result.stdout


def prune_old_backups(keep=KEEP_LAST, backup_dir=DEFAULT_BACKUP_DIR):
    """Keep only the newest `keep` dumps. Returns the removed paths."""
    backup_dir = Path(backup_dir)
    dumps = sorted(
        backup_dir.glob("*.sql"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for old in dumps[keep:]:
        old.unlink()
        removed.append(old)
    return removed


def backup_database(db_name=None, keep=KEEP_LAST, backup_dir=DEFAULT_BACKUP_DIR):
    """Write a timestamped .sql dump into backup_dir and prune old ones."""
    from datetime import datetime

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    config = read_config()
    db_name = db_name or config["name"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{db_name}_{stamp}.sql"

    data = dump_to_bytes(db_name=db_name, config=config)
    target.write_bytes(data)
    removed = prune_old_backups(keep=keep, backup_dir=backup_dir)
    return target, removed


def main():
    parser = argparse.ArgumentParser(description="Back up the Money Tracker database.")
    parser.add_argument("--db", default=None, help="database name (default: DB_NAME)")
    parser.add_argument("--keep", type=int, default=KEEP_LAST, help="how many dumps to keep")
    parser.add_argument("--dir", default=str(DEFAULT_BACKUP_DIR), help="backup directory")
    args = parser.parse_args()

    try:
        target, removed = backup_database(
            db_name=args.db, keep=args.keep, backup_dir=args.dir
        )
    except Exception as exc:
        print(f"BACKUP FAILED: {exc}")
        return 1

    size_kb = target.stat().st_size / 1024
    print(f"Backup written: {target} ({size_kb:.1f} KB)")
    for old in removed:
        print(f"  removed old backup: {old.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
