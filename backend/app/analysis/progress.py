"""Per-file pipeline stage and the user-visible event log.

The analysis graph calls the tracker that run_file_analysis installs.
Upload, queue, and retry paths write events through the helpers below.
"""

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from app.config import get_settings
from app.db.models import AudioFile, FileEvent
from app.db.session import SessionLocal
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

_tracker: ContextVar["StageTracker | None"] = ContextVar("stage_tracker", default=None)
_seq_lock = threading.Lock()
_seq_tick = 0


def _next_seq() -> int:
    global _seq_tick
    with _seq_lock:
        _seq_tick = (_seq_tick + 1) % 1000
        return int(time.time() * 1000) * 1000 + _seq_tick


def current_tracker() -> "StageTracker | None":
    return _tracker.get()


def pause_for_demo() -> None:
    delay = float(get_settings().mock_stage_delay_sec or 0)
    if delay > 0:
        time.sleep(delay)


def append_event(
    db,
    audio: AudioFile,
    message: str,
    *,
    stage: str | None = None,
    level: str = "info",
    duration_ms: int | None = None,
) -> None:
    db.add(
        FileEvent(
            user_id=audio.user_id,
            id=uuid.uuid4(),
            file_id=audio.id,
            filename=audio.original_filename,
            message=message[:500],
            seq=_next_seq(),
            stage=stage,
            level=level,
            duration_ms=duration_ms,
            created_at=utcnow(),
        )
    )


def record_job_note(
    user_id,
    file_id,
    message: str,
    *,
    level: str = "info",
    stage: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        uid = uuid.UUID(str(user_id))
        fid = uuid.UUID(str(file_id))
        audio = (
            db.query(AudioFile).filter(AudioFile.user_id == uid, AudioFile.id == fid).one_or_none()
        )
        if audio is None:
            return
        append_event(db, audio, message, stage=stage or audio.stage, level=level)
        db.commit()
    except Exception:
        logger.exception("event log write failed")
        db.rollback()
    finally:
        db.close()


class StageTracker:
    def __init__(self, db, audio: AudioFile) -> None:
        self.db = db
        self.audio = audio
        self._started: dict[str, float] = {}

    def begin(self, stage: str, message: str) -> None:
        self.audio.stage = stage
        append_event(self.db, self.audio, message, stage=stage)
        self.db.commit()
        self._started[stage] = time.perf_counter()
        pause_for_demo()

    def finish(self, stage: str, message: str) -> None:
        started = self._started.pop(stage, None)
        duration_ms = None if started is None else int((time.perf_counter() - started) * 1000)
        text = message if duration_ms is None else f"{message} ({duration_ms} ms)"
        append_event(self.db, self.audio, text, stage=stage, duration_ms=duration_ms)
        self.db.commit()

    def note(self, message: str, *, stage: str | None = None, level: str = "info") -> None:
        append_event(self.db, self.audio, message, stage=stage or self.audio.stage, level=level)
        self.db.commit()


@contextmanager
def track(db, audio: AudioFile):
    tracker = StageTracker(db, audio)
    token = _tracker.set(tracker)
    try:
        yield tracker
    finally:
        _tracker.reset(token)


def mark_queued(file_id, user_id, message: str) -> None:
    db = SessionLocal()
    try:
        uid = uuid.UUID(str(user_id))
        fid = uuid.UUID(str(file_id))
        audio = (
            db.query(AudioFile).filter(AudioFile.user_id == uid, AudioFile.id == fid).one_or_none()
        )
        if audio is None:
            return
        audio.stage = "queued"
        append_event(db, audio, message, stage="queued")
        db.commit()
    finally:
        db.close()
