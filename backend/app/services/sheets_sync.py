"""Google Sheets mirror sync for purchasing view.

When GOOGLE_SHEET_SYNC_ENABLED is false (default), sync is a no-op that still
builds the payload so exports and logs work. Enable with a service account JSON
and sheet ID in env to push live tabs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditEvent, Checkout, InventoryItem, Movement
from app.services.inventory import available_qty

logger = logging.getLogger(__name__)


def build_mirror_payload(db: Session) -> dict:
    items = db.execute(select(InventoryItem).order_by(InventoryItem.name)).scalars().all()
    inventory_rows = []
    for it in items:
        avail = available_qty(db, it)
        inventory_rows.append(
            [
                it.sku,
                it.name,
                it.category.value,
                it.qty_on_hand,
                avail,
                it.reorder_min,
                "YES" if avail <= it.reorder_min else "NO",
                it.barcode or "",
            ]
        )

    opens = db.execute(select(Checkout).where(Checkout.returned_at.is_(None))).scalars().all()
    open_rows = []
    for c in opens:
        item = db.get(InventoryItem, c.item_id)
        open_rows.append(
            [
                c.tx_id,
                item.name if item else "",
                c.person_role,
                c.taken_at.isoformat() if c.taken_at else "",
                c.expected_return.isoformat() if c.expected_return else "",
            ]
        )

    moves = (
        db.execute(select(Movement).order_by(Movement.created_at.desc()).limit(300)).scalars().all()
    )
    move_rows = [
        [
            m.created_at.isoformat() if m.created_at else "",
            m.movement_type.value,
            m.item_name,
            m.qty,
            m.actor,
            m.reason or "",
        ]
        for m in moves
    ]

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "inventory": {
            "headers": [
                "SKU",
                "Item",
                "Category",
                "Qty On Hand",
                "Available",
                "Reorder Min",
                "Below Min",
                "Barcode",
            ],
            "rows": inventory_rows,
        },
        "open_checkouts": {
            "headers": ["TxId", "Item", "Taken By", "Taken At", "Expected Return"],
            "rows": open_rows,
        },
        "movements": {
            "headers": ["When", "Type", "Item", "Qty", "Actor", "Reason"],
            "rows": move_rows,
        },
    }


def _write_sheet_values(sheet_id: str, service_account_json: str, payload: dict) -> None:
    # Lazy import so API boots without Google libs when sync is disabled.
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = json.loads(service_account_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    def upsert_tab(title: str, headers: list, rows: list) -> None:
        values = [headers] + rows
        # Clear + write a fixed range; create sheet if missing is out of scope —
        # purchasing workbook should pre-create tabs Inventory / OpenCheckouts / Movements.
        range_name = f"{title}!A1"
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id, range=f"{title}!A:Z"
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=range_name,
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

    upsert_tab("Inventory", payload["inventory"]["headers"], payload["inventory"]["rows"])
    upsert_tab(
        "OpenCheckouts",
        payload["open_checkouts"]["headers"],
        payload["open_checkouts"]["rows"],
    )
    upsert_tab("Movements", payload["movements"]["headers"], payload["movements"]["rows"])


def sync_mirror(db: Session) -> dict:
    settings = get_settings()
    payload = build_mirror_payload(db)
    if not settings.google_sheet_sync_enabled:
        logger.info("Sheet sync skipped (disabled). rows=%s", len(payload["inventory"]["rows"]))
        return {"ok": True, "skipped": True, "payload_preview_rows": len(payload["inventory"]["rows"])}

    if not settings.google_sheet_id or not settings.google_service_account_json:
        db.add(
            AuditEvent(
                event_type="sheet_sync_failed",
                detail="Missing GOOGLE_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON",
            )
        )
        db.commit()
        return {"ok": False, "error": "Sheet sync misconfigured"}

    try:
        _write_sheet_values(
            settings.google_sheet_id, settings.google_service_account_json, payload
        )
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
    if settings.google_sheet_sync_enabled:
        sync_mirror(db)
