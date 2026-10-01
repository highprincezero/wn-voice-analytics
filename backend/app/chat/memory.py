"""Postgres storage for one account's chat session."""

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, ChatSession
from app.timeutil import utcnow

_HISTORY_LIMIT = 8
_THREAD_LIMIT = 40
_EARLIER = (
    "previous question",
    "last question",
    "earlier question",
    "what did i ask",
    "question before",
)


def asks_earlier_question(message: str) -> bool:
    """True when the user is asking what they asked before."""
    lines = []
    for line in (message or "").replace("\u2019", "'").splitlines():
        if line.strip().lower().startswith("file_id="):
            continue
        lines.append(line)
    text = " ".join(" ".join(lines).lower().split())
    return any(phrase in text for phrase in _EARLIER)


def _store_text(content: str) -> str:
    kept = []
    for line in (content or "").splitlines():
        if line.strip().lower().startswith("file_id="):
            continue
        kept.append(line)
    text = "\n".join(kept).strip()
    if not text:
        text = (content or "").strip()
    return text[:2000]


def previous_question(history: list[dict]) -> str:
    """The previous user turn, or empty. The current question is not in history."""
    previous = ""
    for turn in reversed(history or []):
        if turn.get("role") != "user":
            continue
        content = _store_text(str(turn.get("content") or ""))
        if content:
            previous = " ".join(content.split())
            break
    if len(previous) > 500:
        previous = previous[:500].rstrip() + "..."
    return previous


def earlier_question_reply(history: list[dict]) -> str:
    """Fixed wording for mock mode, or when the compose model fails."""
    previous = previous_question(history)
    if not previous:
        return "This is the first question in this chat."
    return f"Your previous question was: {previous}"


def open_session(db: Session, user_id: uuid.UUID, session_id: uuid.UUID | None) -> ChatSession:
    if session_id is not None:
        found = (
            db.query(ChatSession)
            .filter(ChatSession.user_id == user_id, ChatSession.id == session_id)
            .one_or_none()
        )
        if found is not None:
            return found
    row = ChatSession(
        user_id=user_id,
        id=session_id or uuid.uuid4(),
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def latest_session(db: Session, user_id: uuid.UUID) -> ChatSession | None:
    return (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user_id)
        .order_by(ChatSession.created_at.desc(), ChatSession.id.desc())
        .first()
    )


def _next_seq(db: Session, user_id: uuid.UUID, session_id: uuid.UUID) -> int:
    current = (
        db.query(func.max(ChatMessage.seq))
        .filter(ChatMessage.user_id == user_id, ChatMessage.session_id == session_id)
        .scalar()
    )
    return int(current or 0) + 1


def _append(
    db: Session,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    role: str,
    content: str,
    seq: int,
) -> None:
    text = _store_text(content)
    if not text or role not in {"user", "assistant"}:
        return
    db.add(
        ChatMessage(
            user_id=user_id,
            id=uuid.uuid4(),
            session_id=session_id,
            role=role,
            content=text,
            seq=seq,
            created_at=utcnow(),
        )
    )


def _rows(db: Session, user_id: uuid.UUID, session_id: uuid.UUID, limit: int) -> list[ChatMessage]:
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == user_id, ChatMessage.session_id == session_id)
        .order_by(ChatMessage.seq.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return rows


def turns(db: Session, user_id: uuid.UUID, session_id: uuid.UUID, limit: int) -> list[dict]:
    rows = _rows(db, user_id, session_id, limit)
    return [{"role": row.role, "content": row.content} for row in rows]


def prepare_history(
    db: Session,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    client_history: list[dict],
) -> list[dict]:
    """Copy browser turns into an empty session, then return the stored ones."""
    if _next_seq(db, user_id, session_id) == 1:
        seq = 1
        for turn in (client_history or [])[-_HISTORY_LIMIT:]:
            role = str(turn.get("role") or "")
            content = str(turn.get("content") or "")
            if role not in {"user", "assistant"} or not _store_text(content):
                continue
            _append(db, user_id, session_id, role, content, seq)
            seq += 1
        db.flush()
    return turns(db, user_id, session_id, _HISTORY_LIMIT)


def save_exchange(
    db: Session,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    message: str,
    reply: str,
) -> None:
    seq = _next_seq(db, user_id, session_id)
    _append(db, user_id, session_id, "user", message, seq)
    _append(db, user_id, session_id, "assistant", reply.strip() or "No answer.", seq + 1)
    db.flush()


def thread(db: Session, user_id: uuid.UUID, session_id: uuid.UUID) -> list[dict]:
    return turns(db, user_id, session_id, _THREAD_LIMIT)
