"""
One-off script: sets "Zip Ties Cutter" and "Knife (Cutter)" quantity to 2 each.

Run it once, from inside the API container:

    docker compose exec api python -m app.set_tools_qty_2026
"""

from app.database import SessionLocal
from app.models import InventoryItem

TARGETS = {
    "Zip Ties Cutter": 2,
    "Knife (Cutter)": 2,
}


def run():
    db = SessionLocal()
    try:
        for name, new_qty in TARGETS.items():
            item = db.query(InventoryItem).filter(InventoryItem.name == name).one_or_none()
            if item is None:
                print(f'NOT FOUND: "{name}" — check the exact name matches what\'s in the database.')
                continue
            old_qty = item.qty_on_hand
            item.qty_on_hand = new_qty
            print(f"{name}: {old_qty} -> {new_qty}")
        db.commit()
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
