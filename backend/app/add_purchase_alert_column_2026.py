"""
One-off migration: adds the needs_purchase_alerted column to the existing
inventory_items table. Needed because SQLAlchemy's create_all only creates
brand-new tables — it doesn't alter columns onto a table that already exists.

Run it once, from inside the API container:

    docker compose exec api python -m app.add_purchase_alert_column_2026
"""

from sqlalchemy import text

from app.database import engine


def run():
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE inventory_items "
                "ADD COLUMN IF NOT EXISTS needs_purchase_alerted BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
    print("Done — needs_purchase_alerted column is now on inventory_items.")


if __name__ == "__main__":
    run()
