import logging
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

logger = logging.getLogger(__name__)

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


BLOCKED_MESSAGE = "rejected by content safety: your message was flagged ({reason})."
BLOCKED_HISTORY = (
    "rejected by content safety: an earlier message of yours in this chat was flagged "
    "({reason}). Start a new chat to continue."
)
_BLOCK_PREFIX = "rejected by content safety"


def _without_blocked_turns(history: list[dict]) -> list[dict]:
    """Drop turns that were blocked (a user turn answered by a block message, and that
    message). They never reached the model, so they are not history."""
    kept: list[dict] = []
    for index, turn in enumerate(history):
        content = turn["content"].strip()
        if turn["role"] == "assistant" and content.lower().startswith(_BLOCK_PREFIX):
            continue
        following = history[index + 1] if index + 1 < len(history) else None
        if (
            turn["role"] == "user"
            and following is not None
            and following["role"] == "assistant"
            and following["content"].strip().lower().startswith(_BLOCK_PREFIX)
        ):
            continue
        kept.append(turn)
    return kept


def _screen_turn(message: str, history: list[dict]) -> None:
    """Content safety before the model sees anything.

    The current message gets the full check: Prompt Shields plus the harm categories.
    Earlier user turns get Prompt Shields only. Assistant replies are never screened:
    they are our own words, and a harm score on a summary of a call (for example a
    caller "disputing being elderly") must not block every later question.
    """
    earlier = [
        (index, turn["content"])
        for index, turn in enumerate(history)
        if turn["role"] == "user" and turn["content"].strip()
    ]
    safety = get_safety()
    with ThreadPoolExecutor(max_workers=min(len(earlier) + 1, 9)) as pool:
        current = pool.submit(safety.analyze_content, message)
        shields = [(index, pool.submit(safety.shield_prompt, text)) for index, text in earlier]
        result = current.result()
        if result.blocked:
            logger.info("chat blocked: segment=current reason=%s", result.reason)
            raise GuardrailError(BLOCKED_MESSAGE.format(reason=result.reason))
        for index, future in shields:
            found = future.result()
            if found.blocked:
                logger.info("chat blocked: segment=history[%s] reason=%s", index, found.reason)
                raise GuardrailError(BLOCKED_HISTORY.format(reason=found.reason))


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
    history = _without_blocked_turns(history)
    try:
        _screen_turn(message, history)
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
