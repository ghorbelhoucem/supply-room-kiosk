#!/usr/bin/env python3
"""Import operators from a gitignored JSON file into Postgres.

Create backend/seed_secrets.json like:
{
  "operators": [
    {"id": "1", "password": "...", "name": "Ahmed Soliman", "role": "Supervisor"}
  ]
}

Then:
  DATABASE_URL=... python -m app.import_operators
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import select

from app.auth import hash_secret
from app.database import Base, SessionLocal, engine
from app.models import AuthKind, User, UserRole

ROLE_MAP = {
    "Supervisor": UserRole.supervisor,
    "Tele-operator": UserRole.teleoperator,
    "supervisor": UserRole.supervisor,
    "teleoperator": UserRole.teleoperator,
}


def run() -> None:
    path = Path(os.environ.get("SEED_SECRETS_PATH", "seed_secrets.json"))
    if not path.exists():
        raise SystemExit(f"Missing {path}. Create it locally (gitignored).")
    data = json.loads(path.read_text())
    operators = data.get("operators") or []
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        upserted = 0
        for row in operators:
            op_id = str(row["id"])
            role = ROLE_MAP.get(row.get("role") or "Supervisor", UserRole.supervisor)
            name = row.get("name") or f"Operator-{op_id}"
            existing = db.execute(select(User).where(User.operator_id == op_id)).scalar_one_or_none()
            if existing:
                existing.name = name
                existing.role = role
                existing.password_hash = hash_secret(row["password"])
                existing.is_active = True
            else:
                db.add(
                    User(
                        name=name,
                        role=role,
                        auth_kind=AuthKind.operator,
                        operator_id=op_id,
                        password_hash=hash_secret(row["password"]),
                    )
                )
            upserted += 1
        db.commit()
        print(f"Upserted {upserted} operators")
    finally:
        db.close()


if __name__ == "__main__":
    run()
