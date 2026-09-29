import hashlib
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.analysis.progress import append_event
from app.analysis.service import enqueue_analysis
from app.api.deps import get_current_user
from app.config import get_settings
from app.db.models import Analysis, AudioFile, Transcript, User
from app.db.session import get_db
from app.guardrails.validate import GuardrailError, matches_custom, parse_custom_filter
from app.storage.blob import get_blob_store
from app.storage.keys import (
    CONTENT_TYPES,
    analysis_key,
    assert_user_key,
    audio_key,
    safe_extension,
    safe_filename,
    transcript_key,
)
from app.timeutil import utcnow

# APIRouter(prefix=...): every route below is mounted under /files (then /api/v1 on top).
router = APIRouter(prefix="/files", tags=["files"])


def _taxonomy_matches(taxonomy: dict | None, needle: str) -> bool:
    if not taxonomy:
        return False
    lowered = needle.lower()
    for key in ("professional_topics", "personal_topics", "upcoming_events"):
        for item in taxonomy.get(key) or []:
            if lowered in str(item).lower():
                return True
    return False


def _file_item(audio: AudioFile, analysis: Analysis | None) -> dict:
    return {
        "id": str(audio.id),
        "original_filename": audio.original_filename,
        "content_type": audio.content_type,
        "byte_size": audio.byte_size,
        "duration_sec": audio.duration_sec,
        "status": audio.status,
        "stage": audio.stage,
        "skipped_stages": ["layer1", "layer2"] if audio.status == "blocked" else [],
        "error_message": audio.error_message,
        "created_at": audio.created_at,
        "storage_key": audio.storage_key,
        "summary": None if analysis is None else analysis.summary,
        "taxonomy": None if analysis is None else analysis.taxonomy,
        "layer2": None if analysis is None else analysis.layer2,
        "block_reason": None if analysis is None else analysis.block_reason,
        "summary_strategy": None if analysis is None else analysis.summary_strategy,
        "provider": None if analysis is None else analysis.provider,
    }


# Tenant isolation: always filter by user_id so users can only see their own files.
def _owned_file(db: Session, user: User, file_id: uuid.UUID) -> AudioFile:
    audio = (
        db.query(AudioFile)
        .filter(AudioFile.user_id == user.id, AudioFile.id == file_id)
        .one_or_none()
    )
    if audio is None:
        # HTTPException: FastAPI turns this into an HTTP error response (404 JSON body).
        raise HTTPException(status_code=404, detail="Not found")
    return audio


# POST /api/v1/files (201 on success); async because it awaits upload.read().
@router.post("", status_code=201)
async def upload_files(
    # UploadFile + File(...): multipart/form-data upload; `...` means the field is required.
    files: list[UploadFile] = File(...),
    # Depends(...): FastAPI calls these first and injects the results (auth user, DB session).
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="at least one file is required")
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="upload at most 10 files at once")
    settings = get_settings()
    created = []
    for upload in files:
        filename = safe_filename(upload.filename or "audio.wav")
        try:
            ext = safe_extension(filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Reads the whole uploaded file into memory as bytes.
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty file")
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="file too large")
        file_id = uuid.uuid4()
        key = audio_key(user.id, file_id, ext)
        assert_user_key(user.id, key)
        # Store raw audio in blob storage; the DB row only keeps the storage key.
        get_blob_store().upload(key, data, CONTENT_TYPES[ext])
        audio = AudioFile(
            user_id=user.id,
            id=file_id,
            original_filename=filename,
            content_type=CONTENT_TYPES[ext],
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            storage_key=key,
            status="uploaded",
            stage="upload",
            created_at=utcnow(),
        )
        # Commit first so the row exists before the worker looks it up.
        db.add(audio)
        db.commit()
        append_event(db, audio, "File uploaded and saved to blob storage", stage="upload")
        append_event(db, audio, "Row inserted in Postgres", stage="upload")
        db.commit()
        # Hand off to inline run / Celery / Service Bus (see analysis/service.py).
        enqueue_analysis(file_id, user.id)
        db.refresh(audio)
        analysis = (
            db.query(Analysis)
            .filter(Analysis.user_id == user.id, Analysis.file_id == audio.id)
            .one_or_none()
        )
        created.append(_file_item(audio, analysis))
    return {"items": created}


@router.get("")
def list_files(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_duration: float | None = None,
    max_duration: float | None = None,
    taxonomy: str | None = None,
    custom: str | None = None,
    # Query(...) adds validation for query-string params (ge/le = min/max).
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        custom_filter = parse_custom_filter(custom)
    except GuardrailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    query = db.query(AudioFile).filter(AudioFile.user_id == user.id)
    if date_from is not None:
        query = query.filter(AudioFile.created_at >= date_from)
    if date_to is not None:
        query = query.filter(AudioFile.created_at <= date_to)
    if min_duration is not None:
        query = query.filter(AudioFile.duration_sec >= min_duration)
    if max_duration is not None:
        query = query.filter(AudioFile.duration_sec <= max_duration)
    rows = query.order_by(AudioFile.created_at.desc()).all()
    analyses = {
        item.file_id: item for item in db.query(Analysis).filter(Analysis.user_id == user.id).all()
    }
    items = []
    for audio in rows:
        analysis = analyses.get(audio.id)
        if taxonomy and not _taxonomy_matches(
            None if analysis is None else analysis.taxonomy, taxonomy
        ):
            continue
        if custom_filter is not None:
            name, value = custom_filter
            layer2 = None if analysis is None else analysis.layer2
            if not matches_custom(layer2, name, value):
                continue
        items.append(_file_item(audio, analysis))
    total = len(items)
    return {"items": items[offset : offset + limit], "total": total}


# {file_id} is a path parameter; typing it as uuid.UUID makes FastAPI validate it (422).
@router.get("/{file_id}")
def get_file(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    audio = _owned_file(db, user, file_id)
    analysis = (
        db.query(Analysis)
        .filter(Analysis.user_id == user.id, Analysis.file_id == audio.id)
        .one_or_none()
    )
    transcript = (
        db.query(Transcript)
        .filter(Transcript.user_id == user.id, Transcript.file_id == audio.id)
        .one_or_none()
    )
    item = _file_item(audio, analysis)
    item["transcript"] = None if transcript is None else transcript.text
    item["transcript_key"] = None if transcript is None else transcript.storage_key
    return item


@router.get("/{file_id}/audio")
def download_audio(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    audio = _owned_file(db, user, file_id)
    assert_user_key(user.id, audio.storage_key)
    data = get_blob_store().download(audio.storage_key)
    # Streams the raw bytes back with the original content type.
    return Response(content=data, media_type=audio.content_type)


# Re-run the pipeline for an existing file.
@router.post("/{file_id}/analyze")
def reanalyze(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    audio = _owned_file(db, user, file_id)
    audio.status = "uploaded"
    audio.stage = "upload"
    audio.error_message = None
    append_event(db, audio, "Analysis re-queued", stage="upload")
    db.commit()
    enqueue_analysis(audio.id, user.id)
    db.refresh(audio)
    analysis = (
        db.query(Analysis)
        .filter(Analysis.user_id == user.id, Analysis.file_id == audio.id)
        .one_or_none()
    )
    return _file_item(audio, analysis)


# 204 No Content: success with an empty body.
@router.delete("/{file_id}", status_code=204)
def delete_file(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    audio = _owned_file(db, user, file_id)
    store = get_blob_store()
    for key in (
        audio.storage_key,
        transcript_key(user.id, audio.id),
        analysis_key(user.id, audio.id),
    ):
        assert_user_key(user.id, key)
        store.delete(key)
    db.delete(audio)
    db.commit()
