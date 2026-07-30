from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_secret
from app.models import AuthKind, InventoryItem, ItemCategory, User, UserRole


PIN_USERS = [
    # unique PINs
    ("Houcem", UserRole.maintenance, "4708", None),
    ("Marwan", UserRole.maintenance, "4191", None),
    ("Hamza", UserRole.maintenance, "4719", None),
    ("Rosa", UserRole.management, "4685", None),
    ("Clarissa", UserRole.management, "4728", None),
    # shared PIN groups
    ("Ahmed", UserRole.maintenance, "0000", "maintenance-shared"),
    ("Felix", UserRole.maintenance, "0000", "maintenance-shared"),
    ("Yie Hang", UserRole.maintenance, "0000", "maintenance-shared"),
    ("Bingie", UserRole.management, "0000", "management-shared"),
    ("Winnie", UserRole.management, "0000", "management-shared"),
    ("Developer", UserRole.devs, "7346", "devs-shared"),
]

# Demo operators — replace via seed_secrets / import in production
DEMO_OPERATORS = [
    ("1", "Senate!now1", UserRole.supervisor),
    ("2", "Punch+love2", UserRole.teleoperator),
]

SAMPLE_ITEMS = [
    ("TOOL-KEYBOARD", "Keyboard", ItemCategory.tools, 3, 1, "Keyboard"),
    ("TOOL-MOUSE", "Mouse", ItemCategory.tools, 2, 1, "Mouse"),
    ("PART-HDMI", "HDMI Cable", ItemCategory.station_parts, 8, 3, "HDMI Cable"),
    ("PART-ETH-1M", "Ethernet Cable 1M", ItemCategory.station_parts, 10, 3, "Ethernet Cable 1M"),
    ("PART-USB-HUB", "USB Hub", ItemCategory.station_parts, 4, 2, "USB Hub"),
]


def seed_if_empty(db: Session) -> None:
    existing = db.execute(select(User).limit(1)).scalar_one_or_none()
    if existing:
        return

    for name, role, pin, group in PIN_USERS:
        db.add(
            User(
                name=name,
                role=role,
                auth_kind=AuthKind.pin,
                pin_hash=hash_secret(pin),
                shared_pin_group=group,
            )
        )

    for op_id, password, role in DEMO_OPERATORS:
        db.add(
            User(
                name=f"Operator-{op_id}",
                role=role,
                auth_kind=AuthKind.operator,
                operator_id=op_id,
                password_hash=hash_secret(password),
            )
        )

    for sku, name, category, qty, reorder_min, barcode in SAMPLE_ITEMS:
        db.add(
            InventoryItem(
                sku=sku,
                name=name,
                category=category,
                qty_on_hand=qty,
                reorder_min=reorder_min,
                barcode=barcode,
            )
        )
    db.commit()
