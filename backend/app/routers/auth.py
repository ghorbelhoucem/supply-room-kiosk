from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    ROLE_KEY_TO_ENUM,
    audit,
    create_access_token,
    verify_secret,
)
from app.database import get_db
from app.models import AuthKind, User
from app.schemas import LoginOperatorRequest, LoginPinRequest, LoginResponse, PersonOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login/pin")
def login_pin(body: LoginPinRequest, db: Session = Depends(get_db)):
    role = ROLE_KEY_TO_ENUM.get(body.role_key)
    if not role:
        raise HTTPException(status_code=400, detail="Unknown role")

    candidates = (
        db.execute(
            select(User).where(
                User.role == role,
                User.auth_kind == AuthKind.pin,
                User.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    matches = [u for u in candidates if verify_secret(body.pin, u.pin_hash)]
    if not matches:
        audit(db, "auth_failed", detail=f"pin role={body.role_key}")
        db.commit()
        return {"ok": False, "error": "Incorrect PIN, try again.", "code": "BAD_PIN"}

    # Unique PIN → single user
    unique = [u for u in matches if not u.shared_pin_group]
    if len(unique) == 1 and not body.name:
        user = unique[0]
        token = create_access_token(user)
        audit(db, "auth_ok", actor=f"{user.name}/{user.role.value}")
        db.commit()
        return LoginResponse(
            token=token,
            person=PersonOut(name=user.name, role=user.role.value, code=f"{user.name}/{user.role.value}"),
        )

    shared = [u for u in matches if u.shared_pin_group]
    if shared and not body.name:
        names = sorted({u.name for u in shared})
        if len(names) == 1:
            user = shared[0]
            token = create_access_token(user)
            audit(db, "auth_ok", actor=f"{user.name}/{user.role.value}")
            db.commit()
            return LoginResponse(
                token=token,
                person=PersonOut(
                    name=user.name, role=user.role.value, code=f"{user.name}/{user.role.value}"
                ),
            )
        return {
            "ok": True,
            "needs_name": True,
            "shared_names": names,
        }

    if body.name:
        user = next((u for u in matches if u.name == body.name), None)
        if not user:
            return {"ok": False, "error": "Name does not match this PIN.", "code": "BAD_NAME"}
        token = create_access_token(user)
        audit(db, "auth_ok", actor=f"{user.name}/{user.role.value}")
        db.commit()
        return LoginResponse(
            token=token,
            person=PersonOut(name=user.name, role=user.role.value, code=f"{user.name}/{user.role.value}"),
        )

    return {"ok": False, "error": "Ambiguous PIN.", "code": "AMBIGUOUS_PIN"}


@router.post("/login/operator")
def login_operator(body: LoginOperatorRequest, db: Session = Depends(get_db)):
    role = ROLE_KEY_TO_ENUM.get(body.role_key)
    if not role:
        raise HTTPException(status_code=400, detail="Unknown role")

    user = db.execute(
        select(User).where(
            User.operator_id == body.operator_id.strip(),
            User.auth_kind == AuthKind.operator,
            User.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if not user or user.role != role or not verify_secret(body.password, user.password_hash):
        audit(db, "auth_failed", detail=f"operator id={body.operator_id}")
        db.commit()
        return {"ok": False, "error": "Incorrect password, try again.", "code": "BAD_PASSWORD"}

    token = create_access_token(user)
    audit(db, "auth_ok", actor=f"{user.name}/{user.role.value}")
    db.commit()
    return LoginResponse(
        token=token,
        person=PersonOut(name=user.name, role=user.role.value, code=f"{user.name}/{user.role.value}"),
    )
