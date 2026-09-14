"""
One-off migration: the EARLIER add_sops_category_2026.py script added the
wrong string to the database enum ('SOPs', matching the display value).
SQLAlchemy actually sends the enum's internal name by default ('sops',
lowercase) — which is why Tools/Station Parts always worked (their internal
names already happened to match) and SOPs did not. This adds the correct
value so creating/restocking SOP items actually works.

Run it once, from inside the API container:

    docker compose exec api python -m app.fix_sops_enum_value_2026
"""

from sqlalchemy import text

from app.database import engine


def run():
    with engine.begin() as conn:
        conn.execute(text("ALTER TYPE item_category ADD VALUE IF NOT EXISTS 'sops'"))
    print("Done — 'sops' (lowercase, matching SQLAlchemy's actual enum name) is now valid.")


if __name__ == "__main__":
    run()
