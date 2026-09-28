from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import RollupSummary, User
from app.db.session import get_db
from app.jobs.summary_job import run_rollup
from app.schemas.api import RollupRequest

router = APIRouter(prefix="/summaries", tags=["summaries"])


def _item(row: RollupSummary) -> dict:
    return {
        "id": str(row.id),
        "group_by": row.group_by,
        "time_from": row.time_from,
        "time_to": row.time_to,
        "trigger": row.trigger,
        "created_at": row.created_at,
        "result": row.result,
    }


@router.post("", status_code=201)
def create_summary(
    body: RollupRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = run_rollup(db, user.id, body.group_by, body.time_from, body.time_to, "on_demand")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _item(row)


@router.get("")
def list_summaries(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows = (
        db.query(RollupSummary)
        .filter(RollupSummary.user_id == user.id)
        .order_by(RollupSummary.created_at.desc())
        .limit(50)
        .all()
    )
    return {"items": [_item(row) for row in rows]}
