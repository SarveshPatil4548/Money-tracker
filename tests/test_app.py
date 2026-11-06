"""Route-level tests for the Money Tracker app (run against money_tracker_test)."""

import json

import db as app_db
from helpers import cat_id, seed_categories


def _add(client, category_name, amount, description, txn_date):
    return client.post(
        "/transactions/add",
        data={
            "category_id": str(cat_id(category_name)),
            "amount": amount,
            "txn_date": txn_date,
            "description": description,
            "type": "expense",
        },
        follow_redirects=True,
    )


def test_add_transaction_through_the_form(app, client):
    seed_categories()
    response = _add(client, "Groceries", "40.00", "Potatoes", "2026-10-20")
    assert response.status_code == 200
    assert b"Potatoes" in response.data
    assert app_db.count_transactions() == 1


def test_add_transaction_rejects_a_bad_amount(app, client):
    seed_categories()
    response = _add(client, "Groceries", "-5", "bad", "2026-10-20")
    assert b"greater than 0" in response.data
    assert app_db.count_transactions() == 0


def test_edit_transaction(app, client):
    seed_categories()
    _add(client, "Groceries", "40.00", "Potatoes", "2026-10-20")
    txn_id = app_db.list_transactions()[0]["id"]

    response = client.post(
        f"/transactions/{txn_id}/edit",
        data={
            "category_id": str(cat_id("Groceries")),
            "amount": "45.50",
            "txn_date": "2026-10-21",
            "description": "Potatoes and onions",
            "type": "expense",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    row = app_db.list_transactions()[0]
    assert row["description"] == "Potatoes and onions"
    assert row["amount"] == app_db.Decimal("45.50")
    assert row["txn_date"] == "2026-10-21"


def test_delete_is_soft_and_undo_brings_it_back(app, client):
    seed_categories()
    _add(client, "Groceries", "40.00", "Potatoes", "2026-10-20")
    txn_id = app_db.list_transactions()[0]["id"]

    response = client.post(
        f"/transactions/{txn_id}/delete", data={}, follow_redirects=True
    )
    assert response.status_code == 200
    assert b"Recently deleted" in response.data
    assert b"Undo" in response.data
    # still physically present
    assert app_db.db.session.execute(
        app_db.text("SELECT COUNT(*) FROM transactions")
    ).scalar() == 1
    assert app_db.count_transactions() == 0

    response = client.post(
        f"/transactions/{txn_id}/undo", data={}, follow_redirects=True
    )
    assert response.status_code == 200
    assert app_db.count_transactions() == 1
    assert app_db.list_transactions()[0]["description"] == "Potatoes"


def test_delete_requires_a_post(app, client):
    seed_categories()
    _add(client, "Groceries", "40.00", "Potatoes", "2026-10-20")
    txn_id = app_db.list_transactions()[0]["id"]

    response = client.get(f"/transactions/{txn_id}/delete")
    assert response.status_code == 405
    assert app_db.count_transactions() == 1


def test_the_delete_button_asks_for_confirmation(app, client):
    seed_categories()
    _add(client, "Groceries", "40.00", "Potatoes", "2026-10-20")
    page = client.get("/transactions").get_data(as_text=True)
    # the row's delete button points at the confirm dialog
    assert "data-delete-url" in page
    # and the dialog itself is rendered
    assert 'id="delete-dialog"' in page
    assert "undo" in page.lower()


def test_edit_button_carries_the_row_data(app, client):
    seed_categories()
    _add(client, "Groceries", "40.00", 'Potatoes "red" & onions', "2026-10-20")
    page = client.get("/transactions").get_data(as_text=True)
    # Each row exposes everything the edit form needs as plain data attributes.
    assert 'data-category-id="' in page
    assert 'data-note="' in page
    assert 'data-edit-url="/transactions/' in page
    # The old JSON-in-a-double-quoted-attribute markup broke the edit button.
    assert "data-edit-json" not in page


def test_add_edit_add_edit_round_trip(app, client):
    seed_categories()
    _add(client, "Groceries", "10.00", "first", "2026-10-20")
    first_id = app_db.list_transactions()[0]["id"]

    client.post(
        f"/transactions/{first_id}/edit",
        data={
            "category_id": str(cat_id("Groceries")),
            "amount": "11.00",
            "txn_date": "2026-10-21",
            "description": "first edited",
            "type": "expense",
        },
        follow_redirects=True,
    )
    _add(client, "Salary", "500.00", "second", "2026-10-22")
    second_id = next(
        t["id"] for t in app_db.list_transactions() if t["description"] == "second"
    )
    client.post(
        f"/transactions/{second_id}/edit",
        data={
            "category_id": str(cat_id("Salary")),
            "amount": "550.00",
            "txn_date": "2026-10-23",
            "description": "second edited",
            "type": "income",
        },
        follow_redirects=True,
    )

    rows = {t["id"]: t for t in app_db.list_transactions()}
    assert app_db.count_transactions() == 2
    assert rows[first_id]["amount"] == app_db.Decimal("11.00")
    assert rows[first_id]["description"] == "first edited"
    assert rows[second_id]["amount"] == app_db.Decimal("550.00")
    assert rows[second_id]["description"] == "second edited"


def test_dashboard_totals(app, client):
    seed_categories()
    _add(client, "Salary", "1000.00", "Salary", "2026-10-20")
    _add(client, "Groceries", "40.00", "Potatoes", "2026-10-20")

    page = client.get("/").get_data(as_text=True)
    assert "Potatoes" in page
    assert app_db.sum_by_type("expense") == app_db.Decimal("40.00")


def test_csv_export(app, client):
    seed_categories()
    _add(client, "Groceries", "40.00", "Potatoes", "2026-10-20")
    response = client.get("/export/transactions.csv")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Potatoes" in body
    assert body.splitlines()[0].startswith("id,date,category")


def test_json_export(app, client):
    seed_categories()
    _add(client, "Groceries", "40.00", "Potatoes", "2026-10-20")
    response = client.get("/export/transactions.json")
    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload["database"] == "money_tracker_test"
    assert payload["transactions"][0]["description"] == "Potatoes"


def test_backup_download(app, client):
    seed_categories()
    _add(client, "Groceries", "40.00", "Potatoes", "2026-10-20")
    response = client.get("/export/backup.sql")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "CREATE TABLE" in body
    assert "money_tracker_test" in body


def test_health_endpoint(app, client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["database"] == "money_tracker_test"
