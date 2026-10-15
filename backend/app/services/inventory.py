from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    Checkout,
    IdempotencyKey,
    InventoryItem,
    ItemCategory,
    Movement,
    MovementType,
    WarrantyReport,
)


# ---- SOP Room shelving -------------------------------------------------------
# Racks A..F, each with up to 6 shelves; a shelf is written like "A3" (rack A, shelf 3).
# ONE constant: it is served to the kiosk inside the snapshot, so the pickers and this
# validation can never disagree. To match a rack's real shelf count, change it here.
SOP_RACK_LAYOUT = {"A": 6, "B": 6, "C": 6, "D": 6, "E": 6, "F": 6}

# Places that are not a numbered rack shelf. A SOP is either on a rack shelf ("A3") or in one
# of these ("Basement"). Keep each name to 8 characters or fewer: that is the width of the database
# column AND the limit of ReceiveRequest.shelf_location in schemas.py. A longer name needs both widened.
SOP_EXTRA_LOCATIONS = ["Basement"]
if any(len(place) > 8 for place in SOP_EXTRA_LOCATIONS):
    raise ValueError("SOP_EXTRA_LOCATIONS: names must be 8 characters or fewer (inventory_items.shelf_location is VARCHAR(8) and ReceiveRequest.shelf_location allows 8)")


def sop_layout() -> list[dict]:
    return [{"rack": rack, "shelves": n} for rack, n in SOP_RACK_LAYOUT.items()]


def sop_locations() -> list[str]:
    return list(SOP_EXTRA_LOCATIONS)


def normalize_shelf(raw) -> str | None:
    """'a 3' / 'A-3' / 'a03' -> 'A3'. None if nothing was given. ValueError (friendly text) if it is not a real shelf."""
    code = re.sub(r"[\s\-]+", "", str(raw or "")).upper()
    if not code:
        return None
    for place in SOP_EXTRA_LOCATIONS:  # "basement" / " BASE MENT " -> "Basement"
        if code == re.sub(r"[\s\-]+", "", place).upper():
            return place
    m = re.fullmatch(r"([A-Z])(\d{1,2})", code)
    if not m or m.group(1) not in SOP_RACK_LAYOUT:
        extras = "".join(f", or {place}" for place in SOP_EXTRA_LOCATIONS)
        raise ValueError(f'"{raw}" is not a location. Use a rack letter ({", ".join(SOP_RACK_LAYOUT)}) and a shelf number, like A3{extras}.')
    rack, shelf = m.group(1), int(m.group(2))
    if not 1 <= shelf <= SOP_RACK_LAYOUT[rack]:
        raise ValueError(f"Rack {rack} has shelves 1 to {SOP_RACK_LAYOUT[rack]}.")
    return f"{rack}{shelf}"


def available_qty(db: Session, item: InventoryItem) -> int:
    if item.category == ItemCategory.station_parts:
        return max(0, item.qty_on_hand)
    open_count = (
        db.execute(
            select(Checkout).where(
                Checkout.item_id == item.id,
                Checkout.returned_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    missing = sum(c.qty for c in open_count)
    return max(0, item.qty_on_hand - missing)


def availability_label(db: Session, item: InventoryItem) -> str:
    avail = available_qty(db, item)
    if item.category in (ItemCategory.tools, ItemCategory.sops):
        missing = item.qty_on_hand - avail
        if missing > 0:
            return f"{avail}/{item.qty_on_hand} ({missing} missing)"
        return f"{avail}/{item.qty_on_hand}"
    if avail <= 0:
        return "X"
    return f"{avail} left"


def get_idempotent(db: Session, client_request_id: str) -> dict | None:
    row = db.execute(
        select(IdempotencyKey).where(IdempotencyKey.client_request_id == client_request_id)
    ).scalar_one_or_none()
    return row.response_json if row else None


def save_idempotent(db: Session, client_request_id: str, action: str, response: dict) -> None:
    db.add(
        IdempotencyKey(
            client_request_id=client_request_id,
            action=action,
            response_json=response,
        )
    )


def inventory_row(db: Session, it: InventoryItem) -> dict:
    return {
        "reference": it.category.value,
        "item": it.name,
        "quantity": it.qty_on_hand,
        "availability": availability_label(db, it),
        "barcode": it.barcode,
        "reorder_min": it.reorder_min,
        "sop_status": it.sop_status,
        "shelf": it.shelf_location,
    }


def snapshot(db: Session) -> dict:
    items = db.execute(
        select(InventoryItem).where(InventoryItem.is_active.is_(True)).order_by(InventoryItem.name)
    ).scalars().all()
    checkouts = db.execute(select(Checkout).order_by(Checkout.taken_at.desc())).scalars().all()
    inventory = [inventory_row(db, it) for it in items]
    history = []
    for c in checkouts:
        item = db.get(InventoryItem, c.item_id)
        if item is not None and not item.is_active:
            continue  # a deleted item's history stays in the database but out of the lists
        if c.returned_by == "N/A":
            returned_at = "N/A"
        elif c.returned_at:
            returned_at = c.returned_at.isoformat()
        else:
            returned_at = "Not returned"
        history.append(
            {
                "timestamp": c.taken_at.isoformat() if c.taken_at else "",
                "personRole": c.person_role,
                "item": item.name if item else "Unknown",
                "expectedReturn": c.expected_return.isoformat() if c.expected_return else "None",
                "returnedAt": returned_at,
                "returnedBy": c.returned_by,
                "txId": c.tx_id,
                "qty": c.qty,
            }
        )

    # Also surface receive/adjust movements (restocks & manual adjustments) —
    # these previously only showed up in the Google Sheet mirror, not here.
    other_moves = (
        db.execute(
            select(Movement)
            .where(Movement.movement_type.in_(["receive", "adjust"]))
            .order_by(Movement.created_at.desc())
        )
        .scalars()
        .all()
    )
    for m in other_moves:
        owner = db.get(InventoryItem, m.item_id) if m.item_id else None
        if owner is not None and not owner.is_active:
            continue
        label = "Restock" if m.movement_type.value == "receive" else "Adjustment"
        history.append(
            {
                "timestamp": m.created_at.isoformat() if m.created_at else "",
                "personRole": m.actor,
                "item": owner.name if owner is not None else m.item_name,   # the CURRENT name, so history follows a rename
                "expectedReturn": label,
                "returnedAt": "N/A",
                "returnedBy": None,
                "txId": m.related_tx_id or "",
                "qty": m.qty,
            }
        )

    history.sort(key=lambda r: r["timestamp"], reverse=True)

    warranty_reports = (
        db.execute(select(WarrantyReport).order_by(WarrantyReport.created_at.desc()))
        .scalars()
        .all()
    )
    warranty = [
        {
            "timestamp": w.created_at.isoformat() if w.created_at else "",
            "reportedBy": w.reported_by,
            "partName": w.part_name,
            "serialNumber": w.serial_number,
            "issue": w.issue,
        }
        for w in warranty_reports
    ]

    return {"ok": True, "inventory": inventory, "history": history, "warranty": warranty, "sop_layout": sop_layout(), "sop_locations": sop_locations()}


def take_batch(db: Session, person: str, role: str, items: list[dict]) -> dict:
    person_role = f"{person}/{role}"
    created = []
    for raw in items:
        name = raw["item"]
        qty = int(raw.get("qty") or 1)
        category = raw.get("category")
        expected_raw = raw.get("expectedReturn")

        if name.startswith("Other:"):
            # Free-text other items are logged as movements only (no stock SKU).
            tx_id = f"tx-{uuid.uuid4().hex[:12]}"
            db.add(
                Movement(
                    movement_type=MovementType.take,
                    item_id=None,
                    item_name=name,
                    qty=qty,
                    actor=person_role,
                    reason="other",
                    related_tx_id=tx_id,
                )
            )
            created.append(tx_id)
            continue

        item = db.execute(
            select(InventoryItem).where(InventoryItem.name == name, InventoryItem.is_active.is_(True)).with_for_update()
        ).scalar_one_or_none()
        if not item:
            return {"ok": False, "error": f'Unknown item "{name}"', "code": "UNKNOWN_ITEM"}
        if category and item.category.value != category:
            return {
                "ok": False,
                "error": f'Category mismatch for "{name}"',
                "code": "CATEGORY_MISMATCH",
            }

        avail = available_qty(db, item)
        if qty > avail:
            return {
                "ok": False,
                "error": f'Only {avail} of "{name}" available',
                "code": "INSUFFICIENT_STOCK",
                "available": avail,
            }

        if item.category == ItemCategory.station_parts:
            item.qty_on_hand -= qty
            tx_id = f"tx-{uuid.uuid4().hex[:12]}"
            db.add(
                Movement(
                    movement_type=MovementType.take,
                    item_id=item.id,
                    item_name=item.name,
                    qty=qty,
                    actor=person_role,
                    related_tx_id=tx_id,
                )
            )
            # Consumable: record closed checkout as N/A return
            expected = None
            db.add(
                Checkout(
                    tx_id=tx_id,
                    item_id=item.id,
                    person_role=person_role,
                    qty=qty,
                    expected_return=None,
                    returned_at=datetime.now(timezone.utc),
                    returned_by="N/A",
                )
            )
            created.append(tx_id)
        else:
            expected = None
            if expected_raw:
                try:
                    expected = datetime.fromisoformat(expected_raw.replace("Z", "+00:00"))
                except ValueError:
                    expected = None
            for _ in range(qty):
                tx_id = f"tx-{uuid.uuid4().hex[:12]}"
                db.add(
                    Checkout(
                        tx_id=tx_id,
                        item_id=item.id,
                        person_role=person_role,
                        qty=1,
                        expected_return=expected,
                    )
                )
                db.add(
                    Movement(
                        movement_type=MovementType.take,
                        item_id=item.id,
                        item_name=item.name,
                        qty=1,
                        actor=person_role,
                        related_tx_id=tx_id,
                    )
                )
                created.append(tx_id)
    return {"ok": True, "txIds": created}


def return_batch(db: Session, tx_ids: list[str], returned_by: str) -> dict:
    now = datetime.now(timezone.utc)
    for tx_id in tx_ids:
        checkout = db.execute(
            select(Checkout).where(Checkout.tx_id == tx_id).with_for_update()
        ).scalar_one_or_none()
        if not checkout:
            return {"ok": False, "error": f"Unknown checkout {tx_id}", "code": "UNKNOWN_TX"}
        if checkout.returned_at is not None and checkout.returned_by != "N/A":
            return {
                "ok": False,
                "error": f"Checkout {tx_id} already returned",
                "code": "ALREADY_RETURNED",
            }
        item = db.get(InventoryItem, checkout.item_id)
        if not item or item.category not in (ItemCategory.tools, ItemCategory.sops):
            return {"ok": False, "error": "Only tools and SOPs can be returned", "code": "NOT_RETURNABLE"}
        checkout.returned_at = now
        checkout.returned_by = returned_by
        db.add(
            Movement(
                movement_type=MovementType.return_,
                item_id=item.id,
                item_name=item.name,
                qty=checkout.qty,
                actor=returned_by,
                related_tx_id=tx_id,
            )
        )
    return {"ok": True, "txIds": tx_ids}


def receive_stock(
    db: Session,
    item_name: str,
    qty: int,
    actor: str,
    reason: str | None,
    category: str | None = None,
    sop_status: str | None = None,
    shelf_location: str | None = None,
    reorder_min: int | None = None,
) -> dict:
    try:
        shelf = normalize_shelf(shelf_location)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "code": "BAD_SHELF"}
    item = db.execute(
        select(InventoryItem).where(InventoryItem.name == item_name, InventoryItem.is_active.is_(True)).with_for_update()
    ).scalar_one_or_none()

    if not item:
        if not category:
            return {"ok": False, "error": f'Unknown item "{item_name}"', "code": "UNKNOWN_ITEM"}
        try:
            item_category = ItemCategory(category)
        except ValueError:
            return {
                "ok": False,
                "error": f'Invalid category "{category}"',
                "code": "INVALID_CATEGORY",
            }
        if item_category == ItemCategory.sops and shelf is None:
            return {
                "ok": False,
                "error": "Choose a location for the new SOP (for example A3, or Basement), so people can find it.",
                "code": "SHELF_REQUIRED",
            }
        item = InventoryItem(
            sku=f"NEW-{uuid.uuid4().hex[:8].upper()}",
            name=item_name,
            category=item_category,
            qty_on_hand=0,
            reorder_min=reorder_min if (reorder_min is not None and item_category != ItemCategory.sops) else 5,
            barcode=item_name,
            sop_status=sop_status if item_category == ItemCategory.sops else None,
            shelf_location=shelf if item_category == ItemCategory.sops else None,
        )
        db.add(item)
        db.flush()  # get item.id before logging the movement below
        reason = reason or "New item added via kiosk"
    elif item.category == ItemCategory.sops:
        if sop_status:
            item.sop_status = sop_status
        if shelf:
            item.shelf_location = shelf  # restocking can also move it to another shelf

    item.qty_on_hand += qty
    db.add(
        Movement(
            movement_type=MovementType.receive,
            item_id=item.id,
            item_name=item.name,
            qty=qty,
            actor=actor,
            reason=reason,
        )
    )
    return {"ok": True, "quantity": item.qty_on_hand}


def adjust_stock(
    db: Session, item_name: str, qty_delta: int, actor: str, reason: str
) -> dict:
    item = db.execute(
        select(InventoryItem).where(InventoryItem.name == item_name, InventoryItem.is_active.is_(True)).with_for_update()
    ).scalar_one_or_none()
    if not item:
        return {"ok": False, "error": f'Unknown item "{item_name}"', "code": "UNKNOWN_ITEM"}
    new_qty = item.qty_on_hand + qty_delta
    if new_qty < 0:
        return {
            "ok": False,
            "error": "Adjustment would make quantity negative",
            "code": "NEGATIVE_STOCK",
            "available": item.qty_on_hand,
        }
    item.qty_on_hand = new_qty
    db.add(
        Movement(
            movement_type=MovementType.adjust,
            item_id=item.id,
            item_name=item.name,
            qty=qty_delta,
            actor=actor,
            reason=reason,
        )
    )
    return {"ok": True, "quantity": item.qty_on_hand}


# ---- Editing items from the Manager Report ----------------------------------------------
SOP_STATUSES = ("Active", "Non-Active")


def _clean_name(raw) -> str:
    return " ".join(str(raw or "").split())


def find_active_item(db: Session, name: str, lock: bool = False) -> InventoryItem | None:
    q = select(InventoryItem).where(InventoryItem.name == name, InventoryItem.is_active.is_(True))
    if lock:
        q = q.with_for_update()
    return db.execute(q).scalar_one_or_none()


def checked_out_qty(db: Session, item: InventoryItem) -> int:
    """How many are out right now. Station Parts are one-time use, so never 'out'."""
    if item.category == ItemCategory.station_parts:
        return 0
    rows = db.execute(
        select(Checkout).where(Checkout.item_id == item.id, Checkout.returned_at.is_(None))
    ).scalars().all()
    return sum(c.qty for c in rows)


def _name_in_use(db: Session, name: str, exclude_id) -> bool:
    return (
        db.execute(
            select(InventoryItem.id).where(func.lower(InventoryItem.name) == name.lower(), InventoryItem.id != exclude_id)
        ).first()
        is not None
    )


def _audit(db: Session, event_type: str, actor: str, detail: dict) -> None:
    db.add(AuditEvent(event_type=event_type, actor=actor, detail=json.dumps(detail, ensure_ascii=False)))


def update_item(
    db: Session,
    item_name: str,
    *,
    actor: str,
    new_name: str | None = None,
    qty: int | None = None,
    expected_qty: int | None = None,
    reorder_min: int | None = None,
    sop_status: str | None = None,
    shelf_location: str | None = None,
    reason: str | None = None,
) -> dict:
    """
    Change what is sent (anything left as None is untouched):
      new_name        rename. The BARCODE never changes, so printed QR labels keep scanning.
      qty             the new total (Tools/SOPs) or amount on hand (Station Parts). If expected_qty
                      is given and the stored number differs, nothing is saved (CONFLICT).
      reorder_min     Tools / Station Parts only.
      sop_status, shelf_location   SOPs only.
    """
    item = find_active_item(db, item_name, lock=True)
    if item is None:
        return {"ok": False, "error": f'Unknown item "{item_name}"', "code": "UNKNOWN_ITEM"}
    is_sop = item.category == ItemCategory.sops
    changes: dict = {}

    if new_name is not None:
        name = _clean_name(new_name)
        if not name:
            return {"ok": False, "error": "Give the item a name.", "code": "BAD_REQUEST"}
        if len(name) > 200:
            return {"ok": False, "error": "That name is too long (200 characters max).", "code": "BAD_REQUEST"}
        if name != item.name:
            if _name_in_use(db, name, item.id):
                return {"ok": False, "error": f'There is already an item called "{name}". Use a different name.', "code": "DUPLICATE_NAME"}
            changes["name"] = [item.name, name]

    if qty is not None:
        if expected_qty is not None and expected_qty != item.qty_on_hand:
            return {
                "ok": False, "code": "CONFLICT", "quantity": item.qty_on_hand,
                "error": f"Someone else changed the quantity to {item.qty_on_hand} while you were editing. "
                         "The latest number is shown — check it and save again.",
            }
        if qty != item.qty_on_hand:
            out = checked_out_qty(db, item)
            if qty < out:
                return {
                    "ok": False, "code": "BELOW_CHECKED_OUT",
                    "error": f"{out} are checked out right now, so the total can't be below {out}. Get them returned first, or enter {out} or more.",
                }
            changes["qty"] = [item.qty_on_hand, qty]

    if reorder_min is not None and not is_sop and reorder_min != item.reorder_min:
        changes["reorder_min"] = [item.reorder_min, reorder_min]

    if is_sop:
        if sop_status:
            if sop_status not in SOP_STATUSES:
                return {"ok": False, "error": 'Status must be "Active" or "Non-Active".', "code": "BAD_REQUEST"}
            if sop_status != item.sop_status:
                changes["sop_status"] = [item.sop_status, sop_status]
        try:
            shelf = normalize_shelf(shelf_location)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "code": "BAD_SHELF"}
        if shelf and shelf != item.shelf_location:
            changes["shelf"] = [item.shelf_location, shelf]

    if not changes:
        return {"ok": True, "unchanged": True, "changes": {}, "item": inventory_row(db, item)}

    old_name = item.name
    reason = " ".join((reason or "").split())[:200] or None
    if "name" in changes:
        if item.barcode is None and not _barcode_taken(db, old_name, item.id):
            item.barcode = old_name  # keep the old QR label working
        item.name = changes["name"][1]
    if "qty" in changes:
        before, after = changes["qty"]
        item.qty_on_hand = after
        db.add(
            Movement(
                movement_type=MovementType.adjust, item_id=item.id, item_name=item.name,
                qty=after - before, actor=actor, reason=reason or "Edited in Manager Report",
            )
        )
    if "reorder_min" in changes:
        item.reorder_min = changes["reorder_min"][1]
    if "sop_status" in changes:
        item.sop_status = changes["sop_status"][1]
    if "shelf" in changes:
        item.shelf_location = changes["shelf"][1]
    db.flush()
    _audit(db, "inventory_edit", actor, {"item": old_name, "sku": item.sku, "category": item.category.value, "changes": changes, "reason": reason})
    return {"ok": True, "changes": changes, "item": inventory_row(db, item)}


def _barcode_taken(db: Session, code: str, exclude_id) -> bool:
    return db.execute(select(InventoryItem.id).where(InventoryItem.barcode == code, InventoryItem.id != exclude_id)).first() is not None


def delete_item(db: Session, item_name: str, *, actor: str) -> dict:
    """
    SOFT delete. The item disappears from every list, the Sheets and the low-stock alerts, but its
    history stays in the database. Its name gets a '(deleted …)' tail and its barcode is cleared,
    so the same name can be used again for a brand-new item.
    """
    item = find_active_item(db, item_name, lock=True)
    if item is None:
        return {"ok": False, "error": f'Unknown item "{item_name}"', "code": "UNKNOWN_ITEM"}
    out = checked_out_qty(db, item)
    if out:
        return {"ok": False, "code": "STILL_CHECKED_OUT", "error": f"{out} still checked out — get it returned first, then delete it."}

    old_name = item.name
    tail = f" (deleted {datetime.now(timezone.utc):%Y-%m-%d %H%M})"
    new_name, n = old_name[: 200 - len(tail)] + tail, 1
    while _name_in_use(db, new_name, item.id):
        n += 1
        new_name = f"{old_name[: 190 - len(tail)]}{tail} #{n}"
    item.name = new_name
    item.barcode = None
    item.is_active = False
    item.needs_purchase_alerted = False
    db.flush()
    _audit(db, "inventory_delete", actor, {"item": old_name, "sku": item.sku, "category": item.category.value, "qty_on_hand": item.qty_on_hand})
    return {"ok": True, "deleted": old_name}
