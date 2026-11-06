"""Small helpers shared by the test modules."""

import db as app_db


def seed_categories():
    """Seed the default categories into the TEST database (idempotent)."""
    app_db._seed_categories()


def cat_id(name):
    """Return the id of a seeded category by name (never hard-code ids)."""
    from sqlalchemy import text

    with app_db.db.engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM categories WHERE name = :name"), {"name": name}
        ).fetchone()
    if row is None:
        raise AssertionError(f"category '{name}' has not been seeded")
    return row[0]
