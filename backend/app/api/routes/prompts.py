from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import PromptConfig, User
from app.db.session import get_db
from app.guardrails.catalog import public_catalog
from app.guardrails.validate import GuardrailError, validate_selections
from app.schemas.api import PromptConfigRequest, PromptConfigResponse
from app.timeutil import utcnow

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("/options")
def options() -> dict:
    return {"options": public_catalog()}


@router.get("/config", response_model=PromptConfigResponse)
def get_config(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PromptConfigResponse:
    row = db.query(PromptConfig).filter(PromptConfig.user_id == user.id).one_or_none()
    return PromptConfigResponse(selections=[] if row is None else row.selections)


@router.put("/config", response_model=PromptConfigResponse)
def put_config(
    body: PromptConfigRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PromptConfigResponse:
    raw = [item.model_dump() for item in body.selections]
    try:
        cleaned = validate_selections(raw)
    except GuardrailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = db.query(PromptConfig).filter(PromptConfig.user_id == user.id).one_or_none()
    if row is None:
        row = PromptConfig(user_id=user.id, selections=cleaned, updated_at=utcnow())
        db.add(row)
    else:
        row.selections = cleaned
        row.updated_at = utcnow()
    db.commit()
    return PromptConfigResponse(selections=cleaned)
