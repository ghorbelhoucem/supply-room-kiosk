"""
Furniture — rooms.

Browse the furniture by ROOM: floor -> room -> what is in it, with amounts. Every change
(modify, add, remove, move between rooms) is written to the audit trail, and shows up in the
Furniture Google Sheet's History (see furniture_sync.py).

Data: furniture_rooms (floor + label), furniture_room_types (chair, large cabinet …, shared by
all rooms) and furniture_room_stock (how many of a type are in a room). A stock row exists only
while at least one is there: removing the last one deletes the row.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, FurnitureRoom, FurnitureRoomStock, FurnitureRoomType

MAX_QTY = 100000
SEED_FILE = Path(__file__).resolve().parent.parent / "furniture_rooms_seed.json"
_ROOM_NO = re.compile(r"^(\d)\.(\d{2})$")  # "2.05" = floor 2, room 05
_UNSET = object()
UNSET = _UNSET  # for callers that tell "not sent" apart from "sent as empty"


def _fail(code: str, error: str) -> dict:
    return {"ok": False, "code": code, "error": error}


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _uid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _audit(db: Session, kind: str, actor: str, detail: dict) -> None:
    db.add(AuditEvent(event_type=f"furniture_{kind}", actor=actor, detail=json.dumps(detail, ensure_ascii=False)))


def room_sort_key(room) -> tuple:
    """Floor-level areas first, then numbered rooms in number order."""
    m = _ROOM_NO.match(room.label)
    return (room.floor, 1 if m else 0, int(m.group(2)) if m else 0, room.label.lower())


# ---------------------------------------------------------------- read side
def snapshot_rooms(db: Session) -> dict:
    rooms = sorted(db.execute(select(FurnitureRoom)).scalars().all(), key=room_sort_key)
    types = sorted(db.execute(select(FurnitureRoomType)).scalars().all(), key=lambda t: t.name.lower())
    type_name = {t.id: t.name.lower() for t in types}
    stock: dict = {}
    for row in db.execute(select(FurnitureRoomStock)).scalars().all():
        stock.setdefault(row.room_id, []).append(row)
    return {
        "ok": True,
        "floors": sorted({r.floor for r in rooms}),
        "rooms": [
            {
                "id": str(r.id), "floor": r.floor, "label": r.label, "kind": r.kind, "note": r.note,
                "items": [
                    {"type_id": str(s.type_id), "qty": int(s.qty), "comment": s.comment}
                    for s in sorted(stock.get(r.id, []), key=lambda s: type_name.get(s.type_id, ""))
                ],
            }
            for r in rooms
        ],
        "types": [{"id": str(t.id), "name": t.name, "measure": t.measure, "note": t.note, "photo": t.photo} for t in types],
    }


# ------------------------------------------------------------------ helpers
def _room(db: Session, room_id) -> FurnitureRoom | None:
    uid = _uid(room_id)
    return db.get(FurnitureRoom, uid) if uid else None


def _type(db: Session, type_id) -> FurnitureRoomType | None:
    uid = _uid(type_id)
    return db.get(FurnitureRoomType, uid) if uid else None


def _row(db: Session, room_id, type_id, lock: bool = True) -> FurnitureRoomStock | None:
    q = select(FurnitureRoomStock).where(FurnitureRoomStock.room_id == room_id, FurnitureRoomStock.type_id == type_id)
    return db.execute(q.with_for_update() if lock else q).scalar_one_or_none()


def _valid_qty(qty, minimum: int = 1):
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < minimum or qty > MAX_QTY:
        return _fail("BAD_REQUEST", f"Enter a whole number from {minimum} to {MAX_QTY}.")
    return None


def _valid_label(floor: int, label: str):
    if not label:
        return _fail("BAD_REQUEST", "Give the room a number or a name.")
    if len(label) > 40:
        return _fail("BAD_REQUEST", "That room name is too long (40 characters max).")
    m = _ROOM_NO.match(label)
    if m and int(m.group(1)) != floor:
        return _fail("BAD_REQUEST", f"Room {label} belongs on floor {m.group(1)}, not floor {floor}.")
    return None


def _label_taken(db: Session, floor: int, label: str, exclude_id=None) -> bool:
    q = select(FurnitureRoom.id).where(FurnitureRoom.floor == floor, func.lower(FurnitureRoom.label) == label.lower())
    if exclude_id is not None:
        q = q.where(FurnitureRoom.id != exclude_id)
    return db.execute(q).first() is not None


def _type_by_name(db: Session, name: str, exclude_id=None) -> FurnitureRoomType | None:
    q = select(FurnitureRoomType).where(func.lower(FurnitureRoomType.name) == name.lower())
    if exclude_id is not None:
        q = q.where(FurnitureRoomType.id != exclude_id)
    return db.execute(q).scalars().first()


def _comment(value) -> str | None:
    c = " ".join(str(value or "").split())
    return c[:300] or None


# ------------------------------------------------------------- room records
def create_room(db: Session, *, actor: str, floor: int, label: str, note: str | None = None) -> dict:
    label = _clean(label)
    if not isinstance(floor, int) or not 1 <= floor <= 9:
        return _fail("BAD_REQUEST", "Pick a floor from 1 to 9.")
    bad = _valid_label(floor, label)
    if bad:
        return bad
    if _label_taken(db, floor, label):
        return _fail("DUPLICATE_ROOM", f'Floor {floor} already has "{label}".')
    room = FurnitureRoom(floor=floor, label=label, kind="room" if _ROOM_NO.match(label) else "area", note=_clean(note) or None)
    db.add(room)
    db.flush()
    _audit(db, "room_create", actor, {"floor": floor, "label": label})
    return {"ok": True, "room_id": str(room.id)}


def update_room(db: Session, room_id, *, actor: str, label=_UNSET, note=_UNSET) -> dict:
    room = _room(db, room_id)
    if room is None:
        return _fail("NOT_FOUND", "That room no longer exists.")
    before = {"label": room.label, "note": room.note}
    new_label = room.label if label is _UNSET or label is None else _clean(label)
    new_note = room.note if note is _UNSET else (_clean(note) or None)
    bad = _valid_label(room.floor, new_label)
    if bad:
        return bad
    if new_label.lower() != room.label.lower() and _label_taken(db, room.floor, new_label, exclude_id=room.id):
        return _fail("DUPLICATE_ROOM", f'Floor {room.floor} already has "{new_label}".')
    after = {"label": new_label, "note": new_note}
    if before == after:
        return {"ok": True, "unchanged": True, "room_id": str(room.id)}
    room.label, room.note = new_label, new_note
    room.kind = "room" if _ROOM_NO.match(new_label) else "area"
    db.flush()
    _audit(db, "room_edit", actor, {"floor": room.floor, "before": before, "after": after})
    return {"ok": True, "room_id": str(room.id)}


def delete_room(db: Session, room_id, *, actor: str) -> dict:
    room = _room(db, room_id)
    if room is None:
        return _fail("NOT_FOUND", "That room no longer exists.")
    if db.execute(select(func.count()).select_from(FurnitureRoomStock).where(FurnitureRoomStock.room_id == room.id)).scalar():
        return _fail("NOT_EMPTY", "That room still has furniture in it. Move or remove it first.")
    _audit(db, "room_delete", actor, {"floor": room.floor, "label": room.label})
    db.delete(room)
    return {"ok": True}


# ------------------------------------------------------------- item types
def update_type(db: Session, type_id, *, actor: str, name=_UNSET, measure=_UNSET, note=_UNSET) -> dict:
    t = _type(db, type_id)
    if t is None:
        return _fail("NOT_FOUND", "That item no longer exists.")
    before = {"name": t.name, "measure": t.measure, "note": t.note}
    new_name = t.name if name is _UNSET or name is None else _clean(name)
    if not new_name:
        return _fail("BAD_REQUEST", "Give the item a name.")
    if len(new_name) > 200:
        return _fail("BAD_REQUEST", "That name is too long (200 characters max).")
    if new_name.lower() != t.name.lower() and _type_by_name(db, new_name, exclude_id=t.id):
        return _fail("DUPLICATE_TYPE", f'There is already an item called "{new_name}".')
    after = {
        "name": new_name,
        "measure": t.measure if measure is _UNSET else (_clean(measure)[:200] or None),
        "note": t.note if note is _UNSET else (_clean(note) or None),
    }
    if before == after:
        return {"ok": True, "unchanged": True, "type_id": str(t.id)}
    t.name, t.measure, t.note = after["name"], after["measure"], after["note"]
    db.flush()
    _audit(db, "roomtype_edit", actor, {"before": before, "after": after})
    return {"ok": True, "type_id": str(t.id)}


# ------------------------------------------------------- stock in a room
def add_to_room(
    db: Session, room_id, *, actor: str, qty: int, type_id=None, new_type_name=None, new_type_measure=None, comment=None
) -> dict:
    """Put `qty` of a type into a room (added to what is already there). A brand-new type can be created on the way."""
    room = _room(db, room_id)
    if room is None:
        return _fail("NOT_FOUND", "That room no longer exists.")
    bad = _valid_qty(qty)
    if bad:
        return bad
    created_type = False
    if type_id:
        t = _type(db, type_id)
        if t is None:
            return _fail("NOT_FOUND", "That item no longer exists.")
    else:
        name = _clean(new_type_name)
        if not name:
            return _fail("BAD_REQUEST", "Pick an item, or give the new item a name.")
        if len(name) > 200:
            return _fail("BAD_REQUEST", "That name is too long (200 characters max).")
        t = _type_by_name(db, name)
        if t is None:
            t = FurnitureRoomType(name=name, measure=_clean(new_type_measure)[:200] or None)
            db.add(t)
            db.flush()
            created_type = True
    row = _row(db, room.id, t.id)
    c = _comment(comment)
    if row is None:
        row = FurnitureRoomStock(room_id=room.id, type_id=t.id, qty=qty, comment=c)
        db.add(row)
        before = 0
    else:
        before = row.qty
        if row.qty + qty > MAX_QTY:
            return _fail("BAD_REQUEST", f"That would be more than {MAX_QTY}.")
        row.qty += qty
        if c and c not in (row.comment or ""):
            row.comment = f"{row.comment} · {c}" if row.comment else c
    db.flush()
    _audit(db, "room_add", actor, {"room": room.label, "floor": room.floor, "type": t.name, "qty": qty, "before": before, "to": row.qty, "new_type": created_type})
    return {"ok": True, "room_id": str(room.id), "type_id": str(t.id), "qty": row.qty, "created_type": created_type}


def set_room_stock(db: Session, room_id, type_id, *, actor: str, qty: int, expected_qty=None, comment=_UNSET) -> dict:
    """MODIFY: set the amount (and optionally the comment) of a type in a room. 0 removes it. expected_qty guards against a concurrent edit."""
    room, t = _room(db, room_id), _type(db, type_id)
    if room is None or t is None:
        return _fail("NOT_FOUND", "That room or item no longer exists.")
    bad = _valid_qty(qty, 0)
    if bad:
        return bad
    row = _row(db, room.id, t.id)
    if row is None:
        return _fail("NOT_FOUND", f"{t.name} is no longer in room {room.label}.")
    if expected_qty is not None and expected_qty != row.qty:
        return {
            "ok": False, "code": "CONFLICT", "quantity": row.qty,
            "error": f"Someone else changed {t.name} in {room.label} to {row.qty} while you were editing. The latest number is shown — check it and save again.",
        }
    new_comment = row.comment if comment is _UNSET else _comment(comment)
    if qty == row.qty and new_comment == row.comment:
        return {"ok": True, "unchanged": True, "qty": row.qty}
    before, old_comment = row.qty, row.comment
    if qty == 0:
        db.delete(row)
        _audit(db, "room_remove", actor, {"room": room.label, "floor": room.floor, "type": t.name, "qty": before, "left": 0})
        db.flush()
        return {"ok": True, "qty": 0}
    row.qty, row.comment = qty, new_comment
    db.flush()
    _audit(db, "room_set", actor, {"room": room.label, "floor": room.floor, "type": t.name, "from": before, "to": qty,
                                   "comment_changed": new_comment != old_comment, "comment": new_comment})
    return {"ok": True, "qty": qty}


def remove_from_room(db: Session, room_id, type_id, *, actor: str, qty=None) -> dict:
    """Take `qty` (or all, when qty is None) of a type out of a room."""
    room, t = _room(db, room_id), _type(db, type_id)
    if room is None or t is None:
        return _fail("NOT_FOUND", "That room or item no longer exists.")
    row = _row(db, room.id, t.id)
    if row is None:
        return _fail("NOT_FOUND", f"{t.name} is no longer in room {room.label}.")
    take = row.qty if qty is None else qty
    bad = _valid_qty(take)
    if bad:
        return bad
    if take > row.qty:
        return _fail("NOT_ENOUGH", f"Room {room.label} only has {row.qty} × {t.name}.")
    left = row.qty - take
    if left == 0:
        db.delete(row)
    else:
        row.qty = left
    db.flush()
    _audit(db, "room_remove", actor, {"room": room.label, "floor": room.floor, "type": t.name, "qty": take, "left": left})
    return {"ok": True, "qty": left}


def move_between_rooms(db: Session, from_room_id, to_room_id, type_id, *, actor: str, qty: int, expected_from_qty=None) -> dict:
    """MOVE `qty` of a type from one room to another — all or nothing."""
    src, dst, t = _room(db, from_room_id), _room(db, to_room_id), _type(db, type_id)
    if src is None or dst is None or t is None:
        return _fail("NOT_FOUND", "That room or item no longer exists.")
    if src.id == dst.id:
        return _fail("BAD_REQUEST", "Pick a different room to move it to.")
    bad = _valid_qty(qty)
    if bad:
        return bad
    # lock in a fixed order so two simultaneous moves can never deadlock
    first, second = sorted([(src.id, "src"), (dst.id, "dst")], key=lambda p: str(p[0]))
    rows = {}
    for rid, tag in (first, second):
        rows[tag] = _row(db, rid, t.id)
    src_row, dst_row = rows["src"], rows["dst"]
    if src_row is None:
        return _fail("NOT_FOUND", f"{t.name} is no longer in room {src.label}.")
    if expected_from_qty is not None and expected_from_qty != src_row.qty:
        return {
            "ok": False, "code": "CONFLICT", "quantity": src_row.qty,
            "error": f"Someone else changed {t.name} in {src.label} to {src_row.qty} while you were choosing. The latest number is shown — check it and try again.",
        }
    if qty > src_row.qty:
        return _fail("NOT_ENOUGH", f"Room {src.label} only has {src_row.qty} × {t.name}.")
    carried = src_row.comment if qty == src_row.qty else None   # moving the last ones takes the note along
    left = src_row.qty - qty
    if left == 0:
        db.delete(src_row)
    else:
        src_row.qty = left
    if dst_row is None:
        dst_row = FurnitureRoomStock(room_id=dst.id, type_id=t.id, qty=qty, comment=carried)
        db.add(dst_row)
    else:
        dst_row.qty += qty
        if carried and not dst_row.comment:
            dst_row.comment = carried
    db.flush()
    _audit(db, "room_move", actor, {"type": t.name, "from_room": src.label, "from_floor": src.floor, "to_room": dst.label,
                                    "to_floor": dst.floor, "qty": qty, "from_left": left, "to_now": dst_row.qty})
    return {"ok": True, "from_left": left, "to_now": dst_row.qty}


# ------------------------------------------------------------- first-run import
def import_seed(db: Session, data: dict, actor: str = "import") -> dict:
    types: dict = {}
    for t in data.get("types", []):
        row = FurnitureRoomType(name=t["name"], measure=t.get("measure"), note=t.get("note"), photo=t.get("photo"))
        db.add(row)
        types[t["name"]] = row
    db.flush()
    n_rooms = n_rows = units = 0
    for r in data.get("rooms", []):
        room = FurnitureRoom(floor=r["floor"], label=r["label"], kind=r.get("kind", "room"), note=r.get("note"))
        db.add(room)
        db.flush()
        n_rooms += 1
        for it in r.get("items", []):
            db.add(FurnitureRoomStock(room_id=room.id, type_id=types[it["type"]].id, qty=it["qty"], comment=it.get("comment")))
            n_rows += 1
            units += it["qty"]
    db.flush()
    _audit(db, "room_import", actor, {"source": data.get("source"), "rooms": n_rooms, "types": len(types), "rows": n_rows, "units": units})
    return {"rooms": n_rooms, "types": len(types), "rows": n_rows, "units": units}


def seed_rooms_if_empty(db: Session) -> dict:
    """First start after this feature is deployed: load the spreadsheet's rooms. Never touches a database that already has rooms."""
    if db.execute(select(func.count()).select_from(FurnitureRoom)).scalar():
        return {"skipped": True}
    if not SEED_FILE.exists():
        return {"skipped": True, "reason": "no seed file"}
    result = import_seed(db, json.loads(SEED_FILE.read_text(encoding="utf-8")))
    db.commit()
    return result
