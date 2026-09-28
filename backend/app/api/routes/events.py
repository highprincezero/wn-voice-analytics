import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import FileEvent, User
from app.db.session import get_db

router = APIRouter(prefix="/events", tags=["events"])


def _item(row: FileEvent) -> dict:
    return {
        "id": str(row.id),
        "file_id": str(row.file_id),
        "filename": row.filename,
        "message": row.message,
        "stage": row.stage,
        "level": row.level,
        "duration_ms": row.duration_ms,
        "created_at": row.created_at,
    }


@router.get("")
def list_events(
    limit: int = Query(default=300, ge=1, le=300),
    file_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(FileEvent).filter(FileEvent.user_id == user.id)
    if file_id is not None:
        query = query.filter(FileEvent.file_id == file_id)
    rows = query.order_by(FileEvent.seq.desc()).limit(limit).all()
    return {"items": [_item(row) for row in rows]}
