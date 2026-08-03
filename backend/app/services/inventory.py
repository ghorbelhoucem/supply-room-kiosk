from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Checkout,
    IdempotencyKey,
    InventoryItem,
    ItemCategory,
    Movement,
    MovementType,
)


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
    if item.category == ItemCategory.tools:
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


def snapshot(db: Session) -> dict:
    items = db.execute(select(InventoryItem).order_by(InventoryItem.name)).scalars().all()
    checkouts = db.execute(select(Checkout).order_by(Checkout.taken_at.desc())).scalars().all()
    inventory = []
    for it in items:
        inventory.append(
            {
                "reference": it.category.value,
                "item": it.name,
                "quantity": it.qty_on_hand,
                "availability": availability_label(db, it),
                "barcode": it.barcode,
                "reorder_min": it.reorder_min,
            }
        )
    history = []
    for c in checkouts:
        item = db.get(InventoryItem, c.item_id)
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
        label = "Restock" if m.movement_type.value == "receive" else "Adjustment"
        history.append(
            {
                "timestamp": m.created_at.isoformat() if m.created_at else "",
                "personRole": m.actor,
                "item": m.item_name,
                "expectedReturn": label,
                "returnedAt": "N/A",
                "returnedBy": None,
                "txId": m.related_tx_id or "",
                "qty": m.qty,
            }
        )

    history.sort(key=lambda r: r["timestamp"], reverse=True)

    return {"ok": True, "inventory": inventory, "history": history}


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
            select(InventoryItem).where(InventoryItem.name == name).with_for_update()
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
        if not item or item.category != ItemCategory.tools:
            return {"ok": False, "error": "Only tools can be returned", "code": "NOT_RETURNABLE"}
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
) -> dict:
    item = db.execute(
        select(InventoryItem).where(InventoryItem.name == item_name).with_for_update()
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
        item = InventoryItem(
            sku=f"NEW-{uuid.uuid4().hex[:8].upper()}",
            name=item_name,
            category=item_category,
            qty_on_hand=0,
            reorder_min=3,
            barcode=item_name,
        )
        db.add(item)
        db.flush()  # get item.id before logging the movement below
        reason = reason or "New item added via kiosk"

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
        select(InventoryItem).where(InventoryItem.name == item_name).with_for_update()
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
