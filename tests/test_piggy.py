"""Piggy bank: models, balances, validation, soft delete and isolation."""

import pytest

import db as app_db
from helpers import cat_id, seed_categories


def _balance():
    return app_db.piggy_balance()


def test_default_bank_is_created_once(app):
    bank = app_db.ensure_piggy_bank()
    again = app_db.ensure_piggy_bank()
    assert bank.id == again.id
    assert str(bank.daily_amount) == "50.00"
    assert bank.contributor_me == "Me"
    assert bank.contributor_her == "Her"
    assert app_db.get_piggy_bank()["id"] == str(bank.id)


def test_deposits_and_withdrawals_update_balance(app):
    app_db.add_piggy_entry("me", "deposit", "50.00", "2026-10-01")
    app_db.add_piggy_entry("her", "deposit", "50.00", "2026-10-01")
    app_db.add_piggy_entry("me", "withdrawal", "30.00", "2026-10-02")

    assert _balance() == app_db.Decimal("70.00")
    entries = app_db.list_piggy_entries()
    assert len(entries) == 3
    assert all(e["contributor_name"] in ("Me", "Her") for e in entries)


def test_withdrawal_cannot_go_below_zero(app):
    app_db.add_piggy_entry("me", "deposit", "40.00", "2026-10-01")

    with pytest.raises(app_db.PiggyBalanceError):
        app_db.add_piggy_entry("her", "withdrawal", "60.00", "2026-10-02")
    assert _balance() == app_db.Decimal("40.00")


def test_backdated_withdrawal_cannot_make_a_later_balance_negative(app):
    app_db.add_piggy_entry("me", "deposit", "100.00", "2026-10-01")
    app_db.add_piggy_entry("me", "withdrawal", "90.00", "2026-10-05")
    app_db.add_piggy_entry("me", "deposit", "70.00", "2026-10-10")

    with pytest.raises(app_db.PiggyBalanceError):
        app_db.add_piggy_entry("me", "withdrawal", "50.00", "2026-10-02")


def test_duplicate_daily_deposit_warns_then_can_be_confirmed(app):
    app_db.add_piggy_entry("me", "deposit", "50.00", "2026-10-01")

    with pytest.raises(app_db.DuplicatePiggyDepositError):
        app_db.add_piggy_entry("me", "deposit", "50.00", "2026-10-01")

    app_db.add_piggy_entry(
        "me", "deposit", "50.00", "2026-10-01", allow_duplicate=True
    )
    app_db.add_piggy_entry("her", "deposit", "50.00", "2026-10-01")
    assert _balance() == app_db.Decimal("150.00")


def test_soft_delete_and_undo(app):
    entry = app_db.add_piggy_entry("me", "deposit", "50.00", "2026-10-01")

    assert app_db.delete_piggy_entry(entry["id"]) is True
    assert _balance() == app_db.Decimal("0.00")
    assert app_db.list_deleted_piggy_entries()[0]["id"] == entry["id"]

    assert app_db.undo_delete_piggy_entry(entry["id"]) is True
    assert _balance() == app_db.Decimal("50.00")


def test_editing_a_withdrawal_is_validated(app):
    app_db.add_piggy_entry("me", "deposit", "100.00", "2026-10-01")
    entry = app_db.add_piggy_entry("me", "withdrawal", "60.00", "2026-10-02")

    updated = app_db.update_piggy_entry(
        entry["id"], amount="80.00", note="updated"
    )
    assert updated["amount"] == "80.00"
    assert updated["note"] == "updated"

    with pytest.raises(app_db.PiggyBalanceError):
        app_db.update_piggy_entry(entry["id"], amount="120.00")


def test_settings_and_contributor_names(app):
    app_db.update_piggy_settings(
        name="Vacation Pot",
        daily_amount="100.00",
        contributor_me="Alex",
        contributor_her="Sam",
    )
    bank = app_db.get_piggy_bank()
    assert bank["name"] == "Vacation Pot"
    assert bank["daily_amount"] == "100.00"
    assert bank["contributor_me"] == "Alex"

    entry = app_db.add_piggy_entry("her", "deposit", "10.00", "2026-10-01")
    assert entry["contributor_name"] == "Sam"


def test_duplicate_contributor_names_rejected(app):
    with pytest.raises(app_db.PiggyValidationError):
        app_db.update_piggy_settings(contributor_me="Same", contributor_her="same")


def test_piggy_does_not_change_dashboard_totals(app):
    seed_categories()
    groceries = cat_id("Groceries")
    app_db.add_transaction(groceries, "400.00", "shop", "2026-10-01")

    before_income = app_db.sum_by_type("income")
    before_expense = app_db.sum_by_type("expense")

    app_db.add_piggy_entry("me", "deposit", "500.00", "2026-10-01")
    app_db.add_piggy_entry("her", "deposit", "500.00", "2026-10-01")
    app_db.add_piggy_entry("me", "withdrawal", "100.00", "2026-10-02")

    assert app_db.sum_by_type("income") == before_income
    assert app_db.sum_by_type("expense") == before_expense
    assert _balance() == app_db.Decimal("900.00")


def test_summary_and_calendar_for_month(app):
    app_db.add_piggy_entry("me", "deposit", "50.00", "2026-09-30")
    app_db.add_piggy_entry("me", "deposit", "50.00", "2026-10-01")
    app_db.add_piggy_entry("her", "deposit", "100.00", "2026-10-03")
    app_db.add_piggy_entry("her", "withdrawal", "40.00", "2026-10-04")

    summary = app_db.piggy_summary("2026-10")
    assert summary["opening_balance"] == "50.00"
    assert summary["month_deposits"] == "150.00"
    assert summary["month_withdrawals"] == "40.00"
    assert summary["closing_balance"] == "160.00"
    assert len(summary["days"]) == 31
    assert sum(1 for week in summary["weeks"] for day in week if day) == 31
    day = next(d for d in summary["days"] if d["date"] == "2026-10-04")
    assert day["withdrawals"] == "40.00"
    assert day["entry_count"] == 1
