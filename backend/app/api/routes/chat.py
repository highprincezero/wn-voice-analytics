from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.chat.agent import run_chat_agent
from app.db.models import User
from app.db.session import get_db
from app.guardrails.safety import get_safety
from app.guardrails.validate import GuardrailError

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=8)


def _screen(text: str) -> None:
    if get_safety().analyze_content(text).blocked:
        raise GuardrailError("rejected by content safety")


@router.post("")
def chat(
    body: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is empty")
    history = [{"role": turn.role, "content": turn.content} for turn in body.history]
    try:
        _screen(message)
        for turn in history:
            if turn["content"].strip():
                _screen(turn["content"])
    except GuardrailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    outcome = run_chat_agent(db, user.id, message, history)
    return outcome
