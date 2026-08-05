from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_manager, require_restock_access
from app.database import get_db
from app.models import Checkout, InventoryItem, User
from app.schemas import (
    AdjustRequest,
    ReceiveRequest,
    ReturnBatchRequest,
    TakeBatchRequest,
)
from app.services import inventory as inv
from app.services.sheets_sync import maybe_sync_after_mutation
from app.services.slack_notify import check_and_notify_purchase_alerts, notify_transaction

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
    who = body.person or user.name
    role = body.role or user.role.value
    take_totals = {}
    for i in body.items:
        take_totals[i.item] = take_totals.get(i.item, 0) + i.qty
    items_desc = ", ".join(f"{qty} × {name}" for name, qty in take_totals.items())
    notify_transaction(f"📤 *{who}* ({role}) took: {items_desc}")
    check_and_notify_purchase_alerts(db)
    db.commit()
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
    returned_by = body.returnedBy or f"{user.name}/{user.role.value}"
    checkouts = db.execute(select(Checkout).where(Checkout.tx_id.in_(body.txIds))).scalars().all()
    return_totals = {}
    for c in checkouts:
        item = db.get(InventoryItem, c.item_id)
        name = item.name if item else "unknown item"
        return_totals[name] = return_totals.get(name, 0) + c.qty
    parts = [f"{qty} × {name}" for name, qty in return_totals.items()]
    notify_transaction(f"📥 *{returned_by}* returned: {', '.join(parts) if parts else 'item(s)'}")
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
    item_existed_before = db.execute(
        select(InventoryItem).where(InventoryItem.name == body.item)
    ).scalar_one_or_none() is not None
    result = inv.receive_stock(db, body.item, body.qty, actor, body.reason, category=body.category)
    if not result.get("ok"):
        db.rollback()
        return result
    inv.save_idempotent(db, body.client_request_id, "receive", result)
    db.commit()
    maybe_sync_after_mutation(db)
    label = "🆕 new item added" if not item_existed_before else "restocked"
    notify_transaction(f"🚚 *{actor}* {label}: +{body.qty} × {body.item}")
    check_and_notify_purchase_alerts(db)
    db.commit()
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
    sign = "+" if body.qty_delta >= 0 else ""
    notify_transaction(f"⚙️ *{actor}* adjusted {body.item}: {sign}{body.qty_delta} (now {result.get('quantity')})")
    check_and_notify_purchase_alerts(db)
    db.commit()
    return result
