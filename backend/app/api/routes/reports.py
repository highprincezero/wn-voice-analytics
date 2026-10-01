import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import GroupReport, User
from app.db.session import get_db
from app.jobs.report_job import create_report, enqueue_group_report
from app.schemas.api import ReportRequest

router = APIRouter(prefix="/reports", tags=["reports"])


def _item(row: GroupReport) -> dict:
    return {
        "id": str(row.id),
        "status": row.status,
        "groupings": row.groupings,
        "time_from": row.time_from,
        "time_to": row.time_to,
        "created_at": row.created_at,
        "finished_at": row.finished_at,
        "error_message": row.error_message,
        "result": row.result,
    }


# 202: the report is built by a background job. Poll GET /reports/{id}.
@router.post("", status_code=202)
def start_report(
    body: ReportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = create_report(db, user.id, body.groupings, body.time_from, body.time_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    enqueue_group_report(row.id, user.id)
    db.refresh(row)
    return _item(row)


@router.get("")
def list_reports(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows = (
        db.query(GroupReport)
        .filter(GroupReport.user_id == user.id)
        .order_by(GroupReport.created_at.desc())
        .limit(20)
        .all()
    )
    return {"items": [_item(row) for row in rows]}


@router.get("/{report_id}")
def get_report(
    report_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        rid = uuid.UUID(report_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    row = (
        db.query(GroupReport)
        .filter(GroupReport.user_id == user.id, GroupReport.id == rid)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _item(row)
