"""
One-off migration: adds the sop_status column to inventory_items.
Plain VARCHAR, not an enum — deliberately, to avoid the exact enum-value
pitfall we hit with the 'category' column.

Run it once, from inside the API container:

    docker compose exec api python -m app.add_sop_status_column_2026
"""

from sqlalchemy import text

from app.database import engine


def run():
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS sop_status VARCHAR(20)")
        )
    print("Done — sop_status column is now on inventory_items.")


if __name__ == "__main__":
    run()
