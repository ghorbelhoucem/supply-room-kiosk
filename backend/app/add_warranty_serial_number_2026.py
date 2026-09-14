"""
One-off migration: adds the serial_number column to warranty_reports.

Added as nullable at the database level (safe even if you already have
existing warranty rows without one) — the API layer still requires it for
every new submission going forward, enforced by the request schema.

Run it once, from inside the API container:

    docker compose exec api python -m app.add_warranty_serial_number_2026
"""

from sqlalchemy import text

from app.database import engine


def run():
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE warranty_reports ADD COLUMN IF NOT EXISTS serial_number VARCHAR(100)")
        )
    print("Done — serial_number column is now on warranty_reports.")


if __name__ == "__main__":
    run()
