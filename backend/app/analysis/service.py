import json
import logging
import uuid

from app.analysis.graph import run_graph
from app.analysis.progress import append_event, mark_queued, pause_for_demo, track
from app.config import get_settings
from app.db.models import Analysis, AudioFile, PromptConfig, Transcript
from app.db.session import SessionLocal
from app.guardrails.validate import validate_selections
from app.storage.blob import get_blob_store
from app.storage.keys import analysis_key, transcript_key
from app.timeutil import utcnow

logger = logging.getLogger(__name__)


def enqueue_analysis(file_id: uuid.UUID, user_id: uuid.UUID) -> None:
    settings = get_settings()
    if settings.analysis_mode == "inline":
        mark_queued(file_id, user_id, "Analysis started inline (no queue)")
        try:
            run_file_analysis(str(file_id), str(user_id))
        except Exception:
            logger.exception("inline analysis failed for %s", file_id)
        return
    body = {"kind": "analyze_file", "file_id": str(file_id), "user_id": str(user_id)}
    if settings.broker == "servicebus":
        from app.jobs.publisher import publish_job

        mark_queued(file_id, user_id, "Job queued on Service Bus (transcription)")
        publish_job("transcription", body)
        return
    from app.jobs.tasks import analyze_file_task

    task_id = str(uuid.uuid4())
    mark_queued(file_id, user_id, f"Job queued (task id {task_id})")
    analyze_file_task.apply_async((str(file_id), str(user_id)), task_id=task_id)


def run_file_analysis(file_id: str, user_id: str, task_id: str | None = None) -> None:
    db = SessionLocal()
    try:
        uid = uuid.UUID(str(user_id))
        fid = uuid.UUID(str(file_id))
        audio = (
            db.query(AudioFile).filter(AudioFile.user_id == uid, AudioFile.id == fid).one_or_none()
        )
        if audio is None:
            logger.warning("analysis skipped; file %s is missing", file_id)
            return
        audio.status = "processing"
        audio.error_message = None
        db.commit()
        with track(db, audio) as tracker:
            pickup = "Worker picked up the job"
            if task_id:
                pickup = f"{pickup} (task id {task_id})"
            tracker.note(pickup)
            pause_for_demo()
            try:
                payload = get_blob_store().download(audio.storage_key)
                config = db.query(PromptConfig).filter(PromptConfig.user_id == uid).one_or_none()
                raw_options = list(config.selections) if config is not None else []
                options = validate_selections(raw_options) if raw_options else []
                state = run_graph(
                    audio_bytes=payload,
                    filename=audio.original_filename,
                    options=options,
                )
                _persist(db, audio, state, tracker)
                db.commit()
            except Exception as exc:
                db.rollback()
                failed = (
                    db.query(AudioFile)
                    .filter(AudioFile.user_id == uid, AudioFile.id == fid)
                    .one_or_none()
                )
                if failed is not None:
                    failed.status = "failed"
                    failed.error_message = type(exc).__name__[:200]
                    append_event(
                        db,
                        failed,
                        f"Analysis failed: {type(exc).__name__}",
                        stage=failed.stage,
                        level="error",
                    )
                    db.commit()
                logger.exception("analysis failed for %s", file_id)
                raise
    finally:
        db.close()


def _persist(db, audio: AudioFile, state: dict, tracker) -> None:
    blocked = bool(state.get("blocked"))
    audio.duration_sec = float(state.get("duration_sec") or 0)
    audio.status = "blocked" if blocked else "completed"
    audio.error_message = state.get("block_reason") or None
    provider = get_settings().llm_provider
    transcript_body = {
        "text": state.get("transcript") or "",
        "provider": provider,
    }
    t_key = transcript_key(audio.user_id, audio.id)
    get_blob_store().upload(
        t_key,
        json.dumps(transcript_body).encode(),
        "application/json",
    )
    transcript = (
        db.query(Transcript)
        .filter(Transcript.user_id == audio.user_id, Transcript.file_id == audio.id)
        .one_or_none()
    )
    if transcript is None:
        transcript = Transcript(
            user_id=audio.user_id,
            id=uuid.uuid4(),
            file_id=audio.id,
            text=transcript_body["text"],
            storage_key=t_key,
            provider=provider,
            created_at=utcnow(),
        )
        db.add(transcript)
    else:
        transcript.text = transcript_body["text"]
        transcript.storage_key = t_key
        transcript.provider = provider

    document = {
        "status": audio.status,
        "duration_sec": audio.duration_sec,
        "summary": None if blocked else state.get("summary"),
        "taxonomy": None if blocked else state.get("taxonomy"),
        "layer2": {} if blocked else state.get("layer2") or {},
        "block_reason": state.get("block_reason") or None,
        "summary_strategy": None if blocked else state.get("summary_strategy"),
        "provider": provider,
        "chunk_count": state.get("chunk_count"),
    }
    a_key = analysis_key(audio.user_id, audio.id)
    get_blob_store().upload(a_key, json.dumps(document).encode(), "application/json")
    analysis = (
        db.query(Analysis)
        .filter(Analysis.user_id == audio.user_id, Analysis.file_id == audio.id)
        .one_or_none()
    )
    if analysis is None:
        analysis = Analysis(
            user_id=audio.user_id,
            id=uuid.uuid4(),
            file_id=audio.id,
            status=audio.status,
            duration_sec=audio.duration_sec,
            summary=document["summary"],
            taxonomy=document["taxonomy"],
            layer2=document["layer2"],
            block_reason=document["block_reason"],
            summary_strategy=document["summary_strategy"],
            provider=provider,
            storage_key=a_key,
            created_at=utcnow(),
        )
        db.add(analysis)
    else:
        analysis.status = audio.status
        analysis.duration_sec = audio.duration_sec
        analysis.summary = document["summary"]
        analysis.taxonomy = document["taxonomy"]
        analysis.layer2 = document["layer2"]
        analysis.block_reason = document["block_reason"]
        analysis.summary_strategy = document["summary_strategy"]
        analysis.provider = provider
        analysis.storage_key = a_key
    audio.stage = "saved"
    tracker.note("Results saved to Postgres and blob storage", stage="saved")
