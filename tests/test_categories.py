"""Category deletion: unused, reassign, archive, protection, undo (test DB only)."""

import sys
from datetime import date
from pathlib import Path

import db as app_db
from helpers import cat_id, seed_categories

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backup as backup_tool  # noqa: E402


def _csrf(client):
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token")
    if not token:
        client.get("/categories")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token")
    return token


def _post(client, url, payload):
    return client.post(
        url,
        json=payload,
        headers={"X-CSRF-Token": _csrf(client)},
    )


def _category(name):
    for category in app_db.list_categories(include_archived=True, include_deleted=True):
        if category["name"] == name:
            return category
    raise AssertionError(f"category '{name}' not found")


def _make(name, kind="expense"):
    app_db.add_category(name, kind)
    return _category(name)


def test_delete_an_unused_category_and_undo(app, client):
    seed_categories()
    pets = _make("Pets")

    response = _post(client, f"/categories/{pets['id']}/delete", {"action": "delete"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert app_db.get_category(pets["id"]) is None
    assert _category("Pets")["deleted_at"] != ""

    page = client.get("/categories").get_data(as_text=True)
    assert "Recently deleted" in page
    assert "Pets" in page

    response = _post(client, f"/categories/{pets['id']}/undo", {})
    assert response.status_code == 200
    assert app_db.get_category(pets["id"])["name"] == "Pets"
    assert _category("Pets")["deleted_at"] == ""


def test_delete_a_used_category_with_reassign_keeps_totals(app, client, monkeypatch):
    seed_categories()
    food = _make("Food")
    groceries = cat_id("Groceries")

    app_db.add_transaction(food["id"], "10.00", "lunch", "2026-10-01")
    app_db.add_transaction(food["id"], "15.00", "dinner", "2026-10-02")
    app_db.upsert_budget(food["id"], "100.00")
    app_db.upsert_budget(groceries, "200.00")

    before_count = app_db.count_transactions()
    before_expense = app_db.sum_by_type("expense")

    monkeypatch.setattr(app_db, "_backup_before_reassign", lambda: "backup.sql")
    response = _post(
        client,
        f"/categories/{food['id']}/delete",
        {"action": "reassign", "target_category_id": str(groceries)},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["moved_transactions"] == 2

    assert app_db.count_transactions() == before_count
    assert app_db.sum_by_type("expense") == before_expense == app_db.Decimal("25.00")
    assert {t["category"] for t in app_db.list_transactions()} == {"Groceries"}
    assert app_db.get_category(food["id"]) is None

    budgets = {b["name"]: b["monthly_limit"] for b in app_db.list_budgets()}
    assert budgets == {"Groceries": app_db.Decimal("300.00")}

    # Undo moves only the rows that this deletion moved, and restores budgets.
    response = _post(client, f"/categories/{food['id']}/undo", {})
    assert response.status_code == 200
    assert app_db.get_category(food["id"])["name"] == "Food"
    assert {t["category"] for t in app_db.list_transactions()} == {"Food"}
    budgets = {b["name"]: b["monthly_limit"] for b in app_db.list_budgets()}
    assert budgets == {
        "Food": app_db.Decimal("100.00"),
        "Groceries": app_db.Decimal("200.00"),
    }


def test_reassign_rejects_a_different_type(app, client, monkeypatch):
    seed_categories()
    food = _make("Food")
    app_db.add_transaction(food["id"], "10.00", "lunch", "2026-10-01")
    monkeypatch.setattr(app_db, "_backup_before_reassign", lambda: "backup.sql")

    response = _post(
        client,
        f"/categories/{food['id']}/delete",
        {"action": "reassign", "target_category_id": str(cat_id("Salary"))},
    )
    assert response.status_code == 409
    assert "income" in response.get_json()["error"]
    assert app_db.get_category(food["id"]) is not None
    assert app_db.list_transactions()[0]["category"] == "Food"


def test_direct_delete_of_a_used_category_is_refused(app, client):
    seed_categories()
    food = _make("Food")
    app_db.add_transaction(food["id"], "10.00", "lunch", "2026-10-01")

    response = _post(client, f"/categories/{food['id']}/delete", {"action": "delete"})
    assert response.status_code == 409
    assert app_db.get_category(food["id"]) is not None


def test_archive_keeps_old_transactions_and_charts(app, client):
    seed_categories()
    food = _make("Food")
    app_db.add_transaction(food["id"], "42.00", "lunch", "2026-10-01")
    app_db.upsert_budget(food["id"], "100.00")

    response = _post(client, f"/categories/{food['id']}/delete", {"action": "archive"})
    assert response.status_code == 200

    assert app_db.get_category(food["id"]) is None
    assert _category("Food")["archived_at"] != ""
    assert app_db.list_transactions()[0]["category"] == "Food"
    assert app_db.list_transactions()[0]["amount"] == app_db.Decimal("42.00")
    spending = app_db.spending_by_category("2026-10-01")
    assert spending == [{"name": "Food", "total": 42.0}]
    # Archived budgets are hidden from the active list but not lost.
    assert all(b["name"] != "Food" for b in app_db.list_budgets())

    response = _post(client, f"/categories/{food['id']}/restore", {})
    assert response.status_code == 200
    assert app_db.get_category(food["id"])["name"] == "Food"
    assert any(b["name"] == "Food" for b in app_db.list_budgets())


def test_last_category_of_a_type_cannot_be_deleted(app, client):
    only_income = _make("Only Income", "income")

    response = _post(
        client, f"/categories/{only_income['id']}/delete", {"action": "delete"}
    )
    assert response.status_code == 409
    assert "last income category" in response.get_json()["error"]
    assert app_db.get_category(only_income["id"]) is not None


def test_default_fallback_category_cannot_be_deleted(app, client):
    seed_categories()
    for name in ("Other Income", "Other Expense", "Uncategorised"):
        if name == "Uncategorised":
            category = _make(name, "expense")
        else:
            category = _category(name)
        response = _post(
            client, f"/categories/{category['id']}/delete", {"action": "archive"}
        )
        assert response.status_code == 409
        assert "fallback" in response.get_json()["error"]
        assert app_db.get_category(category["id"]) is not None


def test_reassign_rolls_back_completely_on_failure(app, client, monkeypatch):
    seed_categories()
    food = _make("Food")
    groceries = cat_id("Groceries")
    app_db.add_transaction(food["id"], "10.00", "lunch", "2026-10-01")
    app_db.upsert_budget(food["id"], "100.00")

    monkeypatch.setattr(app_db, "_backup_before_reassign", lambda: "backup.sql")

    def boom():
        app_db.db.session.rollback()
        raise app_db.SaveError("database on fire")

    monkeypatch.setattr(app_db, "_commit", boom)
    response = _post(
        client,
        f"/categories/{food['id']}/delete",
        {"action": "reassign", "target_category_id": str(groceries)},
    )
    assert response.status_code == 500
    assert response.get_json()["ok"] is False

    assert app_db.get_category(food["id"])["name"] == "Food"
    assert app_db.list_transactions()[0]["category"] == "Food"
    assert {b["name"] for b in app_db.list_budgets()} == {"Food"}


def test_delete_requires_a_valid_csrf_token(app, client):
    seed_categories()
    pets = _make("Pets")

    response = client.post(f"/categories/{pets['id']}/delete", json={"action": "delete"})
    assert response.status_code == 403
    assert response.get_json()["code"] == "csrf_expired"
    assert app_db.get_category(pets["id"]) is not None


def test_csrf_endpoint_issues_a_usable_token(app, client):
    seed_categories()
    pets = _make("Pets")

    token = client.get("/csrf-token").get_json()["csrf_token"]
    assert token

    response = client.post(
        f"/categories/{pets['id']}/delete",
        json={"action": "delete"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_session_secret_and_cookie_lifetime_are_stable(app):
    assert len(app.secret_key) >= 32
    assert app.secret_key != "dev"
    assert app.config["PERMANENT_SESSION_LIFETIME"].days == 30
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_usage_contains_absolute_urls_and_counts(app, client):
    seed_categories()
    food = _make("Food")
    app_db.add_transaction(food["id"], "10.00", "lunch", "2026-10-01")
    app_db.upsert_budget(food["id"], "100.00")

    response = client.get(f"/categories/{food['id']}/usage")
    assert response.status_code == 200
    usage = response.get_json()["usage"]
    assert usage["transaction_count"] == 1
    assert usage["budget_count"] == 1
    assert usage["delete_url"].startswith("http://localhost/categories/")
    assert usage["undo_url"].startswith("http://localhost/categories/")
    assert usage["edit_url"].startswith("http://localhost/categories/")
    assert usage["restore_url"].startswith("http://localhost/categories/")
    assert usage["candidates"]


def test_reassign_writes_a_backup_first(app, client, monkeypatch):
    seed_categories()
    food = _make("Food")
    groceries = cat_id("Groceries")
    app_db.add_transaction(food["id"], "10.00", "lunch", "2026-10-01")
    calls = []

    def fake_backup():
        calls.append("backup")
        return Path("test-backup.sql"), []

    monkeypatch.setattr(backup_tool, "backup_database", fake_backup)
    response = _post(
        client,
        f"/categories/{food['id']}/delete",
        {"action": "reassign", "target_category_id": str(groceries)},
    )
    assert response.status_code == 200
    assert calls == ["backup"]


def test_a_failed_backup_aborts_the_reassign(app, client, monkeypatch):
    seed_categories()
    food = _make("Food")
    groceries = cat_id("Groceries")
    app_db.add_transaction(food["id"], "10.00", "lunch", "2026-10-01")

    def failed_backup():
        raise RuntimeError("no mysqldump today")

    monkeypatch.setattr(backup_tool, "backup_database", failed_backup)
    response = _post(
        client,
        f"/categories/{food['id']}/delete",
        {"action": "reassign", "target_category_id": str(groceries)},
    )
    assert response.status_code == 500
    assert "backup" in response.get_json()["error"].lower()
    assert app_db.get_category(food["id"]) is not None
    assert app_db.list_transactions()[0]["category"] == "Food"


# ---------------------------------------------------------------------------
# renaming
# ---------------------------------------------------------------------------

def test_rename_an_unused_category_keeps_id_and_type(app, client):
    seed_categories()
    pets = _make("Pets")

    response = _post(
        client, f"/categories/{pets['id']}/edit", {"name": "  Companions  "}
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["changed"] is True
    assert payload["category"]["id"] == pets["id"]
    assert payload["category"]["type"] == pets["type"]
    assert payload["category"]["name"] == "Companions"
    assert payload["edit_url"].startswith("http://localhost/categories/")

    page = client.get("/categories").get_data(as_text=True)
    assert "Companions" in page
    assert 'data-category-name="Pets"' not in page
    assert f'data-edit-url="http://localhost/categories/{pets["id"]}/edit"' in page


def test_rename_a_used_category_keeps_transactions_budgets_and_totals(app, client):
    seed_categories()
    food = _make("Food")
    groceries = cat_id("Groceries")
    today = date.today().isoformat()
    month_start = date.today().replace(day=1).isoformat()
    app_db.add_transaction(food["id"], "10.00", "lunch", today)
    app_db.add_transaction(food["id"], "15.00", "dinner", today)
    app_db.upsert_budget(food["id"], "100.00")
    app_db.upsert_budget(groceries, "200.00")

    txn_ids = sorted(t["id"] for t in app_db.list_transactions())
    before_count = app_db.count_transactions()
    before_expense = app_db.sum_by_type("expense")
    before_budget = {
        b["category_id"]: b["monthly_limit"] for b in app_db.list_budgets()
    }

    response = _post(client, f"/categories/{food['id']}/edit", {"name": "Dining"})
    assert response.status_code == 200

    after = app_db.get_category(food["id"])
    assert after["id"] == food["id"]
    assert after["type"] == food["type"]
    assert after["name"] == "Dining"

    assert app_db.count_transactions() == before_count
    assert app_db.sum_by_type("expense") == before_expense
    assert sorted(t["id"] for t in app_db.list_transactions()) == txn_ids
    assert {t["category"] for t in app_db.list_transactions()} == {"Dining"}
    assert all(
        t["category_id"] == food["id"] for t in app_db.list_transactions()
    )
    assert app_db.db.session.execute(
        app_db.text("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL")
    ).scalar() == 0
    assert app_db.spending_by_category(month_start)[0]["name"] == "Dining"

    after_budget = {
        b["category_id"]: b["monthly_limit"] for b in app_db.list_budgets()
    }
    assert after_budget == before_budget
    assert any(b["name"] == "Dining" for b in app_db.list_budgets())

    dashboard = client.get("/").get_data(as_text=True)
    assert "Dining" in dashboard
    budgets = client.get("/budgets").get_data(as_text=True)
    assert "Dining" in budgets


def test_rename_rejects_empty_and_too_long_names(app, client):
    seed_categories()
    pets = _make("Pets")

    for bad in ("", "   ", "x" * 41):
        response = _post(client, f"/categories/{pets['id']}/edit", {"name": bad})
        assert response.status_code == 400
        assert response.get_json()["field"] == "name"
        assert app_db.get_category(pets["id"])["name"] == "Pets"


def test_rename_rejects_case_insensitive_duplicates(app, client):
    seed_categories()
    food = _make("Food")
    snacks = _make("Snacks")

    response = _post(client, f"/categories/{snacks['id']}/edit", {"name": "fOoD"})
    assert response.status_code == 409
    assert "already exists" in response.get_json()["error"]
    assert app_db.get_category(snacks["id"])["name"] == "Snacks"
    assert app_db.get_category(food["id"])["name"] == "Food"


def test_rename_rejects_the_same_name_in_the_other_type(app, client):
    seed_categories()
    bonus = _make("Bonus", "income")
    gifts = _make("Gifts", "expense")

    response = _post(client, f"/categories/{gifts['id']}/edit", {"name": "bonus"})
    assert response.status_code == 409
    assert app_db.get_category(gifts["id"])["name"] == "Gifts"
    assert app_db.get_category(bonus["id"])["name"] == "Bonus"


def test_unchanged_name_closes_without_any_write(app, client, monkeypatch):
    seed_categories()
    pets = _make("Pets")

    def should_not_write():
        raise AssertionError("an unchanged name must not write to the database")

    monkeypatch.setattr(app_db, "_commit", should_not_write)
    response = _post(client, f"/categories/{pets['id']}/edit", {"name": "  Pets  "})
    assert response.status_code == 200
    assert response.get_json()["changed"] is False
    assert app_db.get_category(pets["id"])["name"] == "Pets"


def test_fallback_categories_are_locked_against_renaming(app, client):
    seed_categories()
    other = _category("Other Expense")

    response = _post(client, f"/categories/{other['id']}/edit", {"name": "Misc"})
    assert response.status_code == 409
    assert "fallback" in response.get_json()["error"]
    assert app_db.get_category(other["id"])["name"] == "Other Expense"

    page = client.get("/categories").get_data(as_text=True)
    assert "is a locked fallback category" in page


def test_rename_requires_a_valid_csrf_token(app, client):
    seed_categories()
    pets = _make("Pets")

    response = client.post(f"/categories/{pets['id']}/edit", json={"name": "Birds"})
    assert response.status_code == 403
    assert app_db.get_category(pets["id"])["name"] == "Pets"


def test_rename_rolls_back_and_reports_a_save_failure(app, client, monkeypatch):
    seed_categories()
    pets = _make("Pets")

    def boom():
        raise app_db.SaveError("database on fire")

    monkeypatch.setattr(app_db, "_commit", boom)
    response = _post(client, f"/categories/{pets['id']}/edit", {"name": "Birds"})
    assert response.status_code == 500
    assert response.get_json()["ok"] is False
    assert "Nothing was changed" in response.get_json()["error"]

    app_db.db.session.rollback()
    assert app_db.get_category(pets["id"])["name"] == "Pets"


def test_edit_add_edit_in_a_row_works(app, client):
    seed_categories()
    pets = _make("Pets")

    first = _post(client, f"/categories/{pets['id']}/edit", {"name": "Companions"})
    assert first.status_code == 200

    added = client.post(
        "/categories/add",
        data={"name": "Birds", "type": "expense"},
        follow_redirects=True,
    )
    assert added.status_code == 200
    birds = _category("Birds")

    second = _post(client, f"/categories/{birds['id']}/edit", {"name": "Aviary"})
    assert second.status_code == 200
    assert second.get_json()["category"]["name"] == "Aviary"

    assert app_db.get_category(pets["id"])["name"] == "Companions"
    assert app_db.get_category(birds["id"])["name"] == "Aviary"
