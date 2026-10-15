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
    sop_status: str | None = None  # 'Active' or 'Non-Active', SOPs only
    shelf_location: str | None = Field(default=None, max_length=8)  # SOPs only, e.g. 'A3'
    reorder_min: int | None = Field(default=None, ge=0, le=100000)  # only used when this creates a NEW Tool / Station Part


class AdjustRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    item: str
    qty_delta: int
    reason: str = Field(..., min_length=3)


class UpdateItemRequest(BaseModel):
    """Edit one item from the Manager Report. Only the fields that are sent (not null) are changed."""
    client_request_id: str = Field(..., min_length=8)
    item: str = Field(..., min_length=1, max_length=200)          # the item's CURRENT name
    new_name: str | None = Field(default=None, max_length=200)
    qty: int | None = Field(default=None, ge=0, le=100000)         # the new total (Tools/SOPs) or amount on hand (Station Parts)
    expected_qty: int | None = Field(default=None, ge=0)           # what the person saw; a mismatch means someone else changed it
    reorder_min: int | None = Field(default=None, ge=0, le=100000)
    sop_status: str | None = Field(default=None, max_length=20)
    shelf_location: str | None = Field(default=None, max_length=8)
    reason: str | None = Field(default=None, max_length=200)


class DeleteItemRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    item: str = Field(..., min_length=1, max_length=200)


class InventoryRow(BaseModel):
    reference: str
    item: str
    quantity: int
    availability: str
    barcode: str | None = None
    reorder_min: int = 3
    sop_status: str | None = None
    shelf: str | None = None


class HistoryRow(BaseModel):
    timestamp: str
    personRole: str
    item: str
    expectedReturn: str
    returnedAt: str
    returnedBy: str | None = None
    txId: str
    qty: int | None = None


class WarrantyRow(BaseModel):
    timestamp: str
    reportedBy: str
    partName: str
    serialNumber: str
    issue: str


class SnapshotResponse(BaseModel):
    ok: bool = True
    inventory: list[InventoryRow]
    history: list[HistoryRow]
    warranty: list[WarrantyRow] = []


class ActionOk(BaseModel):
    ok: bool = True
    data: dict[str, Any] | None = None


class WarrantyRequest(BaseModel):
    client_request_id: str = Field(..., min_length=8)
    part_name: str = Field(..., min_length=1)
    serial_number: str = Field(..., min_length=1)
    issue: str = Field(..., min_length=1)
