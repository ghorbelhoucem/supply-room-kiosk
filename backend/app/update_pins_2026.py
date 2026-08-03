"""
One-off script: gives Felix, Yie Hang, Bingie, Ahmed, and Winnie their own
individual PINs instead of the shared '0000' group code.

This does NOT touch inventory, history, or anyone else's login — it only
updates these 5 specific users, and only their PIN.

Run it once, from inside the API container:

    docker compose exec api python -m app.update_pins_2026

(Run this from the same folder where your docker-compose.yml lives.)
"""

from app.auth import hash_secret
from app.database import SessionLocal
from app.models import User, UserRole

# name -> (new PIN, role) — role is used to disambiguate if two people
# ever share the same first name across different teams.
NEW_PINS = {
    "Felix": ("5316", UserRole.maintenance),
    "Yie Hang": ("4192", UserRole.maintenance),
    "Bingie": ("4332", UserRole.management),
    "Ahmed": ("5307", UserRole.maintenance),
    "Winnie": ("4207", UserRole.management),
}


def run():
    db = SessionLocal()
    try:
        updated = []
        not_found = []
        for name, (pin, role) in NEW_PINS.items():
            user = (
                db.query(User)
                .filter(User.name == name, User.role == role)
                .one_or_none()
            )
            if user is None:
                not_found.append(name)
                continue
            user.pin_hash = hash_secret(pin)
            user.shared_pin_group = None  # now a unique PIN, no longer shared
            updated.append(name)

        db.commit()

        print(f"Updated {len(updated)} user(s): {', '.join(updated) if updated else '(none)'}")
        if not_found:
            print(f"NOT FOUND (no changes made for these): {', '.join(not_found)}")
            print("Check that the names/roles above match exactly what's in the database.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
