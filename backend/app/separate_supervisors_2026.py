"""
One-off script: separates Supervisors from Tele-operators into distinct
accounts again (previously they shared one credential pool by design — this
undoes that and enforces the specific list below).

Everyone in SUPERVISOR_IDS becomes role=supervisor. Every other existing
operator-type account becomes role=teleoperator. After this runs, an ID can
only log in under the role it's actually assigned — the "any ID works under
either card" behavior from before is gone (see the matching auth.py update).

Run it once, from inside the API container:

    docker compose exec api python -m app.separate_supervisors_2026
"""

from app.database import SessionLocal
from app.models import AuthKind, User, UserRole

# Matched by name against the 166 known operator names. Each comment shows
# the exact name it was matched to (spelling variants noted where relevant).
SUPERVISOR_IDS = {
    "21",   # Kamaal Ahmed Barkathulla
    "19",   # Nisha Tallat
    "11",   # Muhammad Uzair (stored as "Muhammed Uzair")
    "5",    # Azim Shakhmaev
    "65",   # Shayan Fazili
    "6",    # Usama Ali
    "3",    # Ahmed Halim Ammari
    "35",   # Shaojie Qiu
    "13",   # Pranta Saha
    "81",   # Katharina Graf
    "1",    # Ahmed Soliman
    "38",   # Ahmed Aboelmaaty
    "70",   # Rohit Kishore Galani
    "9",    # Muhammad Farooq (stored as "Mouhammad Farooq")
    "106",  # GopalaRao Kandula
    "104",  # Murali Tadigiri
    "67",   # Danial Ahmed
    "63",   # Joseph Sweiss (stored as "Joseph Maher Mousa Sweiss")
    "172",  # Nurul Afsar
    "17",   # Muhammad Shafee
    "102",  # Marcos Felipe Junior
    "103",  # Jitendra Chowdhary Vattikunta (stored as "Jitendra Chowdary Vattikunta")
    # NOTE: "Joswin Dsouza" was NOT found in the existing name records —
    # their operator ID is unknown, so they are NOT included here yet.
    # Once you send their ID, add it to this set and re-run this script.
}


def run():
    db = SessionLocal()
    try:
        operators = db.query(User).filter(User.auth_kind == AuthKind.operator).all()
        made_supervisor = []
        made_teleoperator = []
        for user in operators:
            if user.operator_id in SUPERVISOR_IDS:
                user.role = UserRole.supervisor
                made_supervisor.append(f"{user.operator_id} ({user.name})")
            else:
                user.role = UserRole.teleoperator
                made_teleoperator.append(user.operator_id)

        db.commit()

        print(f"Set {len(made_supervisor)} operator(s) to Supervisor:")
        for line in made_supervisor:
            print(" -", line)
        print(f"\nSet {len(made_teleoperator)} operator(s) to Tele-operator.")

        found_ids = {u.operator_id for u in operators}
        missing = SUPERVISOR_IDS - found_ids
        if missing:
            print(f"\nWARNING: these supervisor IDs were not found in the database at all: {missing}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
