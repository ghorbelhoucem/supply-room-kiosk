"""
One-off migration: adds "SOPs" as a valid value in the item_category enum
type, so InventoryItem rows can use category=ItemCategory.sops. Needed
because SQLAlchemy's create_all doesn't alter existing enum types.

Run it once, from inside the API container:

    docker compose exec api python -m app.add_sops_category_2026
"""

from sqlalchemy import text

from app.database import engine


def run():
    with engine.begin() as conn:
        # Postgres requires ALTER TYPE ... ADD VALUE to run outside a
        # transaction block in older versions; autocommit via begin() here
        # works on modern Postgres (12+) for a single ADD VALUE statement.
        conn.execute(text("ALTER TYPE item_category ADD VALUE IF NOT EXISTS 'SOPs'"))
    print("Done — 'SOPs' is now a valid item_category value.")


if __name__ == "__main__":
    run()
