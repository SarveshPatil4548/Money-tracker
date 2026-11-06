"""Tests for the backup / restore / import scripts.

Everything here runs against money_tracker_test only. The production
database is never written to, and the restore script must refuse it.
"""

import os
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import backup as backup_tool  # noqa: E402
import restore as restore_tool  # noqa: E402
import import_to_mysql  # noqa: E402

import db as app_db  # noqa: E402


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------

def test_dump_contains_the_schema_and_the_rows(app):
    from helpers import cat_id, seed_categories

    seed_categories()
    app_db.add_transaction(cat_id("Groceries"), "40.00", "Potatoes", "2026-10-20")

    data = backup_tool.dump_to_bytes(db_name="money_tracker_test")
    text = data.decode("utf-8", "replace")
    assert "CREATE TABLE `transactions`" in text
    assert "Potatoes" in text
    assert "money_tracker_test" in text


def test_the_dump_never_contains_the_password(app):
    password = os.environ.get("DB_PASSWORD", "")
    if not password:
        pytest.skip("no password configured")
    data = backup_tool.dump_to_bytes(db_name="money_tracker_test")
    assert password.encode("utf-8") not in data


def test_backup_database_writes_a_timestamped_file(app, tmp_path):
    target, removed = backup_tool.backup_database(
        db_name="money_tracker_test", keep=14, backup_dir=tmp_path
    )
    assert target.exists()
    assert target.name.startswith("money_tracker_test_")
    assert target.name.endswith(".sql")
    assert target.stat().st_size > 0
    assert removed == []


def test_old_backups_are_pruned(app, tmp_path):
    for index in range(5):
        (tmp_path / f"dump_{index}.sql").write_text("x", encoding="utf-8")
    removed = backup_tool.prune_old_backups(keep=2, backup_dir=tmp_path)
    assert len(removed) == 3
    assert len(list(tmp_path.glob("*.sql"))) == 2


def test_the_password_is_never_passed_on_the_command_line(app, monkeypatch):
    """The password must travel via MYSQL_PWD, not in argv."""
    seen = {}

    class FakeResult:
        returncode = 0
        stdout = b"-- dump"
        stderr = b""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env", {})
        return FakeResult()

    monkeypatch.setattr(backup_tool.subprocess, "run", fake_run)
    backup_tool.dump_to_bytes(db_name="money_tracker_test")
    assert all("password" not in part.lower() for part in seen["cmd"])
    assert seen["env"].get("MYSQL_PWD") == os.environ.get("DB_PASSWORD")


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

def test_restore_rewrites_the_target_database():
    sql = (
        "CREATE DATABASE /*!32312 IF NOT EXISTS*/ `money_tracker`\n"
        "USE `money_tracker`;\n"
        "CREATE TABLE `transactions` (`id` int);\n"
    )
    rewritten = restore_tool._rewrite_target(sql, "money_tracker_test")
    assert "USE `money_tracker_test`;" in rewritten
    assert "`money_tracker_test`" in rewritten
    assert "USE `money_tracker`;" not in rewritten


def test_restore_refuses_the_production_database(app, tmp_path, capsys, monkeypatch):
    dump = tmp_path / "dump.sql"
    dump.write_text("CREATE TABLE `transactions` (`id` int);\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["restore.py", str(dump), "--target", "money_tracker"]
    )
    assert restore_tool.main() == 1
    assert "REFUSED" in capsys.readouterr().out


def test_restore_dry_run_changes_nothing(app, tmp_path, capsys, monkeypatch):
    dump = tmp_path / "dump.sql"
    dump.write_text(
        "CREATE TABLE `transactions` (`id` int);\nINSERT INTO `transactions` VALUES (1);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["restore.py", str(dump), "--target", "money_tracker_test", "--dry-run"],
    )
    assert restore_tool.main() == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "nothing was changed" in out


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def test_import_is_preview_only_without_yes(app, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["import_to_mysql.py"])
    monkeypatch.delenv("MONEY_TRACKER_AUTO_IMPORT", raising=False)
    assert import_to_mysql.main() == 0
    out = capsys.readouterr().out
    assert "PREVIEW ONLY" in out
    # nothing was written
    assert app_db.count_transactions() == 0


def test_import_refuses_to_run_automatically(monkeypatch):
    monkeypatch.setenv("MONEY_TRACKER_AUTO_IMPORT", "1")
    monkeypatch.setattr(sys, "argv", ["import_to_mysql.py", "--yes"])
    with pytest.raises(SystemExit):
        import_to_mysql.main()


def test_startup_backup_is_skipped_under_testing(app):
    from app import run_startup_backup

    assert run_startup_backup() is None
