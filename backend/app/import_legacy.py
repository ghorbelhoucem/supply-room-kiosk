#!/usr/bin/env python3
"""One-time import from legacy Apps Script web app GET snapshot into Postgres.

Usage (inside API container or local venv):
  LEGACY_WEBAPP_URL=https://script.google.com/.../exec \
  DATABASE_URL=postgresql+psycopg2://supply:supply@localhost:5432/supply \
  python -m app.import_legacy
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

import httpx
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Checkout, InventoryItem, ItemCategory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("import_legacy")


def parse_dt(value: str | None):
    if not value or value in {"None", "N/A", "Not returned"}:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def run() -> None:
    url = os.environ.get("LEGACY_WEBAPP_URL") or os.environ.get("legacy_webapp_url")
    if not url:
        raise SystemExit("Set LEGACY_WEBAPP_URL to the Apps Script /exec endpoint")

    Base.metadata.create_all(bind=engine)
    logger.info("Fetching legacy snapshot from %s", url)
    data = httpx.get(url, timeout=60).json()
    inventory = data.get("inventory") or []
    history = data.get("history") or []

    db = SessionLocal()
    try:
        for row in inventory:
            name = row.get("item")
            if not name:
                continue
            category_raw = row.get("reference") or "Station Parts"
            category = (
                ItemCategory.tools
                if category_raw == "Tools"
                else ItemCategory.station_parts
            )
            qty = int(row.get("quantity") or 0)
            existing = db.execute(
                select(InventoryItem).where(InventoryItem.name == name)
            ).scalar_one_or_none()
            if existing:
                existing.qty_on_hand = qty
                existing.category = category
            else:
                sku = f"IMP-{uuid.uuid4().hex[:10].upper()}"
                db.add(
                    InventoryItem(
                        sku=sku,
                        name=name,
                        category=category,
                        qty_on_hand=qty,
                        reorder_min=3,
                        barcode=name,
                    )
                )

        db.flush()
        name_to_item = {
            it.name: it for it in db.execute(select(InventoryItem)).scalars().all()
        }

        imported = 0
        for h in history:
            tx_id = h.get("txId") or f"imp-{uuid.uuid4().hex[:12]}"
            exists = db.execute(select(Checkout).where(Checkout.tx_id == tx_id)).scalar_one_or_none()
            if exists:
                continue
            item = name_to_item.get(h.get("item"))
            if not item:
                continue
            returned_at = None
            returned_by = h.get("returnedBy")
            raw_returned = h.get("returnedAt")
            if raw_returned == "N/A":
                returned_at = datetime.utcnow()
                returned_by = "N/A"
            elif raw_returned and raw_returned != "Not returned":
                returned_at = parse_dt(raw_returned)

            db.add(
                Checkout(
                    tx_id=tx_id,
                    item_id=item.id,
                    person_role=h.get("personRole") or "Unknown",
                    qty=1,
                    taken_at=parse_dt(h.get("timestamp")) or datetime.utcnow(),
                    expected_return=parse_dt(h.get("expectedReturn")),
                    returned_at=returned_at,
                    returned_by=returned_by,
                )
            )
            imported += 1

        db.commit()
        logger.info("Imported %s inventory rows, %s history rows", len(inventory), imported)
    finally:
        db.close()


if __name__ == "__main__":
    run()
