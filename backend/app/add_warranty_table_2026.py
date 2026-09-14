"""
One-off migration: creates the warranty_reports table.

Run it once, from inside the API container:

    docker compose exec api python -m app.add_warranty_table_2026
"""

from app.database import Base, engine
from app.models import WarrantyReport  # noqa: F401 — needed so Base knows about this table


def run():
    Base.metadata.create_all(bind=engine, tables=[WarrantyReport.__table__])
    print("Done — warranty_reports table created.")


if __name__ == "__main__":
    run()
