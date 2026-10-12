"""
Furniture Room — service layer.

Read side:  snapshot_furniture()  (what the kiosk browses)
Write side: create_item / update_item / adjust_stock / deactivate_item

Every write is recorded in audit_events (who, what, before -> after), and
removing an item is a SOFT delete (is_active = False): it vanishes from every
list but its stock rows and history stay intact.
"""

from __future__ import annotations

import json
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, FurnitureItem, FurnitureStock, Location

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
MAX_QTY = 100000


def _fail(code: str, error: str) -> dict:
    return {"ok": False, "code": code, "error": error}


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())  # trim + collapse whitespace


def _parse_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _audit(db: Session, event_type: str, actor: str, detail: dict) -> None:
    db.add(AuditEvent(event_type=event_type, actor=actor, detail=json.dumps(detail, ensure_ascii=False)))


# ---------------------------------------------------------------- read side
def _locations(db: Session) -> list[Location]:
    return db.execute(select(Location).order_by(Location.sort_order, Location.name)).scalars().all()


def _stock_by_item(db: Session, item_ids=None) -> dict:
    q = select(FurnitureStock)
    if item_ids is not None:
        q = q.where(FurnitureStock.item_id.in_(item_ids))
    out: dict = {}
    for row in db.execute(q).scalars().all():
        out.setdefault(row.item_id, {})[str(row.location_id)] = int(row.qty)
    return out


def item_payload(it: FurnitureItem, have: dict, loc_ids: list[str]) -> dict:
    stock = {lid: int(have.get(lid, 0)) for lid in loc_ids}  # every location present, 0 when none
    return {
        "id": str(it.id),
        "sku": it.sku,
        "name": it.name,
        "category": it.category,
        "note": it.note,
        "images": list(it.image_paths or []),
        "stock": stock,
        "total": sum(stock.values()),
    }


def snapshot_furniture(db: Session) -> dict:
    locations = _locations(db)
    items = (
        db.execute(
            select(FurnitureItem)
            .where(FurnitureItem.is_active.is_(True))
            .order_by(FurnitureItem.sort_order, FurnitureItem.name)
        )
        .scalars()
        .all()
    )
    stock_by_item = _stock_by_item(db)
    loc_ids = [str(loc.id) for loc in locations]
    out_items = [item_payload(it, stock_by_item.get(it.id, {}), loc_ids) for it in items]

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


# --------------------------------------------------------------- write side
def _name_taken(db: Session, name: str, exclude_id=None) -> bool:
    q = select(FurnitureItem.id).where(
        func.lower(FurnitureItem.name) == name.lower(), FurnitureItem.is_active.is_(True)
    )
    if exclude_id is not None:
        q = q.where(FurnitureItem.id != exclude_id)
    return db.execute(q).first() is not None


def _next_sku(db: Session) -> str:
    top = 0
    for (sku,) in db.execute(select(FurnitureItem.sku)).all():
        m = re.fullmatch(r"FUR-(\d+)", sku or "")
        if m:
            top = max(top, int(m.group(1)))
    return f"FUR-{top + 1:03d}"


def _validate_text(name, category, note) -> dict | None:
    if not name:
        return _fail("BAD_REQUEST", "Give the item a name.")
    if len(name) > 200:
        return _fail("BAD_REQUEST", "That name is too long (200 characters max).")
    if not category:
        return _fail("BAD_REQUEST", "Pick or type a category.")
    if len(category) > 80:
        return _fail("BAD_REQUEST", "That category name is too long (80 characters max).")
    if note is not None and len(note) > 2000:
        return _fail("BAD_REQUEST", "That note is too long (2000 characters max).")
    return None


def create_item(db: Session, *, name, category, note, stock: dict, actor: str) -> dict:
    name, category, note = _clean(name), _clean(category), (note or "").strip() or None
    bad = _validate_text(name, category, note)
    if bad:
        return bad
    if _name_taken(db, name):
        return _fail("DUPLICATE_NAME", f'There is already an item called "{name}". Use a different name.')

    locations = _locations(db)
    valid_ids = {str(loc.id) for loc in locations}
    wanted: dict = {}
    for lid, qty in (stock or {}).items():
        if str(lid) not in valid_ids:
            return _fail("BAD_REQUEST", "Unknown location.")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0 or qty > MAX_QTY:
            return _fail("BAD_REQUEST", "Quantities must be whole numbers from 0 to 100000.")
        wanted[str(lid)] = qty

    top = db.execute(select(func.max(FurnitureItem.sort_order))).scalar() or 0
    item = FurnitureItem(
        sku=_next_sku(db), name=name, category=category, note=note,
        image_paths=[], sort_order=int(top) + 1, is_active=True,
    )
    db.add(item)
    db.flush()
    for loc in locations:
        db.add(FurnitureStock(item_id=item.id, location_id=loc.id, qty=wanted.get(str(loc.id), 0)))
    db.flush()

    _audit(db, "furniture_add", actor, {
        "sku": item.sku, "name": item.name, "category": item.category,
        "stock": {loc.name: wanted.get(str(loc.id), 0) for loc in locations},
    })
    loc_ids = [str(loc.id) for loc in locations]
    return {"ok": True, "item": item_payload(item, {lid: wanted.get(lid, 0) for lid in loc_ids}, loc_ids)}


def _get_active_item(db: Session, item_id):
    uid = _parse_uuid(item_id)
    item = db.get(FurnitureItem, uid) if uid else None
    if item is None or not item.is_active:
        return None
    return item


def update_item(db: Session, item_id, *, fields: dict, actor: str) -> dict:
    """fields may contain 'name', 'category', 'note' — only what is present is changed."""
    item = _get_active_item(db, item_id)
    if item is None:
        return _fail("NOT_FOUND", "That item no longer exists.")

    name = _clean(fields["name"]) if "name" in fields else item.name
    category = _clean(fields["category"]) if "category" in fields else item.category
    note = ((fields["note"] or "").strip() or None) if "note" in fields else item.note
    bad = _validate_text(name, category, note)
    if bad:
        return bad
    if name.lower() != item.name.lower() and _name_taken(db, name, exclude_id=item.id):
        return _fail("DUPLICATE_NAME", f'There is already an item called "{name}". Use a different name.')

    before = {"name": item.name, "category": item.category, "note": item.note}
    after = {"name": name, "category": category, "note": note}
    if before == after:
        return {"ok": True, "unchanged": True, "item": _fresh_payload(db, item)}
    item.name, item.category, item.note = name, category, note
    db.flush()
    _audit(db, "furniture_edit", actor, {"sku": item.sku, "before": before, "after": after})
    return {"ok": True, "item": _fresh_payload(db, item)}


def _fresh_payload(db: Session, item: FurnitureItem) -> dict:
    loc_ids = [str(loc.id) for loc in _locations(db)]
    return item_payload(item, _stock_by_item(db, [item.id]).get(item.id, {}), loc_ids)


def adjust_stock(db: Session, item_id, *, changes: list, reason: str | None, actor: str) -> dict:
    """
    changes: [{location_id, qty, expected?}] — qty is the NEW absolute count.
    If 'expected' is given and the stored count is different, nothing is saved
    (someone else changed it meanwhile) and a CONFLICT is returned.
    """
    item = _get_active_item(db, item_id)
    if item is None:
        return _fail("NOT_FOUND", "That item no longer exists.")
    if not changes:
        return _fail("BAD_REQUEST", "Nothing to change.")

    locations = {str(loc.id): loc for loc in _locations(db)}
    seen: set = set()
    plan = []
    for ch in changes:
        lid = str(ch.get("location_id"))
        if lid not in locations:
            return _fail("BAD_REQUEST", "Unknown location.")
        if lid in seen:
            return _fail("BAD_REQUEST", "The same location appears twice.")
        seen.add(lid)
        qty = ch.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0 or qty > MAX_QTY:
            return _fail("BAD_REQUEST", "Quantities must be whole numbers from 0 to 100000.")
        row = db.execute(
            select(FurnitureStock)
            .where(FurnitureStock.item_id == item.id, FurnitureStock.location_id == locations[lid].id)
            .with_for_update()
        ).scalar_one_or_none()
        current = int(row.qty) if row is not None else 0
        expected = ch.get("expected")
        if expected is not None and int(expected) != current:
            return _fail(
                "CONFLICT",
                f"Someone else changed {locations[lid].name} to {current} while you were editing. "
                "The latest numbers are shown — check them and save again.",
            )
        plan.append((locations[lid], row, current, qty))

    reason = _clean(reason)[:200] or None
    changed = []
    for loc, row, current, qty in plan:
        if qty == current:
            continue
        if row is None:
            db.add(FurnitureStock(item_id=item.id, location_id=loc.id, qty=qty))
        else:
            row.qty = qty
        changed.append((loc, current, qty))
    db.flush()

    for loc, before, after in changed:
        _audit(db, "furniture_adjust", actor, {
            "sku": item.sku, "name": item.name, "location": loc.name,
            "from": before, "to": after, "reason": reason,
        })
    return {"ok": True, "unchanged": not changed, "item": _fresh_payload(db, item)}


def deactivate_item(db: Session, item_id, *, actor: str) -> dict:
    uid = _parse_uuid(item_id)
    item = db.get(FurnitureItem, uid) if uid else None
    if item is None:
        return _fail("NOT_FOUND", "That item no longer exists.")
    if not item.is_active:
        return {"ok": True, "already_removed": True}
    snapshot = _fresh_payload(db, item)
    item.is_active = False
    db.flush()
    _audit(db, "furniture_remove", actor, {
        "sku": item.sku, "name": item.name, "category": item.category, "stock": snapshot["stock"],
    })
    return {"ok": True}
