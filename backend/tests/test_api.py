"""API smoke tests using the app SQLite engine (set via env before import)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["GOOGLE_SHEET_SYNC_ENABLED"] = "false"

from app.auth import hash_secret  # noqa: E402
from app.database import Base, SessionLocal, engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuthKind, InventoryItem, ItemCategory, User, UserRole  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(
        User(
            name="Houcem",
            role=UserRole.maintenance,
            auth_kind=AuthKind.pin,
            pin_hash=hash_secret("4708"),
        )
    )
    db.add(
        User(
            name="Rosa",
            role=UserRole.management,
            auth_kind=AuthKind.pin,
            pin_hash=hash_secret("4685"),
        )
    )
    db.add(
        InventoryItem(
            sku="TOOL-KEYBOARD",
            name="Keyboard",
            category=ItemCategory.tools,
            qty_on_hand=2,
            reorder_min=1,
            barcode="Keyboard",
        )
    )
    db.add(
        InventoryItem(
            sku="PART-HDMI",
            name="HDMI Cable",
            category=ItemCategory.station_parts,
            qty_on_hand=5,
            reorder_min=3,
            barcode="HDMI Cable",
        )
    )
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    db.close()


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_login_and_take_return_idempotent(client):
    login = client.post("/api/auth/login/pin", json={"role_key": "maintenance", "pin": "4708"})
    assert login.status_code == 200
    body = login.json()
    assert body["ok"] is True
    token = body["token"]
    headers = {"Authorization": f"Bearer {token}"}

    req_id = "11111111-1111-1111-1111-111111111111"
    payload = {
        "client_request_id": req_id,
        "person": "Houcem",
        "role": "Maintenance",
        "items": [{"item": "Keyboard", "category": "Tools", "qty": 1, "expectedReturn": None}],
    }
    first = client.post("/api/take-batch", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json()["ok"] is True
    tx_ids = first.json()["txIds"]

    second = client.post("/api/take-batch", json=payload, headers=headers)
    assert second.status_code == 200
    assert second.json()["txIds"] == tx_ids

    oversell = client.post(
        "/api/take-batch",
        json={
            "client_request_id": "22222222-2222-2222-2222-222222222222",
            "person": "Houcem",
            "role": "Maintenance",
            "items": [{"item": "Keyboard", "category": "Tools", "qty": 2, "expectedReturn": None}],
        },
        headers=headers,
    )
    assert oversell.json()["ok"] is False
    assert oversell.json()["code"] == "INSUFFICIENT_STOCK"

    ret = client.post(
        "/api/return-batch",
        json={
            "client_request_id": "33333333-3333-3333-3333-333333333333",
            "txIds": tx_ids,
            "returnedBy": "Houcem/Maintenance",
        },
        headers=headers,
    )
    assert ret.status_code == 200
    assert ret.json()["ok"] is True


def test_manager_receive_and_export(client):
    login = client.post("/api/auth/login/pin", json={"role_key": "management", "pin": "4685"})
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    recv = client.post(
        "/api/receive",
        json={
            "client_request_id": "44444444-4444-4444-4444-444444444444",
            "item": "HDMI Cable",
            "qty": 3,
            "reason": "PO-100",
        },
        headers=headers,
    )
    assert recv.json()["ok"] is True
    assert recv.json()["quantity"] == 8

    export = client.get("/api/exports/inventory.xlsx", headers=headers)
    assert export.status_code == 200
    assert "spreadsheetml" in export.headers["content-type"]


def test_report_allows_maintenance_denies_supervisor(client):
    maint = client.post("/api/auth/login/pin", json={"role_key": "maintenance", "pin": "4708"})
    assert client.get("/api/reports/summary", headers={"Authorization": f"Bearer {maint.json()['token']}"}).status_code == 200

    # supervisor has no seeded user with pin in this fixture; create one
    from app.database import SessionLocal
    from app.models import AuthKind, User, UserRole
    from app.auth import hash_secret, create_access_token

    db = SessionLocal()
    user = User(
        name="Op-9",
        role=UserRole.supervisor,
        auth_kind=AuthKind.operator,
        operator_id="9",
        password_hash=hash_secret("x"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user)
    db.close()
    denied = client.get("/api/reports/summary", headers={"Authorization": f"Bearer {token}"})
    assert denied.status_code == 403
