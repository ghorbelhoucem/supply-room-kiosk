"""
Furniture Room — read side. Builds the one payload the kiosk needs to browse:
locations, categories (in display order) and every item with its per-location
stock. Lives in its own module so nothing here can affect the Storage/SOP code.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FurnitureItem, FurnitureStock, Location

# Display order + icon (+ optional note) per category. Items store the category
# as plain text; anything not listed here is still shown, after these, with a
# default icon. Single source of truth — the kiosk gets this via the API.
CATEGORY_META = [
    ("Chairs & Seating", "🪑", None),
    ("Tables & Desks", "📐", None),
    ("Storage & Shelving", "🗄️", None),
    ("Kitchen & Appliances", "☕", None),
    ("Whiteboards & Displays", "📺", None),
    ("Robots & Stations", "🤖", None),
    ("IT", "💻", None),
    ("Workshop / Maintenance", "🔧", "Out of delta — waiting for Felix / Yie"),
    ("Transport & Logistics", "🚚", None),
    ("Office Essentials", "🧯", None),
]
DEFAULT_ICON = "📦"


def snapshot_furniture(db: Session) -> dict:
    locations = db.execute(select(Location).order_by(Location.sort_order, Location.name)).scalars().all()
    items = (
        db.execute(
            select(FurnitureItem)
            .where(FurnitureItem.is_active.is_(True))
            .order_by(FurnitureItem.sort_order, FurnitureItem.name)
        )
        .scalars()
        .all()
    )

    stock_by_item: dict = {}
    for row in db.execute(select(FurnitureStock)).scalars().all():
        stock_by_item.setdefault(row.item_id, {})[str(row.location_id)] = int(row.qty)

    loc_ids = [str(loc.id) for loc in locations]
    out_items = []
    for it in items:
        have = stock_by_item.get(it.id, {})
        stock = {lid: have.get(lid, 0) for lid in loc_ids}  # every location present, 0 when none
        out_items.append(
            {
                "id": str(it.id),
                "sku": it.sku,
                "name": it.name,
                "category": it.category,
                "note": it.note,
                "images": list(it.image_paths or []),
                "stock": stock,
                "total": sum(stock.values()),
            }
        )

    present = {i["category"] for i in out_items}
    known = {name for name, _, _ in CATEGORY_META}
    categories = [
        {"name": name, "icon": icon, "note": note}
        for name, icon, note in CATEGORY_META
        if name in present
    ] + [{"name": name, "icon": DEFAULT_ICON, "note": None} for name in sorted(present - known)]

    return {
        "ok": True,
        "locations": [{"id": str(loc.id), "name": loc.name} for loc in locations],
        "categories": categories,
        "items": out_items,
    }
