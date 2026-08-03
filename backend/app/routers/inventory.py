from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_manager, require_restock_access
from app.database import get_db
from app.models import User
from app.schemas import (
    AdjustRequest,
    ReceiveRequest,
    ReturnBatchRequest,
    TakeBatchRequest,
)
from app.services import inventory as inv
from app.services.sheets_sync import maybe_sync_after_mutation

router = APIRouter(tags=["inventory"])


@router.get("/inventory")
@router.get("/")
def get_snapshot(db: Session = Depends(get_db)):
    """Public read snapshot for kiosk boot / status. Mutations require auth."""
    return inv.snapshot(db)


@router.post("/take-batch")
def take_batch(
    body: TakeBatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    existing = inv.get_idempotent(db, body.client_request_id)
    if existing:
        return existing

    result = inv.take_batch(
        db,
        person=body.person or user.name,
        role=body.role or user.role.value,
        items=[i.model_dump() for i in body.items],
    )
    if not result.get("ok"):
        db.rollback()
        return result

    inv.save_idempotent(db, body.client_request_id, "takeBatch", result)
    db.commit()
    maybe_sync_after_mutation(db)
    return result


@router.post("/return-batch")
def return_batch(
    body: ReturnBatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    existing = inv.get_idempotent(db, body.client_request_id)
    if existing:
        return existing

    result = inv.return_batch(db, tx_ids=body.txIds, returned_by=body.returnedBy or f"{user.name}/{user.role.value}")
    if not result.get("ok"):
        db.rollback()
        return result

    inv.save_idempotent(db, body.client_request_id, "returnBatch", result)
    db.commit()
    maybe_sync_after_mutation(db)
    return result


@router.post("/receive")
def receive(
    body: ReceiveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_restock_access),
):
    existing = inv.get_idempotent(db, body.client_request_id)
    if existing:
        return existing
    actor = f"{user.name}/{user.role.value}"
    result = inv.receive_stock(db, body.item, body.qty, actor, body.reason, category=body.category)
    if not result.get("ok"):
        db.rollback()
        return result
    inv.save_idempotent(db, body.client_request_id, "receive", result)
    db.commit()
    maybe_sync_after_mutation(db)
    return result


@router.post("/adjust")
def adjust(
    body: AdjustRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
):
    existing = inv.get_idempotent(db, body.client_request_id)
    if existing:
        return existing
    actor = f"{user.name}/{user.role.value}"
    result = inv.adjust_stock(db, body.item, body.qty_delta, actor, body.reason)
    if not result.get("ok"):
        db.rollback()
        return result
    inv.save_idempotent(db, body.client_request_id, "adjust", result)
    db.commit()
    maybe_sync_after_mutation(db)
    return result
