from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import User, UserRole
from app.services import furniture as furn
from app.services import furniture_rooms as rooms
from app.services import furniture_sync
from app.services import inventory as inv

router = APIRouter(tags=["furniture"])


# The WHOLE Furniture Room — browsing and changing — is for these roles only.
# Supervisors and Tele-operators (the operator logins) have no access at all.
FURNITURE_ROLES = {UserRole.management, UserRole.maintenance, UserRole.devs}


def require_furniture_access(user: User = Depends(get_current_user)) -> User:
    if user.role not in FURNITURE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Management, Maintenance or Devs can use Furniture.",
        )
    return user


class StockChange(BaseModel):
    location_id: str
    qty: int = Field(..., ge=0, le=furn.MAX_QTY)
    expected: int | None = Field(default=None, ge=0)


class StockRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    changes: list[StockChange] = Field(..., min_length=1)
    reason: str | None = Field(default=None, max_length=200)


class ItemCreateRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    stock: dict[str, int] = Field(default_factory=dict)


class ItemUpdateRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=2000)


def _actor(user: User) -> str:
    return f"{user.name}/{user.role.value}"


def _finish(db: Session, request_id: str | None, action: str, result: dict, background: BackgroundTasks | None = None) -> dict:
    if not result.get("ok"):
        db.rollback()
        return result
    if request_id:
        inv.save_idempotent(db, request_id, action, result)
    db.commit()
    # mirror to the Google Sheet AFTER the response is sent, and only for real changes
    if background is not None and not result.get("unchanged") and not result.get("already_removed"):
        furniture_sync.mark_dirty()
        background.add_task(furniture_sync.sync_in_background)
    return result


@router.get("/furniture")
def get_furniture(db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    """Reading needs a signed-in Management / Maintenance / Devs user, same as changing."""
    data = furn.snapshot_furniture(db)
    data["rooms_index"] = rooms.room_index(db)  # item id -> the rooms it is in, for the item screen
    return data


@router.post("/furniture/items")
def create_item(body: ItemCreateRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = furn.create_item(
        db, name=body.name, category=body.category, note=body.note, stock=body.stock, actor=_actor(user)
    )
    return _finish(db, body.client_request_id, "furniture_create", result, background)


@router.patch("/furniture/items/{item_id}")
def update_item(item_id: str, body: ItemUpdateRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    fields = {k: getattr(body, k) for k in ("name", "category", "note") if k in body.model_fields_set}
    result = furn.update_item(db, item_id, fields=fields, actor=_actor(user))
    return _finish(db, body.client_request_id, "furniture_update", result, background)


@router.patch("/furniture/items/{item_id}/stock")
def adjust_stock(item_id: str, body: StockRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = furn.adjust_stock(
        db, item_id, changes=[c.model_dump() for c in body.changes], reason=body.reason, actor=_actor(user)
    )
    return _finish(db, body.client_request_id, "furniture_stock", result, background)


@router.delete("/furniture/items/{item_id}")
def remove_item(item_id: str, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    """Soft delete: hides the item everywhere, keeps its history. Safe to call twice. An item that is still in a room is refused
    (its units would silently vanish from the rooms) — move it out first, or merge it into another item."""
    n_rooms, units = rooms.room_usage(db, item_id)
    if n_rooms:
        return _finish(db, None, "furniture_remove", {
            "ok": False, "code": "IN_ROOMS",
            "error": f"This item is still in {n_rooms} room{'s' if n_rooms != 1 else ''} ({units} unit{'s' if units != 1 else ''}). Remove it from the rooms first, or merge it into another item.",
        }, background)
    result = furn.deactivate_item(db, item_id, actor=_actor(user))
    return _finish(db, None, "furniture_remove", result, background)


# ======================================================================== rooms
# Browse the furniture by floor -> room. Same access rule as everything above. A room line is a CATALOG item
# (furniture_items) + a room + an amount — never a separate copy of the item.
class RoomCreateRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    floor: int = Field(..., ge=1, le=9)
    label: str = Field(..., min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=500)


class RoomUpdateRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    label: str | None = Field(default=None, min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=500)


class RoomItemAddRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    item_id: str | None = None
    new_name: str | None = Field(default=None, max_length=200)       # a name that is already in the catalog reuses that item
    new_category: str | None = Field(default=None, max_length=80)
    new_note: str | None = Field(default=None, max_length=2000)
    qty: int = Field(..., ge=1, le=rooms.MAX_QTY)
    comment: str | None = Field(default=None, max_length=300)


class RoomItemSetRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    qty: int = Field(..., ge=0, le=rooms.MAX_QTY)
    expected_qty: int | None = Field(default=None, ge=0)
    comment: str | None = Field(default=None, max_length=300)


class RoomItemRemoveRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    qty: int | None = Field(default=None, ge=1, le=rooms.MAX_QTY)  # None = all of them


class RoomMoveRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    from_room_id: str
    to_room_id: str
    item_id: str
    qty: int = Field(..., ge=1, le=rooms.MAX_QTY)
    expected_from_qty: int | None = Field(default=None, ge=0)


class ItemMergeRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    into_id: str


def _sent(body: BaseModel, *fields: str) -> dict:
    """Only the fields the client actually sent (so 'sent empty' can clear a value, 'not sent' leaves it)."""
    return {f: getattr(body, f) for f in fields if f in body.model_fields_set}


@router.get("/furniture/rooms")
def get_rooms(db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    return rooms.snapshot_rooms(db)


@router.post("/furniture/rooms")
def create_room(body: RoomCreateRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = rooms.create_room(db, actor=_actor(user), floor=body.floor, label=body.label, note=body.note)
    return _finish(db, body.client_request_id, "furniture_room_create", result, background)


@router.post("/furniture/rooms/move")
def move_between_rooms(body: RoomMoveRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = rooms.move_between_rooms(
        db, body.from_room_id, body.to_room_id, body.item_id, actor=_actor(user), qty=body.qty, expected_from_qty=body.expected_from_qty
    )
    return _finish(db, body.client_request_id, "furniture_room_move", result, background)


@router.patch("/furniture/rooms/{room_id}")
def update_room(room_id: str, body: RoomUpdateRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = rooms.update_room(db, room_id, actor=_actor(user), **_sent(body, "label", "note"))
    return _finish(db, body.client_request_id, "furniture_room_update", result, background)


@router.delete("/furniture/rooms/{room_id}")
def delete_room(room_id: str, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    """Only an EMPTY room can be deleted."""
    result = rooms.delete_room(db, room_id, actor=_actor(user))
    return _finish(db, None, "furniture_room_delete", result, background)


@router.post("/furniture/rooms/{room_id}/items")
def add_room_item(room_id: str, body: RoomItemAddRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = rooms.add_to_room(
        db, room_id, actor=_actor(user), qty=body.qty, item_id=body.item_id, new_name=body.new_name,
        new_category=body.new_category, new_note=body.new_note, comment=body.comment,
    )
    return _finish(db, body.client_request_id, "furniture_room_add", result, background)


@router.patch("/furniture/rooms/{room_id}/items/{item_id}")
def set_room_item(room_id: str, item_id: str, body: RoomItemSetRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = rooms.set_room_stock(db, room_id, item_id, actor=_actor(user), qty=body.qty, expected_qty=body.expected_qty, **_sent(body, "comment"))
    return _finish(db, body.client_request_id, "furniture_room_set", result, background)


@router.post("/furniture/rooms/{room_id}/items/{item_id}/remove")
def remove_room_item(room_id: str, item_id: str, body: RoomItemRemoveRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = rooms.remove_from_room(db, room_id, item_id, actor=_actor(user), qty=body.qty)
    return _finish(db, body.client_request_id, "furniture_room_remove", result, background)


@router.post("/furniture/items/{item_id}/merge")
def merge_item(item_id: str, body: ItemMergeRequest, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    """This item is the same thing as another one: its rooms and building counts move onto it, and it leaves the catalog."""
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = rooms.merge_items(db, item_id, body.into_id, actor=_actor(user))
    return _finish(db, body.client_request_id, "furniture_merge", result, background)
