"""
Imports the furniture inventory (furniture_seed.json, sitting next to this
file) into the database: locations, items, and the starting stock per location.

Safe to re-run:
  * items are matched by SKU — name / category / note / photos are refreshed,
  * stock rows are only CREATED when missing, never overwritten — so counts
    that have changed through real moves are never reset by a re-run.

    docker compose exec api python -m app.seed_furniture_2026
"""

import json
from pathlib import Path

from sqlalchemy import func

from app.database import SessionLocal
from app.models import FurnitureItem, FurnitureStock, Location

MANIFEST = Path(__file__).with_name("furniture_seed.json")


def run():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        locations = {}
        for loc in data["locations"]:
            row = db.query(Location).filter(Location.name == loc["name"]).one_or_none()
            if row is None:
                row = Location(name=loc["name"], sort_order=loc["sort"])
                db.add(row)
                db.flush()
            else:
                row.sort_order = loc["sort"]
            locations[loc["name"]] = row

        created = updated = stock_created = 0
        for sort_order, it in enumerate(data["items"], start=1):
            item = db.query(FurnitureItem).filter(FurnitureItem.sku == it["sku"]).one_or_none()
            if item is None:
                item = FurnitureItem(sku=it["sku"], name=it["name"], category=it["category"])
                db.add(item)
                created += 1
            else:
                updated += 1
            item.name = it["name"]
            item.category = it["category"]
            item.note = it.get("note")
            item.image_paths = it.get("images", [])
            item.sort_order = sort_order
            db.flush()

            for loc_name, qty in it["stock"].items():
                if db.get(FurnitureStock, (item.id, locations[loc_name].id)) is None:
                    db.add(FurnitureStock(item_id=item.id, location_id=locations[loc_name].id, qty=int(qty)))
                    stock_created += 1

        db.commit()

        print(f"Items: {created} created, {updated} refreshed.  Stock rows created: {stock_created}.")
        for name, loc in locations.items():
            total = (
                db.query(func.coalesce(func.sum(FurnitureStock.qty), 0))
                .filter(FurnitureStock.location_id == loc.id)
                .scalar()
            )
            print(f"  {name}: {total} units")
    finally:
        db.close()


if __name__ == "__main__":
    run()
