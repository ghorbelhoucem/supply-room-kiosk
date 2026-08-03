"""
One-off script: sets every existing item's reorder point (reorder_min) to 5,
matching the new default for items created going forward.

This only touches the reorder_min field — quantities, history, and
everything else stays exactly as-is.

Run it once, from inside the API container:

    docker compose exec api python -m app.update_reorder_min_2026
"""

from app.database import SessionLocal
from app.models import InventoryItem


def run():
    db = SessionLocal()
    try:
        items = db.query(InventoryItem).all()
        changed = []
        already_ok = 0
        for item in items:
            if item.reorder_min != 5:
                changed.append(f"{item.name} ({item.reorder_min} -> 5)")
                item.reorder_min = 5
            else:
                already_ok += 1

        db.commit()

        print(f"Updated {len(changed)} item(s), {already_ok} were already at 5.")
        if changed:
            print("Changed:")
            for line in changed:
                print(" -", line)
    finally:
        db.close()


if __name__ == "__main__":
    run()
