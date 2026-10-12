from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import furniture as furn

router = APIRouter(tags=["furniture"])


@router.get("/furniture")
def get_furniture(db: Session = Depends(get_db)):
    """Public read, same as /inventory. Anything that CHANGES furniture (moves)
    will require a signed-in user."""
    return furn.snapshot_furniture(db)
