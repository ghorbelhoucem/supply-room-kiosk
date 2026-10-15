"""
Furniture — rooms.

Browse the furniture by ROOM: floor -> room -> what is in it, with amounts. A room does not have its own
list of things: every line is a CATALOG item (furniture_items — the same record the buildings use) plus a
room and an amount. So "Round table" is ONE item, counted in the buildings and located in rooms.

Every change (modify, add, remove, move between rooms, merge two items) goes to the audit trail and shows
up in the Furniture Google Sheet's History. A row exists only while at least one of the item is in the
room: removing the last one deletes the row.

Start-up (seed_rooms_if_empty):
  * empty database       -> import the room survey (furniture_rooms_seed.json): each survey line is linked
                            to the catalog item it is, or becomes a new catalog item when it can't be placed
  * first version found  -> migrate its rooms/amounts/edits onto catalog items (the old tables stay as a backup)
"""

from __future__ import annotations

import collections
import json
import logging
import re
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    FurnitureItem,
    FurnitureRoom,
    FurnitureRoomItem,
    FurnitureRoomStock,
    FurnitureRoomType,
    FurnitureStock,
    Location,
)
from app.services import furniture as furn

logger = logging.getLogger(__name__)

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
    items = sorted(
        db.execute(select(FurnitureItem).where(FurnitureItem.is_active.is_(True))).scalars().all(), key=lambda i: i.name.lower()
    )
    name_of = {i.id: i.name.lower() for i in items}
    loc_name = {loc.id: loc.name for loc in db.execute(select(Location)).scalars().all()}
    buildings: dict = {}
    for s in db.execute(select(FurnitureStock)).scalars().all():
        if s.qty > 0 and s.location_id in loc_name:
            buildings.setdefault(s.item_id, {})[loc_name[s.location_id]] = int(s.qty)
    stock: dict = {}
    for row in db.execute(select(FurnitureRoomItem)).scalars().all():
        if row.item_id in name_of:  # an item that was removed from the catalog never shows up
            stock.setdefault(row.room_id, []).append(row)
    known = [name for name, _, _ in furn.CATEGORY_META]
    extra = sorted({i.category for i in items} - set(known))
    return {
        "ok": True,
        "floors": sorted({r.floor for r in rooms}),
        "rooms": [
            {
                "id": str(r.id), "floor": r.floor, "label": r.label, "kind": r.kind, "note": r.note,
                "items": [
                    {"item_id": str(s.item_id), "qty": int(s.qty), "comment": s.comment}
                    for s in sorted(stock.get(r.id, []), key=lambda s: name_of[s.item_id])
                ],
            }
            for r in rooms
        ],
        "items": [
            {
                "id": str(i.id), "sku": i.sku, "name": i.name, "category": i.category, "note": i.note,
                "photo": (i.image_paths or [None])[0], "buildings": buildings.get(i.id, {}),
            }
            for i in items
        ],
        "categories": known + extra,
    }


def room_index(db: Session) -> dict:
    """item id -> the rooms it is in (for the catalog's item screen: 'In rooms')."""
    rooms = {r.id: r for r in db.execute(select(FurnitureRoom)).scalars().all()}
    out: dict = {}
    for row in db.execute(select(FurnitureRoomItem)).scalars().all():
        r = rooms.get(row.room_id)
        if r is not None:
            out.setdefault(str(row.item_id), []).append((r, int(row.qty)))
    return {
        k: [{"room_id": str(r.id), "label": r.label, "floor": r.floor, "kind": r.kind, "qty": q} for r, q in sorted(v, key=lambda p: room_sort_key(p[0]))]
        for k, v in out.items()
    }


def room_usage(db: Session, item_id) -> tuple[int, int]:
    """(how many rooms, how many units) of a catalog item sit in rooms."""
    uid = _uid(item_id)
    if uid is None:
        return 0, 0
    n, units = db.execute(
        select(func.count(), func.coalesce(func.sum(FurnitureRoomItem.qty), 0)).where(FurnitureRoomItem.item_id == uid)
    ).one()
    return int(n), int(units)


# ------------------------------------------------------------------ helpers
def _room(db: Session, room_id) -> FurnitureRoom | None:
    uid = _uid(room_id)
    return db.get(FurnitureRoom, uid) if uid else None


def _item(db: Session, item_id) -> FurnitureItem | None:
    uid = _uid(item_id)
    it = db.get(FurnitureItem, uid) if uid else None
    return it if it is not None and it.is_active else None


def _row(db: Session, room_id, item_id, lock: bool = True) -> FurnitureRoomItem | None:
    q = select(FurnitureRoomItem).where(FurnitureRoomItem.room_id == room_id, FurnitureRoomItem.item_id == item_id)
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


def _active_by_name(db: Session, name: str) -> FurnitureItem | None:
    return db.execute(
        select(FurnitureItem).where(func.lower(FurnitureItem.name) == name.lower(), FurnitureItem.is_active.is_(True))
    ).scalars().first()


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
    if db.execute(select(func.count()).select_from(FurnitureRoomItem).where(FurnitureRoomItem.room_id == room.id)).scalar():
        return _fail("NOT_EMPTY", "That room still has furniture in it. Move or remove it first.")
    _audit(db, "room_delete", actor, {"floor": room.floor, "label": room.label})
    db.delete(room)
    return {"ok": True}


# ------------------------------------------------------- stock in a room
def add_to_room(
    db: Session, room_id, *, actor: str, qty: int, item_id=None, new_name=None, new_category=None, new_note=None, comment=None
) -> dict:
    """Put `qty` of a catalog item into a room (added to what is already there).
    A name that already exists in the catalog reuses that item — it never makes a second copy."""
    room = _room(db, room_id)
    if room is None:
        return _fail("NOT_FOUND", "That room no longer exists.")
    bad = _valid_qty(qty)
    if bad:
        return bad
    created = False
    if item_id:
        item = _item(db, item_id)
        if item is None:
            return _fail("NOT_FOUND", "That item no longer exists.")
    else:
        name = _clean(new_name)
        if not name:
            return _fail("BAD_REQUEST", "Pick an item, or give the new item a name.")
        item = _active_by_name(db, name)
        if item is None:
            if not _clean(new_category):
                return _fail("BAD_REQUEST", "Pick a category for the new item.")
            res = furn.create_item(db, name=name, category=_clean(new_category), note=(new_note or "").strip() or None, stock={}, actor=actor)
            if not res.get("ok"):
                return res
            item = db.get(FurnitureItem, _uid(res["item"]["id"]))
            created = True
    row = _row(db, room.id, item.id)
    c = _comment(comment)
    if row is None:
        row = FurnitureRoomItem(room_id=room.id, item_id=item.id, qty=qty, comment=c)
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
    _audit(db, "room_add", actor, {"room": room.label, "floor": room.floor, "type": item.name, "sku": item.sku, "qty": qty, "before": before, "to": row.qty, "new_type": created})
    return {"ok": True, "room_id": str(room.id), "item_id": str(item.id), "qty": row.qty, "created_item": created}


def set_room_stock(db: Session, room_id, item_id, *, actor: str, qty: int, expected_qty=None, comment=_UNSET) -> dict:
    """MODIFY: set the amount (and optionally the comment) of an item in a room. 0 removes it. expected_qty guards against a concurrent edit."""
    room, item = _room(db, room_id), _item(db, item_id)
    if room is None or item is None:
        return _fail("NOT_FOUND", "That room or item no longer exists.")
    bad = _valid_qty(qty, 0)
    if bad:
        return bad
    row = _row(db, room.id, item.id)
    if row is None:
        return _fail("NOT_FOUND", f"{item.name} is no longer in room {room.label}.")
    if expected_qty is not None and expected_qty != row.qty:
        return {
            "ok": False, "code": "CONFLICT", "quantity": row.qty,
            "error": f"Someone else changed {item.name} in {room.label} to {row.qty} while you were editing. The latest number is shown — check it and save again.",
        }
    new_comment = row.comment if comment is _UNSET else _comment(comment)
    if qty == row.qty and new_comment == row.comment:
        return {"ok": True, "unchanged": True, "qty": row.qty}
    before, old_comment = row.qty, row.comment
    if qty == 0:
        db.delete(row)
        _audit(db, "room_remove", actor, {"room": room.label, "floor": room.floor, "type": item.name, "sku": item.sku, "qty": before, "left": 0})
        db.flush()
        return {"ok": True, "qty": 0}
    row.qty, row.comment = qty, new_comment
    db.flush()
    _audit(db, "room_set", actor, {"room": room.label, "floor": room.floor, "type": item.name, "sku": item.sku, "from": before, "to": qty,
                                   "comment_changed": new_comment != old_comment, "comment": new_comment})
    return {"ok": True, "qty": qty}


def remove_from_room(db: Session, room_id, item_id, *, actor: str, qty=None) -> dict:
    """Take `qty` (or all, when qty is None) of an item out of a room."""
    room, item = _room(db, room_id), _item(db, item_id)
    if room is None or item is None:
        return _fail("NOT_FOUND", "That room or item no longer exists.")
    row = _row(db, room.id, item.id)
    if row is None:
        return _fail("NOT_FOUND", f"{item.name} is no longer in room {room.label}.")
    take = row.qty if qty is None else qty
    bad = _valid_qty(take)
    if bad:
        return bad
    if take > row.qty:
        return _fail("NOT_ENOUGH", f"Room {room.label} only has {row.qty} × {item.name}.")
    left = row.qty - take
    if left == 0:
        db.delete(row)
    else:
        row.qty = left
    db.flush()
    _audit(db, "room_remove", actor, {"room": room.label, "floor": room.floor, "type": item.name, "sku": item.sku, "qty": take, "left": left})
    return {"ok": True, "qty": left}


def move_between_rooms(db: Session, from_room_id, to_room_id, item_id, *, actor: str, qty: int, expected_from_qty=None) -> dict:
    """MOVE `qty` of an item from one room to another — all or nothing."""
    src, dst, item = _room(db, from_room_id), _room(db, to_room_id), _item(db, item_id)
    if src is None or dst is None or item is None:
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
        rows[tag] = _row(db, rid, item.id)
    src_row, dst_row = rows["src"], rows["dst"]
    if src_row is None:
        return _fail("NOT_FOUND", f"{item.name} is no longer in room {src.label}.")
    if expected_from_qty is not None and expected_from_qty != src_row.qty:
        return {
            "ok": False, "code": "CONFLICT", "quantity": src_row.qty,
            "error": f"Someone else changed {item.name} in {src.label} to {src_row.qty} while you were choosing. The latest number is shown — check it and try again.",
        }
    if qty > src_row.qty:
        return _fail("NOT_ENOUGH", f"Room {src.label} only has {src_row.qty} × {item.name}.")
    carried = src_row.comment if qty == src_row.qty else None   # moving the last ones takes the note along
    left = src_row.qty - qty
    if left == 0:
        db.delete(src_row)
    else:
        src_row.qty = left
    if dst_row is None:
        dst_row = FurnitureRoomItem(room_id=dst.id, item_id=item.id, qty=qty, comment=carried)
        db.add(dst_row)
    else:
        dst_row.qty += qty
        if carried and not dst_row.comment:
            dst_row.comment = carried
    db.flush()
    _audit(db, "room_move", actor, {"type": item.name, "sku": item.sku, "from_room": src.label, "from_floor": src.floor, "to_room": dst.label,
                                    "to_floor": dst.floor, "qty": qty, "from_left": left, "to_now": dst_row.qty})
    return {"ok": True, "from_left": left, "to_now": dst_row.qty}


# ----------------------------------------------------------- merge two items
def merge_items(db: Session, source_id, target_id, *, actor: str) -> dict:
    """The SOURCE item is the same thing as the TARGET: its rooms and building counts move onto the target
    and the source leaves the catalog. Nothing is lost, nothing is counted twice."""
    src, dst = _item(db, source_id), _item(db, target_id)
    if src is None or dst is None:
        return _fail("NOT_FOUND", "That item no longer exists.")
    if src.id == dst.id:
        return _fail("BAD_REQUEST", "Pick a different item to merge into.")
    moved_rooms = moved_units = 0
    for row in db.execute(select(FurnitureRoomItem).where(FurnitureRoomItem.item_id == src.id).with_for_update()).scalars().all():
        existing = _row(db, row.room_id, dst.id)
        qty, comment = row.qty, row.comment
        db.delete(row)
        db.flush()
        if existing is None:
            db.add(FurnitureRoomItem(room_id=row.room_id, item_id=dst.id, qty=qty, comment=comment))
        else:
            existing.qty += qty
            if comment and comment not in (existing.comment or ""):
                existing.comment = f"{existing.comment} · {comment}" if existing.comment else comment
        moved_rooms += 1
        moved_units += qty
    building_units = 0
    for s in db.execute(select(FurnitureStock).where(FurnitureStock.item_id == src.id)).scalars().all():
        if s.qty > 0:
            t = db.get(FurnitureStock, (dst.id, s.location_id))
            if t is None:
                db.add(FurnitureStock(item_id=dst.id, location_id=s.location_id, qty=s.qty))
            else:
                t.qty += s.qty
            building_units += s.qty
        s.qty = 0
    src.is_active = False
    db.flush()
    _audit(db, "merge", actor, {"source": {"sku": src.sku, "name": src.name}, "target": {"sku": dst.sku, "name": dst.name},
                                "rooms": moved_rooms, "room_units": moved_units, "building_units": building_units})
    return {"ok": True, "target_id": str(dst.id), "rooms_moved": moved_rooms, "units_moved": moved_units}


# ------------------------------------------------ catalog linking (import + migration)
_CATEGORY_GUESS = [
    (r"robot|franka|pedestal|embodiment", "Robots & Stations"), (r"printer|monitor|screen|pc\b|keyboard|mouse", "IT"),
    (r"whiteboard|\btv\b|partition", "Whiteboards & Displays"), (r"chair|stool|sofa|couch|bench", "Chairs & Seating"),
    (r"shelf|cabinet|locker|wardrobe", "Storage & Shelving"), (r"desk|table", "Tables & Desks"),
    (r"kitchen|fridge|coffee|kettle|microwave", "Kitchen & Appliances"), (r"trolley|pallet|platform", "Transport & Logistics"),
]


def guess_category(name: str) -> str:
    n = str(name or "").lower()
    for pattern, category in _CATEGORY_GUESS:
        if re.search(pattern, n):
            return category
    return "Office Essentials"


def _load_seed() -> dict | None:
    if not SEED_FILE.exists():
        return None
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


class _Resolver:
    """Turns survey kinds / catalog SKUs into catalog items: links to what exists, creates only what does not."""

    def __init__(self, db: Session, seed: dict, actor: str):
        self.db, self.seed, self.actor = db, seed, actor
        self.by_sku = {i.sku: i for i in db.execute(select(FurnitureItem).where(FurnitureItem.is_active.is_(True))).scalars().all()}
        self.by_name = {i.name.lower(): i for i in self.by_sku.values()}
        self.created: dict = {}
        self.linked: set = set()

    def _make(self, name: str, category: str, note: str | None) -> FurnitureItem:
        res = furn.create_item(self.db, name=name, category=category, note=note, stock={}, actor=self.actor)
        if not res.get("ok"):
            raise RuntimeError(f"could not create catalog item {name!r}: {res}")
        item = self.db.get(FurnitureItem, _uid(res["item"]["id"]))
        self.by_sku[item.sku], self.by_name[item.name.lower()] = item, item
        self.created[item.sku] = item
        return item

    def sku(self, sku: str) -> FurnitureItem:
        item = self.by_sku.get(sku)
        if item is None:  # the catalog item was deleted or never loaded: do not fail the import — make it from the hint in the seed
            hint = (self.seed.get("catalog") or {}).get(sku) or {"name": sku, "category": "Office Essentials"}
            item = self.by_name.get(hint["name"].lower()) or self._make(hint["name"], hint["category"], "Added from the room survey (its catalog entry was missing).")
        if item.sku not in self.created:  # "linked" means it already existed; something we had to create counts as new, not as both
            self.linked.add(item.sku)
        return item

    def kind(self, kind: str) -> FurnitureItem:
        link = (self.seed.get("links") or {}).get(kind)
        if link and link.get("sku"):
            return self.sku(link["sku"])
        spec = (self.seed.get("new_items") or {}).get(kind)
        if spec:
            item = self.by_name.get(spec["name"].lower())
            if item is None:
                return self._make(spec["name"], spec["category"], spec.get("note"))
            if item.sku not in self.created:  # the catalog already has an item with this very name: link to it, never duplicate
                self.linked.add(item.sku)
            return item
        # a kind the survey never had (e.g. typed in by someone on the first version): the same name in the catalog, or a new item
        name = _clean(kind)
        item = self.by_name.get(name.lower())
        if item is not None:
            self.linked.add(item.sku)
            return item
        return self._make(name[:1].upper() + name[1:], guess_category(name), "Added from the room survey. Check it is not a duplicate — if it is, use Merge.")


class _Acc:
    """One (room, item) line while importing: several survey lines can land on the same catalog item."""

    def __init__(self):
        self.qty, self.comments, self.variants = 0, [], collections.Counter()

    def add(self, qty: int, comment: str | None, variant: str | None = None):
        self.qty += qty
        if comment and comment not in self.comments:
            self.comments.append(comment)
        if variant:
            self.variants[variant] += qty

    def comment(self) -> str | None:
        parts = [f"{v} ×{n}" if len(self.variants) > 1 else v for v, n in self.variants.items()] + self.comments
        return " · ".join(parts)[:300] or None


def import_seed(db: Session, seed: dict, actor: str = "import") -> dict:
    res = _Resolver(db, seed, actor)
    n_rooms = units = n_rows = 0
    for r in seed.get("rooms", []):
        room = FurnitureRoom(floor=r["floor"], label=r["label"], kind=r.get("kind", "room"), note=r.get("note"))
        db.add(room)
        db.flush()
        n_rooms += 1
        acc: dict = {}
        for it in r.get("items", []):
            item = res.sku(it["sku"]) if it.get("sku") else res.kind(it["new"])
            acc.setdefault(item.id, _Acc()).add(it["qty"], it.get("comment"))
        for item_id, a in acc.items():
            db.add(FurnitureRoomItem(room_id=room.id, item_id=item_id, qty=a.qty, comment=a.comment()))
            n_rows += 1
            units += a.qty
    db.flush()
    stats = {"rooms": n_rooms, "rows": n_rows, "units": units, "linked_items": len(res.linked), "new_items": len(res.created)}
    _audit(db, "room_import", actor, {"source": seed.get("source"), **stats})
    return stats


def migrate_legacy_rooms(db: Session, actor: str = "migration") -> dict:
    """First version -> catalog items. Copies the CURRENT rooms/amounts/comments (so every edit people made is kept);
    the old tables are left untouched as a backup."""
    if db.execute(select(func.count()).select_from(FurnitureRoomItem)).scalar():
        return {"skipped": True}
    if db.execute(select(AuditEvent.id).where(AuditEvent.event_type == "furniture_room_migrate")).first():
        return {"skipped": True}
    legacy = db.execute(select(FurnitureRoomStock)).scalars().all()
    if not legacy:
        return {"skipped": True}
    seed = _load_seed() or {}
    types = {t.id: t for t in db.execute(select(FurnitureRoomType)).scalars().all()}
    rooms = {r.id: r for r in db.execute(select(FurnitureRoom)).scalars().all()}
    res = _Resolver(db, seed, actor)
    overrides = seed.get("row_overrides") or {}
    links = seed.get("links") or {}
    acc: dict = {}
    for row in legacy:
        t, room = types.get(row.type_id), rooms.get(row.room_id)
        if t is None or room is None:
            continue
        sku = overrides.get(f"{room.label}|{t.name}")
        item = res.sku(sku) if sku else res.kind(t.name)
        variant = (links.get(t.name) or {}).get("variant")
        acc.setdefault((room.id, item.id), _Acc()).add(row.qty, row.comment, variant)
    n_rows = units = 0
    for (room_id, item_id), a in acc.items():
        db.add(FurnitureRoomItem(room_id=room_id, item_id=item_id, qty=a.qty, comment=a.comment()))
        n_rows += 1
        units += a.qty
    db.flush()
    stats = {"rooms": len({k[0] for k in acc}), "rows": n_rows, "units": units, "from_rows": len(legacy), "linked_items": len(res.linked), "new_items": len(res.created)}
    _audit(db, "room_migrate", actor, stats)
    return stats


def seed_rooms_if_empty(db: Session) -> dict:
    """Called at every start-up; safe to repeat. Never touches rooms that already use catalog items."""
    try:
        if not db.execute(select(func.count()).select_from(FurnitureRoom)).scalar():
            seed = _load_seed()
            if not seed:
                return {"skipped": True, "reason": "no seed file"}
            result = import_seed(db, seed)
        else:
            result = migrate_legacy_rooms(db)
        db.commit()
        return result
    except Exception as exc:  # a problem here must never stop the app from starting
        db.rollback()
        logger.exception("Furniture rooms set-up failed")
        return {"skipped": True, "error": str(exc)}
