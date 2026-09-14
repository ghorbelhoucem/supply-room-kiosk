"""
Google Sheets sync — pushes Storage Room and SOP Room data to two
COMPLETELY SEPARATE Google Sheets (different access permissions per room).

No Google Cloud service account needed: this forwards each room's current
state to its own (free) Apps Script Web App deployment, which does the
actual writing using its own built-in Google authorization.

Enable with:
  LEGACY_WEBAPP_URL=<Storage Room Apps Script /exec URL>
  SOP_WEBAPP_URL=<SOP Room Apps Script /exec URL>
Either can be left blank to skip syncing that room.
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

STORAGE_CATEGORIES = {ItemCategory.tools, ItemCategory.station_parts}
SOP_CATEGORIES = {ItemCategory.sops}


def _fmt(dt) -> str:
    if not dt:
        return ""
    return dt.isoformat()


def _consolidate_history_rows(rows: list) -> list:
    """
    Rows here are [Timestamp, Person/Role, Item, ExpectedReturn, ReturnedAt,
    TxID, Qty, ReturnedBy]. Merge rows that are identical except for TxID/Qty
    — same item, same person, same exact moment, same return status — into
    one row with a combined Qty.
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


def _build_payload_for_categories(db: Session, categories: set) -> dict:
    """Builds an Inventory/History/Purchase-List payload limited to just the
    given set of ItemCategory values — used to keep Storage Room and SOP
    Room data fully separate from each other."""
    items = [
        it
        for it in db.execute(select(InventoryItem).order_by(InventoryItem.name)).scalars().all()
        if it.category in categories
    ]
    item_ids = {it.id for it in items}

    open_qty_by_item: dict = {}
    open_checkouts = db.execute(
        select(Checkout).where(Checkout.returned_at.is_(None))
    ).scalars().all()
    for c in open_checkouts:
        if c.item_id in item_ids:
            open_qty_by_item[c.item_id] = open_qty_by_item.get(c.item_id, 0) + c.qty

    inventory_rows = []
    for it in items:
        if it.category in (ItemCategory.tools, ItemCategory.sops):
            missing = open_qty_by_item.get(it.id, 0)
            if it.qty_on_hand <= 0:
                availability = "X"
            elif missing <= 0:
                availability = "✓"
            else:
                availability = f"{missing} part(s) missing"
        else:
            availability = "X" if it.qty_on_hand <= 0 else "✓"

        if it.category == ItemCategory.sops:
            reference = f"{it.sop_status} SOP" if it.sop_status else "SOPs"
        else:
            reference = it.category.value
        inventory_rows.append([reference, it.name, it.qty_on_hand, availability])

    purchase_rows = sorted(
        (
            [it.category.value, it.name, it.qty_on_hand, it.reorder_min]
            for it in items
            if it.category != ItemCategory.sops and it.qty_on_hand <= it.reorder_min
        ),
        key=lambda r: r[2],
    )

    history_rows = []
    all_checkouts = db.execute(select(Checkout).order_by(Checkout.taken_at)).scalars().all()
    for c in all_checkouts:
        if c.item_id not in item_ids:
            continue
        item = db.get(InventoryItem, c.item_id)
        is_tool_like = item and item.category in (ItemCategory.tools, ItemCategory.sops)
        expected = _fmt(c.expected_return) if (is_tool_like and c.expected_return) else "None"
        if is_tool_like:
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

    other_moves = db.execute(
        select(Movement)
        .where(Movement.movement_type.in_(["receive", "adjust"]))
        .order_by(Movement.created_at)
    ).scalars().all()
    for m in other_moves:
        if m.item_id not in item_ids:
            continue
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


def _push_to_webapp(webapp_url: str, payload: dict) -> None:
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
    storage_url = (settings.legacy_webapp_url or "").strip()
    sop_url = (settings.sop_webapp_url or "").strip()

    results = {}

    if storage_url:
        storage_payload = _build_payload_for_categories(db, STORAGE_CATEGORIES)
        try:
            _push_to_webapp(storage_url, storage_payload)
            results["storage"] = {"ok": True, "updated_at": storage_payload["updated_at"]}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Storage Room sheet sync failed")
            results["storage"] = {"ok": False, "error": str(exc)}
    else:
        logger.info("Storage Room sheet sync skipped (no LEGACY_WEBAPP_URL set).")
        results["storage"] = {"ok": True, "skipped": True}

    if sop_url:
        sop_payload = _build_payload_for_categories(db, SOP_CATEGORIES)
        try:
            _push_to_webapp(sop_url, sop_payload)
            results["sop"] = {"ok": True, "updated_at": sop_payload["updated_at"]}
        except Exception as exc:  # noqa: BLE001
            logger.exception("SOP Room sheet sync failed")
            results["sop"] = {"ok": False, "error": str(exc)}
    else:
        logger.info("SOP Room sheet sync skipped (no SOP_WEBAPP_URL set).")
        results["sop"] = {"ok": True, "skipped": True}

    overall_ok = results["storage"]["ok"] and results["sop"]["ok"]
    db.add(AuditEvent(
        event_type="sheet_sync_ok" if overall_ok else "sheet_sync_failed",
        detail=str(results),
    ))
    db.commit()
    return {"ok": overall_ok, **results}


def maybe_sync_after_mutation(db: Session) -> None:
    settings = get_settings()
    if (settings.legacy_webapp_url or "").strip() or (settings.sop_webapp_url or "").strip():
        sync_mirror(db)


def append_warranty_report(part_name: str, issue: str, reported_by: str, created_at) -> dict:
    """
    Warranty reports are a one-shot log entry, not tracked inventory state —
    so this appends a single row directly to the Storage Room Sheet's own
    "Warranty" tab, instead of going through the periodic full-rewrite sync.
    Always goes to the Storage Room sheet (LEGACY_WEBAPP_URL), never the SOP
    sheet — Warranty is a Storage Room feature only.
    """
    settings = get_settings()
    webapp_url = (settings.legacy_webapp_url or "").strip()
    if not webapp_url:
        logger.info("Warranty report not sent to Sheet (no LEGACY_WEBAPP_URL set).")
        return {"ok": True, "skipped": True}
    try:
        body = {
            "action": "appendWarranty",
            "timestamp": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
            "reported_by": reported_by,
            "part_name": part_name,
            "issue": issue,
        }
        resp = httpx.post(webapp_url, json=body, timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            raise RuntimeError(f"Apps Script rejected warranty append: {result.get('error')}")
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Warranty report sheet append failed")
        return {"ok": False, "error": str(exc)}
