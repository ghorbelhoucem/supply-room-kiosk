"""
Furniture Room -> its own Google Sheet.

A one-way MIRROR, like the SOP Room's: the app pushes its full current state to
a dedicated Apps Script web app (FURNITURE_WEBAPP_URL); the Sheet never writes
back, so editing the Sheet by hand changes nothing in the app and is overwritten
by the next push.

Two tabs are written:
  Inventory  one row per item, one column per building (+ Total, Note)
  History    every add / quantity change / edit / removal: when, who, what

When it runs
  * right after every successful change, in the BACKGROUND — the person saving
    never waits for Google (see routers/furniture.py)
  * on the scheduler tick, but only if something changed or the last push failed
    (so a flaky Google never loses an update, without hammering Apps Script)
  * once after every start-up (the flag starts "dirty"), which also fills a
    brand-new Sheet by itself
  * always, on a manual POST /api/sync/sheets

A push never raises: a failed sync must never break the person's real action.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent
from app.services import furniture as furn

logger = logging.getLogger(__name__)

# One push at a time. Each push reads the CURRENT database state only AFTER it
# gets the lock, so overlapping pushes can never leave an older snapshot behind.
_lock = threading.Lock()
_needs_sync = True  # True at start-up so the first tick fills the Sheet

HISTORY_HEADERS = ["Timestamp", "By", "Action", "SKU", "Item", "Building", "From", "To", "Details"]


def webapp_url() -> str:
    return (get_settings().furniture_webapp_url or "").strip()


def mark_dirty() -> None:
    global _needs_sync
    _needs_sync = True


def is_dirty() -> bool:
    return _needs_sync


def _safe(value):
    """
    Text that starts with = + - @ would be run as a FORMULA when written into a
    Sheet (e.g. a note of '=IMPORTXML(...)'). A leading apostrophe makes the
    Sheet keep it as plain text. Numbers pass through untouched.
    """
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _history_row(ev: AuditEvent) -> list:
    try:
        d = json.loads(ev.detail or "{}")
        if not isinstance(d, dict):
            d = {"detail": d}
    except (TypeError, ValueError):
        d = {"detail": ev.detail or ""}
    when = ev.created_at.isoformat() if ev.created_at else ""
    kind = (ev.event_type or "").removeprefix("furniture_")
    sku, name = d.get("sku", ""), d.get("name", "")
    building, frm, to, details = "", "", "", ""

    if kind == "add":
        action = "Added"
        stock = d.get("stock") or {}
        to = sum(v for v in stock.values() if isinstance(v, int))
        details = "; ".join([f"Category: {d.get('category', '')}"] + [f"{b}: {q}" for b, q in stock.items()])
    elif kind == "adjust":
        action = "Quantity changed"
        building, frm, to, details = d.get("location", ""), d.get("from", ""), d.get("to", ""), d.get("reason") or ""
    elif kind == "edit":
        action = "Edited"
        before, after = d.get("before") or {}, d.get("after") or {}
        name = after.get("name", name)
        details = "; ".join(
            f"{k}: {before.get(k) or '—'} → {after.get(k) or '—'}"
            for k in ("name", "category", "note")
            if before.get(k) != after.get(k)
        )
    elif kind == "remove":
        action = "Removed"
        stock = d.get("stock") or {}
        frm, to = sum(v for v in stock.values() if isinstance(v, int)), 0
        details = "; ".join([f"Category: {d.get('category', '')}"] + [f"{b}: {q}" for b, q in stock.items()])
    else:
        action = kind.replace("_", " ").capitalize() or "Other"
        details = json.dumps(d, ensure_ascii=False)

    return [_safe(c) for c in (when, ev.actor or "", action, sku, name, building, frm, to, details)]


def build_payload(db: Session) -> dict:
    snap = furn.snapshot_furniture(db)
    locations = snap["locations"]
    order = {c["name"]: i for i, c in enumerate(snap["categories"])}
    indexed = sorted(enumerate(snap["items"]), key=lambda p: (order.get(p[1]["category"], 999), p[0]))

    inventory_headers = ["SKU", "Category", "Item"] + [loc["name"] for loc in locations] + ["Total", "Note"]
    inventory = [
        [_safe(it["sku"]), _safe(it["category"]), _safe(it["name"])]
        + [int(it["stock"].get(loc["id"], 0)) for loc in locations]
        + [int(it["total"]), _safe(it["note"] or "")]
        for _, it in indexed
    ]

    events = (
        db.execute(
            select(AuditEvent).where(AuditEvent.event_type.like("furniture\\_%", escape="\\")).order_by(AuditEvent.created_at)
        )
        .scalars()
        .all()
    )
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "inventory_headers": [_safe(h) for h in inventory_headers],
        "inventory": inventory,
        "history_headers": HISTORY_HEADERS,
        "history": [_history_row(ev) for ev in events],
    }


def _push(url: str, payload: dict) -> None:
    body = {
        "action": "fullSync",
        "inventory_headers": payload["inventory_headers"],
        "inventory": payload["inventory"],
        "history_headers": payload["history_headers"],
        "history": payload["history"],
    }
    resp = httpx.post(url, json=body, timeout=20.0, follow_redirects=True)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Apps Script rejected the sync: {result.get('error')}")


def sync_furniture(db: Session | None = None) -> dict:
    """Push the current state NOW. Never raises. Pass a session, or None to use a private one."""
    global _needs_sync
    url = webapp_url()
    if not url:
        return {"ok": True, "skipped": True, "reason": "FURNITURE_WEBAPP_URL is not set"}
    with _lock:
        own = db is None
        session = SessionLocal() if own else db
        try:
            _needs_sync = False  # cleared BEFORE reading: a change made during the push sets it again
            payload = build_payload(session)
            _push(url, payload)
            return {
                "ok": True,
                "updated_at": payload["updated_at"],
                "items": len(payload["inventory"]),
                "history": len(payload["history"]),
            }
        except Exception as exc:  # noqa: BLE001 — a failed mirror must never break a real action
            _needs_sync = True
            logger.exception("Furniture sheet sync failed")
            return {"ok": False, "error": str(exc)}
        finally:
            if own:
                session.close()


def sync_if_needed(db: Session | None = None) -> dict:
    if not webapp_url():
        return {"ok": True, "skipped": True, "reason": "FURNITURE_WEBAPP_URL is not set"}
    if not _needs_sync:
        return {"ok": True, "unchanged": True}
    return sync_furniture(db)


def sync_in_background() -> None:
    """Entry point for FastAPI BackgroundTasks (runs after the response is sent)."""
    sync_furniture(None)
