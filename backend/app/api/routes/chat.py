import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.chat.agent import run_chat_agent
from app.chat.memory import latest_session, open_session, prepare_history, save_exchange, thread
from app.db.models import User
from app.db.session import get_db
from app.guardrails.safety import get_safety
from app.guardrails.validate import GuardrailError

# All chat routes live under /chat.
router = APIRouter(prefix="/chat", tags=["chat"])


# Pydantic request models: FastAPI validates the JSON body against these automatically.
class ChatTurn(BaseModel):
    # extra="forbid" rejects unknown fields (guardrail against unexpected input).
    model_config = ConfigDict(extra="forbid")

    # Field(pattern=...) restricts role to 'user' or 'assistant'.
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    # Caps conversation history at 8 turns to bound prompt size.
    history: list[ChatTurn] = Field(default_factory=list, max_length=8)
    # Absent on the first turn. The same id keeps later turns in one session.
    session_id: uuid.UUID | None = None


# Content-safety screen for user text before it reaches the LLM.
def _screen(text: str) -> None:
    if get_safety().analyze_content(text).blocked:
        raise GuardrailError("rejected by content safety")


def _screen_all(texts: list[str]) -> None:
    """Screen the question and each history turn side by side. Any block rejects the turn."""
    if len(texts) <= 1:
        for text in texts:
            _screen(text)
        return
    with ThreadPoolExecutor(max_workers=min(len(texts), 9)) as pool:
        for future in [pool.submit(_screen, text) for text in texts]:
            future.result()


# POST /api/v1/chat; `body: ChatRequest` = parsed + validated JSON request body.
@router.post("")
def chat(
    body: ChatRequest,
    # Depends(get_current_user) also enforces auth and the per-user rate limit.
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is empty")
    history = [{"role": turn.role, "content": turn.content} for turn in body.history]
    texts = [message] + [turn["content"] for turn in history if turn["content"].strip()]
    try:
        _screen_all(texts)
    except GuardrailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session = open_session(db, user.id, body.session_id)
    # Stored turns win. Browser turns are copied in only while the session is empty.
    stored = prepare_history(db, user.id, session.id, history)
    outcome = run_chat_agent(db, user.id, message, stored)
    save_exchange(db, user.id, session.id, message, outcome.get("reply") or "")
    db.commit()
    outcome["session_id"] = str(session.id)
    return outcome


@router.get("/session")
def chat_session(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = latest_session(db, user.id)
    if row is None:
        return {"session_id": None, "messages": []}
    return {"session_id": str(row.id), "messages": thread(db, user.id, row.id)}


@router.post("/session")
def start_chat_session(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = open_session(db, user.id, None)
    db.commit()
    return {"session_id": str(row.id), "messages": []}
