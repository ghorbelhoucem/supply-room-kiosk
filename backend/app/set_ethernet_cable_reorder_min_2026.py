"""
One-off script: sets the reorder point (reorder_min) to 10 for every
Ethernet Cable length variant, instead of the default 5. Only affects
items whose name contains "Ethernet Cable" — "Ethernet Adapter" and
anything else is left untouched.

Run it once, from inside the API container:

    docker compose exec api python -m app.set_ethernet_cable_reorder_min_2026
"""

from app.database import SessionLocal
from app.models import InventoryItem

NEW_REORDER_MIN = 10


def run():
    db = SessionLocal()
    try:
        items = db.query(InventoryItem).filter(
            InventoryItem.name.like("Ethernet Cable%")
        ).all()

        if not items:
            print('No items found matching "Ethernet Cable%" — check the exact naming in your database.')
            return

        for item in items:
            old = item.reorder_min
            item.reorder_min = NEW_REORDER_MIN
            print(f"{item.name}: reorder point {old} -> {NEW_REORDER_MIN}")

        db.commit()
        print(f"Done. Updated {len(items)} item(s).")
    finally:
        db.close()


if __name__ == "__main__":
    run()
