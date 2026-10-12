from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import User, UserRole
from app.services import furniture as furn
from app.services import inventory as inv

router = APIRouter(tags=["furniture"])


# The WHOLE Furniture Room — browsing and changing — is for these roles only.
# Supervisors and Tele-operators (the operator logins) have no access at all.
FURNITURE_ROLES = {UserRole.management, UserRole.maintenance, UserRole.devs}


def require_furniture_access(user: User = Depends(get_current_user)) -> User:
    if user.role not in FURNITURE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Management, Maintenance or Devs can use the Furniture Room.",
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


def _finish(db: Session, request_id: str | None, action: str, result: dict) -> dict:
    if not result.get("ok"):
        db.rollback()
        return result
    if request_id:
        inv.save_idempotent(db, request_id, action, result)
    db.commit()
    return result


@router.get("/furniture")
def get_furniture(db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    """Reading needs a signed-in Management / Maintenance / Devs user, same as changing."""
    return furn.snapshot_furniture(db)


@router.post("/furniture/items")
def create_item(body: ItemCreateRequest, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = furn.create_item(
        db, name=body.name, category=body.category, note=body.note, stock=body.stock, actor=_actor(user)
    )
    return _finish(db, body.client_request_id, "furniture_create", result)


@router.patch("/furniture/items/{item_id}")
def update_item(item_id: str, body: ItemUpdateRequest, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    fields = {k: getattr(body, k) for k in ("name", "category", "note") if k in body.model_fields_set}
    result = furn.update_item(db, item_id, fields=fields, actor=_actor(user))
    return _finish(db, body.client_request_id, "furniture_update", result)


@router.patch("/furniture/items/{item_id}/stock")
def adjust_stock(item_id: str, body: StockRequest, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    replay = inv.get_idempotent(db, body.client_request_id)
    if replay:
        return replay
    result = furn.adjust_stock(
        db, item_id, changes=[c.model_dump() for c in body.changes], reason=body.reason, actor=_actor(user)
    )
    return _finish(db, body.client_request_id, "furniture_stock", result)


@router.delete("/furniture/items/{item_id}")
def remove_item(item_id: str, db: Session = Depends(get_db), user: User = Depends(require_furniture_access)):
    """Soft delete: hides the item everywhere, keeps its history. Safe to call twice."""
    result = furn.deactivate_item(db, item_id, actor=_actor(user))
    return _finish(db, None, "furniture_remove", result)
