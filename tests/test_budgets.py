"""Monthly budgets: rollover, alerts, copy, overall limit and JSON routes."""

import db as app_db
from helpers import cat_id, seed_categories


def _csrf(client):
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token")
    if not token:
        client.get("/budgets")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token")
    return token


def _post(client, url, payload=None):
    return client.post(
        url,
        json=payload or {},
        headers={"X-CSRF-Token": _csrf(client), "X-Requested-With": "fetch"},
    )


def test_budgets_are_scoped_to_their_month(app):
    seed_categories()
    groceries = cat_id("Groceries")

    app_db.upsert_budget(groceries, "100.00", month="2026-09")
    app_db.upsert_budget(groceries, "250.00", month="2026-10")

    assert app_db.budget_for_category_month(groceries, "2026-09")["monthly_limit"] == app_db.Decimal("100.00")
    assert app_db.budget_for_category_month(groceries, "2026-10")["monthly_limit"] == app_db.Decimal("250.00")
    assert app_db.budget_for_category_month(groceries, "2026-11") is None
    assert len(app_db.list_budgets("2026-10")) == 1


def test_rollover_adds_last_month_unused_money(app):
    seed_categories()
    groceries = cat_id("Groceries")

    app_db.upsert_budget(groceries, "100.00", month="2026-09")
    app_db.add_transaction(groceries, "40.00", "food", "2026-09-10")
    app_db.upsert_budget(groceries, "100.00", month="2026-10", rollover=True)

    budget = app_db.list_budgets_with_spent("2026-10")[0]
    assert budget["rollover_amount"] == app_db.Decimal("60.00")
    assert budget["effective_limit"] == app_db.Decimal("160.00")


def test_budget_spending_and_status(app):
    seed_categories()
    groceries = cat_id("Groceries")

    app_db.upsert_budget(groceries, "100.00", month="2026-10")
    app_db.add_transaction(groceries, "85.00", "food", "2026-10-10")

    budget = app_db.list_budgets_with_spent("2026-10")[0]
    assert budget["spent"] == app_db.Decimal("85.00")
    assert budget["remaining"] == app_db.Decimal("15.00")
    assert budget["percent"] == 85.0
    assert budget["status"] == "warning"


def test_alerts_are_created_once_per_level(app):
    seed_categories()
    groceries = cat_id("Groceries")

    app_db.upsert_budget(groceries, "100.00", month="2026-10", alert_threshold=80)
    budget_id = app_db.budget_for_category_month(groceries, "2026-10")["id"]
    app_db.add_transaction(groceries, "80.00", "food", "2026-10-10")

    assert app_db.refresh_budget_alerts("2026-10") == [f"category:{budget_id}:threshold"]
    assert app_db.refresh_budget_alerts("2026-10") == []

    app_db.add_transaction(groceries, "30.00", "food", "2026-10-11")
    assert app_db.refresh_budget_alerts("2026-10") == [f"category:{budget_id}:over"]
    assert len(app_db.list_alerts()) == 2


def test_copy_last_month_skips_existing_budgets(app, monkeypatch):
    seed_categories()
    groceries = cat_id("Groceries")
    transport = cat_id("Transport")

    app_db.upsert_budget(groceries, "100.00", month="2026-09", rollover=True)
    app_db.upsert_budget(transport, "50.00", month="2026-09")
    app_db.upsert_budget(transport, "60.00", month="2026-10")
    monkeypatch.setattr(app_db, "_backup_before_bulk", lambda: "backup.sql")

    preview = app_db.preview_copy_budgets("2026-10")
    assert preview["copy_count"] == 1
    assert [item["name"] for item in preview["items"] if not item["skip"]] == ["Groceries"]

    result = app_db.copy_budgets_from_last_month("2026-10")
    assert result["copied"] == 1
    budgets = {b["name"]: b for b in app_db.list_budgets("2026-10")}
    assert budgets["Groceries"]["monthly_limit"] == app_db.Decimal("100.00")
    assert budgets["Groceries"]["rollover"] is True
    assert budgets["Transport"]["monthly_limit"] == app_db.Decimal("60.00")


def test_copy_route_copies_into_the_posted_month(app, client, monkeypatch):
    seed_categories()
    groceries = cat_id("Groceries")
    app_db.upsert_budget(groceries, "100.00", month="2026-09")
    monkeypatch.setattr(app_db, "_backup_before_bulk", lambda: "backup.sql")

    response = _post(client, "/budgets/copy-last-month", {"month": "2026-10"})
    assert response.status_code == 200
    assert response.get_json()["copied"] == 1
    assert app_db.budget_for_category_month(groceries, "2026-10") is not None
    assert app_db.budget_for_category_month(groceries, "2026-09") is not None


def test_overall_budget_and_overview(app):
    seed_categories()
    groceries = cat_id("Groceries")

    app_db.upsert_budget(groceries, "100.00", month="2026-10")
    app_db.upsert_overall_budget("2026-10", "400.00")
    app_db.add_transaction(groceries, "120.00", "food", "2026-10-10")

    overview = app_db.budget_overview("2026-10")
    assert overview["overall_limit"] == app_db.Decimal("400.00")
    assert overview["overall_spent"] == app_db.Decimal("120.00")
    assert overview["overall_remaining"] == app_db.Decimal("280.00")
    assert overview["overall_percent"] == 30.0
    assert overview["over_budgets"][0]["name"] == "Groceries"
    assert overview["total_remaining"] == app_db.Decimal("-20.00")


def test_budget_routes_add_edit_delete_undo_and_overall(app, client):
    seed_categories()
    groceries = cat_id("Groceries")

    response = _post(
        client,
        "/budgets/add",
        {
            "month": "2026-10",
            "category_id": groceries,
            "monthly_limit": "100.00",
            "alert_threshold": "70",
            "rollover": "1",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    budget_id = payload["id"]

    page = client.get("/budgets?month=2026-10").get_data(as_text=True)
    assert "Groceries" in page
    assert "Alert 70%" in page

    response = _post(
        client,
        f"/budgets/{budget_id}/edit",
        {"month": "2026-10", "monthly_limit": "150.00", "alert_threshold": "90"},
    )
    assert response.status_code == 200
    assert app_db.get_budget(budget_id)["monthly_limit"] == app_db.Decimal("150.00")

    response = _post(client, f"/budgets/{budget_id}/delete", {"month": "2026-10"})
    assert response.status_code == 200
    assert app_db.get_budget(budget_id) is None
    assert app_db.list_budgets("2026-10") == []

    response = _post(client, f"/budgets/{budget_id}/undo", {"month": "2026-10"})
    assert response.status_code == 200
    assert app_db.get_budget(budget_id) is not None

    response = _post(client, "/budgets/overall", {"month": "2026-10", "monthly_limit": "500.00"})
    assert response.status_code == 200
    overall = app_db.overall_budget("2026-10")
    assert overall["monthly_limit"] == app_db.Decimal("500.00")

    response = _post(
        client,
        f"/budgets/overall/{overall['id']}/delete",
        {"month": "2026-10"},
    )
    assert response.status_code == 200
    assert app_db.overall_budget("2026-10") is None

    response = _post(
        client,
        f"/budgets/overall/{overall['id']}/undo",
        {"month": "2026-10"},
    )
    assert response.status_code == 200
    assert app_db.overall_budget("2026-10") is not None


def test_budget_add_requires_a_limit(app, client):
    seed_categories()
    response = _post(
        client,
        "/budgets/add",
        {"month": "2026-10", "category_id": cat_id("Groceries")},
    )
    assert response.status_code == 400
    assert "required" in response.get_json()["error"].lower()
