"""
Google Sheets sync — writes into the SAME Inventory / History tabs and
column layout the kiosk has always used.

No Google Cloud service account needed: this forwards the full current
state to your existing (free) Apps Script Web App deployment, which does
the actual writing using its own built-in Google authorization.

Enable with: LEGACY_WEBAPP_URL=<your Apps Script /exec URL>
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditEvent, Checkout, InventoryItem, ItemCategory, Movement

logger = logging.getLogger(__name__)


def _fmt(dt) -> str:
    if not dt:
        return ""
    return dt.isoformat()


def _consolidate_history_rows(rows: list) -> list:
    """
    Rows here are [Timestamp, Person/Role, Item, ExpectedReturn, ReturnedAt,
    TxID, Qty, ReturnedBy]. Merge rows that are identical except for TxID/Qty
    — same item, same person, same exact moment, same return status — into
    one row with a combined Qty. Grouping by the exact timestamp correctly
    represents "one transaction": every row created by a single take/return/
    restock action shares the same server-side transaction timestamp.
    """
    groups: dict = {}
    order: list = []
    for row in rows:
        timestamp, person_role, item, expected, returned_at, tx_id, qty, returned_by = row
        key = (item, person_role, timestamp, expected, returned_at, returned_by)
        if key not in groups:
            groups[key] = {"row": row, "qty": 0}
            order.append(key)
        groups[key]["qty"] += qty or 0

    merged = []
    for key in order:
        row, qty = groups[key]["row"], groups[key]["qty"]
        merged.append([row[0], row[1], row[2], row[3], row[4], row[5], qty, row[7]])
    return merged


def build_mirror_payload(db: Session) -> dict:
    items = db.execute(select(InventoryItem).order_by(InventoryItem.name)).scalars().all()

    # Open (not-returned) Tools checkouts, grouped by item, for the "missing" count
    open_qty_by_item: dict = {}
    open_checkouts = db.execute(
        select(Checkout).where(Checkout.returned_at.is_(None))
    ).scalars().all()
    for c in open_checkouts:
        open_qty_by_item[c.item_id] = open_qty_by_item.get(c.item_id, 0) + c.qty

    inventory_rows = []
    for it in items:
        if it.category == ItemCategory.tools:
            missing = open_qty_by_item.get(it.id, 0)
            if it.qty_on_hand <= 0:
                availability = "X"
            elif missing <= 0:
                availability = "✓"
            else:
                availability = f"{missing} part(s) missing"
        else:
            availability = "X" if it.qty_on_hand <= 0 else "✓"

        inventory_rows.append([it.category.value, it.name, it.qty_on_hand, availability])

    # Purchase List: anything at or below its reorder point, lowest stock first
    purchase_rows = sorted(
        (
            [it.category.value, it.name, it.qty_on_hand, it.reorder_min]
            for it in items
            if it.qty_on_hand <= it.reorder_min
        ),
        key=lambda r: r[2],
    )

    # History: every checkout (take/return pair) ...
    history_rows = []
    all_checkouts = db.execute(select(Checkout).order_by(Checkout.taken_at)).scalars().all()
    for c in all_checkouts:
        item = db.get(InventoryItem, c.item_id)
        is_tool = item and item.category == ItemCategory.tools
        expected = _fmt(c.expected_return) if (is_tool and c.expected_return) else "None"
        if is_tool:
            returned_at = _fmt(c.returned_at) if c.returned_at else "Not returned"
        else:
            returned_at = _fmt(c.returned_at) if c.returned_at else "N/A"
        history_rows.append(
            [
                _fmt(c.taken_at),
                c.person_role,
                item.name if item else "",
                expected,
                returned_at,
                c.tx_id,
                c.qty,
                c.returned_by or "",
            ]
        )

    # ... plus receive/adjust movements (restocks and manual adjustments)
    other_moves = db.execute(
        select(Movement)
        .where(Movement.movement_type.in_(["receive", "adjust"]))
        .order_by(Movement.created_at)
    ).scalars().all()
    for m in other_moves:
        label = "Restock" if m.movement_type.value == "receive" else "Adjustment"
        history_rows.append(
            [
                _fmt(m.created_at),
                m.actor,
                m.item_name,
                label,
                "N/A",
                m.related_tx_id or "",
                m.qty,
                "",
            ]
        )

    history_rows.sort(key=lambda r: r[0])
    history_rows = _consolidate_history_rows(history_rows)

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "inventory": inventory_rows,
        "history": history_rows,
        "purchase_list": purchase_rows,
    }


def _push_to_legacy_webapp(webapp_url: str, payload: dict) -> None:
    body = {
        "action": "fullSync",
        "inventory": payload["inventory"],
        "history": payload["history"],
        "purchase_list": payload["purchase_list"],
    }
    resp = httpx.post(webapp_url, json=body, timeout=20.0, follow_redirects=True)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Apps Script rejected sync: {result.get('error')}")


def sync_mirror(db: Session) -> dict:
    settings = get_settings()
    payload = build_mirror_payload(db)
    webapp_url = (settings.legacy_webapp_url or "").strip()

    if not webapp_url:
        logger.info("Sheet sync skipped (no LEGACY_WEBAPP_URL set). rows=%s", len(payload["inventory"]))
        return {"ok": True, "skipped": True, "payload_preview_rows": len(payload["inventory"])}

    try:
        _push_to_legacy_webapp(webapp_url, payload)
        db.add(AuditEvent(event_type="sheet_sync_ok", detail=payload["updated_at"]))
        db.commit()
        return {"ok": True, "updated_at": payload["updated_at"]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sheet sync failed")
        db.add(AuditEvent(event_type="sheet_sync_failed", detail=str(exc)))
        db.commit()
        return {"ok": False, "error": str(exc)}


def maybe_sync_after_mutation(db: Session) -> None:
    settings = get_settings()
    if (settings.legacy_webapp_url or "").strip():
        sync_mirror(db)
