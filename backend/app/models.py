import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import true as sql_true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserRole(str, enum.Enum):
    maintenance = "Maintenance"
    management = "Management"
    supervisor = "Supervisor"
    teleoperator = "Tele-operator"
    devs = "Devs"


class AuthKind(str, enum.Enum):
    pin = "pin"
    operator = "operator"


class ItemCategory(str, enum.Enum):
    tools = "Tools"
    station_parts = "Station Parts"
    sops = "SOPs"


class MovementType(str, enum.Enum):
    take = "take"
    return_ = "return"
    receive = "receive"
    adjust = "adjust"

    @staticmethod
    def _missing_(value):  # pragma: no cover
        if value == "return":
            return MovementType.return_
        return None


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False)
    auth_kind: Mapped[AuthKind] = mapped_column(Enum(AuthKind, name="auth_kind"), nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(String(255))
    operator_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    shared_pin_group: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    category: Mapped[ItemCategory] = mapped_column(
        Enum(ItemCategory, name="item_category"), nullable=False
    )
    qty_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_min: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    needs_purchase_alerted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Plain string, not an enum — only meaningful for SOPs ("Active" / "Non-Active").
    # Deliberately not an enum type to avoid the exact enum-value pitfall we
    # just hit with 'category' above.
    sop_status: Mapped[str | None] = mapped_column(String(20))
    # SOP Room only: where the SOP sits, rack letter + shelf number ("A1" … "F6").
    # Plain string, not an enum, for the same reason as sop_status.
    shelf_location: Mapped[str | None] = mapped_column(String(8))
    # Soft delete: a deleted item vanishes from every list, the Sheets and the alerts, but its
    # history stays. (Its name is changed on delete so the name can be reused.)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql_true())
    barcode: Mapped[str | None] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Checkout(Base):
    __tablename__ = "checkouts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tx_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id"), nullable=False)
    person_role: Mapped[str] = mapped_column(String(200), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expected_return: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_by: Mapped[str | None] = mapped_column(String(200))

    item: Mapped["InventoryItem"] = relationship()


class Movement(Base):
    __tablename__ = "movements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    movement_type: Mapped[MovementType] = mapped_column(
        Enum(
            MovementType,
            name="movement_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id"))
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    related_tx_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("client_request_id", name="uq_idempotency_client_request"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WarrantyReport(Base):
    """
    A warranty issue report — deliberately NOT inventory-tracked (no
    quantity, no checkout/return). Just a log: who reported an issue with
    which part, and what the issue was. Goes straight to the Storage Room
    Sheet's own dedicated "Warranty" tab.
    """
    __tablename__ = "warranty_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_name: Mapped[str] = mapped_column(String(200), nullable=False)
    serial_number: Mapped[str] = mapped_column(String(100), nullable=False)
    issue: Mapped[str] = mapped_column(Text, nullable=False)
    reported_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Furniture room — deliberately separate from InventoryItem/Checkout/Movement.
# Furniture is tracked per LOCATION and moved between locations, which doesn't
# fit the single-total checkout model — and keeping it in its own tables means
# nothing here can affect the Storage Room or SOP Room code paths.
# Plain strings (not enums) for category, for the same reason as sop_status.
# ---------------------------------------------------------------------------


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FurnitureItem(Base):
    __tablename__ = "furniture_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # names are NOT unique here
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(Text)
    image_paths: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # e.g. ["furniture/FUR-001.jpg"]
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FurnitureStock(Base):
    """How many units of an item sit at a location. One row per (item, location)."""

    __tablename__ = "furniture_stock"
    __table_args__ = (CheckConstraint("qty >= 0", name="ck_furniture_stock_qty_nonneg"),)

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("furniture_items.id", ondelete="CASCADE"), primary_key=True
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FurnitureMove(Base):
    """Append-only log of moves. batch_id groups everything confirmed together."""

    __tablename__ = "furniture_moves"
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_furniture_moves_qty_positive"),
        CheckConstraint("from_location_id <> to_location_id", name="ck_furniture_moves_distinct_locations"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("furniture_items.id"), nullable=False)
    from_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=False)
    to_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
