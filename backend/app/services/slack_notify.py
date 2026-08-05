"""
Slack notifications — two independent channels:
  1. A live feed of every take/return/restock/adjustment
  2. Low-stock alerts, fired only when an item newly drops to/below its
     reorder point (not re-sent on every subsequent take while still low)

Both use simple Slack Incoming Webhooks — no bot install/OAuth needed.
If a webhook URL isn't configured, that channel's notifications are
silently skipped (never blocks the actual transaction).
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import InventoryItem

logger = logging.getLogger(__name__)


def _post_to_slack(webhook_url: str, text: str) -> None:
    if not webhook_url:
        return
    try:
        httpx.post(webhook_url, json={"text": text}, timeout=8.0)
    except Exception:  # noqa: BLE001
        # Never let a Slack outage break a real transaction.
        logger.exception("Slack notification failed")


def notify_transaction(text: str) -> None:
    settings = get_settings()
    _post_to_slack(settings.slack_transactions_webhook_url, text)


def check_and_notify_purchase_alerts(db: Session) -> None:
    """
    Call this after any mutation that could change stock levels. Fires an
    alert only the moment an item newly crosses at/below its reorder point,
    and clears the flag once it's restocked back above it (so a future dip
    triggers a fresh alert instead of staying silent forever).
    """
    settings = get_settings()
    items = db.execute(select(InventoryItem)).scalars().all()
    newly_low = []
    for it in items:
        is_low = it.qty_on_hand <= it.reorder_min
        if is_low and not it.needs_purchase_alerted:
            newly_low.append(it)
            it.needs_purchase_alerted = True
        elif not is_low and it.needs_purchase_alerted:
            it.needs_purchase_alerted = False

    if newly_low and settings.slack_purchase_webhook_url:
        lines = [f"• {it.name} — {it.qty_on_hand} left (reorder point: {it.reorder_min})" for it in newly_low]
        text = "🛒 *Purchase List update — new item(s) need restocking:*\n" + "\n".join(lines)
        _post_to_slack(settings.slack_purchase_webhook_url, text)
