"""
One-off migration: creates the four Furniture Room tables
(locations, furniture_items, furniture_stock, furniture_moves).

Safe to run more than once — existing tables are left alone.

    docker compose exec api python -m app.add_furniture_tables_2026
"""

from app.database import Base, engine
from app.models import FurnitureItem, FurnitureMove, FurnitureStock, Location


def run():
    tables = [Location.__table__, FurnitureItem.__table__, FurnitureStock.__table__, FurnitureMove.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    print("Done — furniture tables are in place:", ", ".join(t.name for t in tables))


if __name__ == "__main__":
    run()
