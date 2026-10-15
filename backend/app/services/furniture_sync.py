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

import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent
from app.services import furniture as furn
from app.services import furniture_rooms as rooms_svc

logger = logging.getLogger(__name__)

# One push at a time. Each push reads the CURRENT database state only AFTER it
# gets the lock, so overlapping pushes can never leave an older snapshot behind.
_lock = threading.Lock()
_needs_sync = True  # True at start-up so the first tick fills the Sheet

# The Rooms tab goes in its OWN request (a lighter Apps Script run, and a failure there can't hold back the
# main tabs). It is re-sent only when the rooms data changed, and skipped for good if the script is too old
# to know the "roomsSync" action (the other tabs keep working either way).
_last_rooms_hash: str | None = None
_rooms_tab_supported = True

# Photos in the Sheet: =IMAGE("<base><path>") makes Google fetch the picture from the
# internet, so the photos must be reachable by anyone who has the address. Set
# FURNITURE_PHOTO_BASE_URL to the web address of the folder that CONTAINS "furniture/"
# (e.g. https://raw.githubusercontent.com/<user>/<repo>/main/images/). Empty = no Photo column.
_PATH_OK = re.compile(r"^[A-Za-z0-9._/\-]+$")
_warned_bad_base = False


def photo_base_url() -> str:
    """The validated photo address prefix (always ends with '/'), or '' when photos are off."""
    global _warned_bad_base
    base = (get_settings().furniture_photo_base_url or "").strip()
    if not base:
        return ""
    if not base.lower().startswith("https://") or any(ch in base for ch in " \t\r\n\"'<>()"):
        if not _warned_bad_base:
            logger.warning("FURNITURE_PHOTO_BASE_URL ignored: it must start with https:// and contain no spaces or quotes")
            _warned_bad_base = True
        return ""
    return base if base.endswith("/") else base + "/"


def _photo_cell(item: dict, base: str) -> str:
    """=IMAGE(...) for the item's first photo, or '' (never a formula built from odd text)."""
    path = ((item.get("images") or [""])[0] or "").lstrip("/")
    if not path or not _PATH_OK.match(path) or ".." in path:
        return ""
    return f'=IMAGE("{base}{path}")'


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


def _text_cell(value):
    """A room number like '3.10' must stay TEXT in a Sheet (it would become the number 3.1), so it gets an apostrophe."""
    if isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value):
        return "'" + value
    return _safe(value)


def _room_history(kind: str, d: dict) -> tuple | None:
    """(action, item, room/place, from, to, details) for the room events, or None if kind is not a room event."""
    room, typ = d.get("room", ""), d.get("type", "")
    if kind == "room_move":
        det = f"moved {d.get('qty')} × {typ} — {d.get('from_room')} now has {d.get('from_left')}, {d.get('to_room')} now has {d.get('to_now')}"
        return "Moved between rooms", typ, f"{d.get('from_room')} → {d.get('to_room')}", "", "", det
    if kind == "room_set":
        det = "; ".join(x for x in (f"comment: {d.get('comment') or '—'}" if d.get("comment_changed") else "",) if x)
        return "Room quantity changed", typ, room, d.get("from", ""), d.get("to", ""), det
    if kind == "room_add":
        det = ("new item type; " if d.get("new_type") else "") + f"added {d.get('qty')}"
        return "Added to room", typ, room, d.get("before", ""), d.get("to", ""), det
    if kind == "room_remove":
        return "Removed from room", typ, room, (d.get("left") or 0) + (d.get("qty") or 0), d.get("left", ""), f"removed {d.get('qty')}"
    if kind == "room_create":
        return "Room added", "", d.get("label", ""), "", "", f"floor {d.get('floor')}"
    if kind == "room_edit":
        b, a = d.get("before") or {}, d.get("after") or {}
        det = "; ".join(f"{k}: {b.get(k) or '—'} → {a.get(k) or '—'}" for k in ("label", "note") if b.get(k) != a.get(k))
        return "Room edited", "", a.get("label", ""), "", "", det
    if kind == "room_delete":
        return "Room deleted", "", d.get("label", ""), "", "", f"floor {d.get('floor')}"
    if kind == "roomtype_edit":
        b, a = d.get("before") or {}, d.get("after") or {}
        det = "; ".join(f"{k}: {b.get(k) or '—'} → {a.get(k) or '—'}" for k in ("name", "measure", "note") if b.get(k) != a.get(k))
        return "Item type edited", a.get("name", ""), "", "", "", det
    if kind == "room_import":
        det = f"{d.get('rooms')} rooms, {d.get('units')} units from {d.get('source')}"
        if "linked_items" in d:
            det += f" — {d.get('linked_items')} existing items linked, {d.get('new_items')} new items"
        else:
            det = f"{d.get('rooms')} rooms, {d.get('types')} item types, {d.get('units')} units from {d.get('source')}"
        return "Rooms imported", "", "", "", d.get("units", ""), det
    if kind == "room_migrate":
        return "Rooms linked to the catalog", "", "", "", d.get("units", ""), f"{d.get('from_rows')} room lines moved onto catalog items — {d.get('linked_items')} existing items linked, {d.get('new_items')} new items; amounts and edits kept"
    if kind == "merge":
        src, dst = d.get("source") or {}, d.get("target") or {}
        return "Items merged", dst.get("name", ""), "", "", "", f"{src.get('name')} ({src.get('sku')}) is the same as {dst.get('name')} ({dst.get('sku')}): {d.get('rooms')} rooms ({d.get('room_units')} units) and {d.get('building_units')} building units moved"
    return None


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
    elif _room_history(kind, d) is not None:
        action, name, building, frm, to, details = _room_history(kind, d)
        return [_safe(c) if i != 5 else _text_cell(c) for i, c in enumerate((when, ev.actor or "", action, "", name, building, frm, to, details))]
    else:
        action = kind.replace("_", " ").capitalize() or "Other"
        details = json.dumps(d, ensure_ascii=False)

    return [_safe(c) for c in (when, ev.actor or "", action, sku, name, building, frm, to, details)]


def build_payload(db: Session) -> dict:
    snap = furn.snapshot_furniture(db)
    locations = snap["locations"]
    order = {c["name"]: i for i, c in enumerate(snap["categories"])}
    indexed = sorted(enumerate(snap["items"]), key=lambda p: (order.get(p[1]["category"], 999), p[0]))

    base = photo_base_url()
    inventory_headers = (["Photo"] if base else []) + ["SKU", "Category", "Item"] + [loc["name"] for loc in locations] + ["Total", "Note"]
    inventory = [
        ([_photo_cell(it, base)] if base else [])  # deliberately NOT passed through _safe(): this one is meant to be a formula
        + [_safe(it["sku"]), _safe(it["category"]), _safe(it["name"])]
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
        "rooms_headers": ROOMS_HEADERS,
        "rooms": _rooms_rows(db),
    }


ROOMS_HEADERS = ["Floor", "Room", "SKU", "Item", "Category", "Amount", "Comment"]


def _rooms_rows(db: Session) -> list:
    """One row per (room, catalog item) — the SKU ties each row back to the Inventory tab."""
    rs = rooms_svc.snapshot_rooms(db)
    by_id = {t["id"]: t for t in rs["items"]}
    return [
        [r["floor"], _text_cell(r["label"]), _safe(by_id[i["item_id"]]["sku"]), _safe(by_id[i["item_id"]]["name"]), _safe(by_id[i["item_id"]]["category"]), int(i["qty"]), _safe(i["comment"] or "")]
        for r in rs["rooms"]
        for i in r["items"]
    ]


def _push_rooms(url: str, payload: dict) -> None:
    """Send the Rooms tab. Returns quietly if the Sheet's script predates it (it answers 'unknown action')."""
    global _last_rooms_hash, _rooms_tab_supported
    digest = hashlib.sha1(json.dumps(payload["rooms"], ensure_ascii=False).encode("utf-8")).hexdigest()
    if not _rooms_tab_supported or digest == _last_rooms_hash:
        return
    resp = httpx.post(url, json={"action": "roomsSync", "rooms_headers": payload["rooms_headers"], "rooms": payload["rooms"]}, timeout=20.0, follow_redirects=True)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        if result.get("error") == "unknown action":
            _rooms_tab_supported = False
            logger.warning("The Furniture Sheet's Apps Script does not know 'roomsSync' yet — update it to get the Rooms tab (the other tabs are fine).")
            return
        raise RuntimeError(f"Apps Script rejected the rooms sync: {result.get('error')}")
    _last_rooms_hash = digest


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
            _push_rooms(url, payload)
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
