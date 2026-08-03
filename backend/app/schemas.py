from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginPinRequest(BaseModel):
    role_key: str
    pin: str
    name: str | None = None


class LoginOperatorRequest(BaseModel):
    role_key: str
    operator_id: str
    password: str


class PersonOut(BaseModel):
    name: str
    role: str
    code: str


class LoginResponse(BaseModel):
    ok: bool = True
    token: str
    person: PersonOut
    shared_names: list[str] | None = None


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    code: str | None = None
    available: int | None = None


class TakeItemIn(BaseModel):
    item: str
    category: str
    qty: int = 1
    expectedReturn: str | None = None


class TakeBatchRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    person: str
    role: str
    items: list[TakeItemIn]


class ReturnBatchRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    txIds: list[str]
    returnedBy: str


class ReceiveRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    item: str
    qty: int = Field(..., gt=0)
    reason: str | None = None
    category: str | None = None  # required only when creating a brand-new item


class AdjustRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    item: str
    qty_delta: int
    reason: str = Field(..., min_length=3)


class InventoryRow(BaseModel):
    reference: str
    item: str
    quantity: int
    availability: str
    barcode: str | None = None
    reorder_min: int = 3


class HistoryRow(BaseModel):
    timestamp: str
    personRole: str
    item: str
    expectedReturn: str
    returnedAt: str
    returnedBy: str | None = None
    txId: str
    qty: int | None = None


class SnapshotResponse(BaseModel):
    ok: bool = True
    inventory: list[InventoryRow]
    history: list[HistoryRow]


class ActionOk(BaseModel):
    ok: bool = True
    data: dict[str, Any] | None = None
