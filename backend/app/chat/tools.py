"""Fixed tool set for the chat agent. Every query filters on the caller's user id."""

import logging
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analysis.audio_features import ensure_pcm16_wav
from app.analysis.mcp_audio import fetch_audio_via_mcp
from app.analysis.providers.intelligence import get_intelligence
from app.analysis.speaker_skill import acoustic_profile
from app.chat.schemas import (
    GetAnalysisArgs,
    ProfileSpeakerArgs,
    RunSummaryArgs,
    SearchFilesArgs,
)
from app.config import get_settings
from app.db.models import Analysis, AudioFile, Transcript
from app.guardrails.safety import get_safety
from app.jobs.summary_job import run_rollup
from app.storage.blob import get_blob_store

logger = logging.getLogger(__name__)

# Allow-list of tool names the agent may call.
TOOL_NAMES = frozenset({"search_files", "get_analysis", "run_summary", "profile_speaker"})
_SEARCH_LIMIT = 20

# Tool schemas in OpenAI function-calling format: name, description, and JSON Schema
# parameters. Sent to the model so it knows which tools exist and how to call them.
TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "List the authenticated user's recordings. "
                "Filters: date_from, date_to, min_duration, max_duration, taxonomy."
            ),
            "parameters": {
                "type": "object",
                # additionalProperties: False = the model can't invent extra arguments.
                "additionalProperties": False,
                "properties": {
                    "date_from": {"type": "string", "description": "ISO timestamp, inclusive"},
                    "date_to": {"type": "string", "description": "ISO timestamp, inclusive"},
                    "min_duration": {
                        "type": "number",
                        "description": "Minimum duration in seconds",
                    },
                    "max_duration": {
                        "type": "number",
                        "description": "Maximum duration in seconds",
                    },
                    "taxonomy": {"type": "string", "description": "Substring of a taxonomy label"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_analysis",
            "description": "Fetch one recording's summary, taxonomy, and Analytics results.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file_id"],
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "File UUID or the stored filename",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_summary",
            "description": (
                "Run an on-demand rollup of the user's completed recordings. "
                "group_by is user, taxonomy_label, day, week, month, or sentiment."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["group_by"],
                "properties": {
                    "group_by": {
                        "type": "string",
                        "enum": ["user", "taxonomy_label", "day", "week", "month", "sentiment"],
                    },
                    "time_from": {"type": "string"},
                    "time_to": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "profile_speaker",
            "description": (
                "Run the speaker-profile skill on one recording's stored audio. "
                "Use this when the user wants a speaker profile or to analyze the voice. "
                "file_id is optional. Omit it to fetch the newest recording from storage. "
                "A stored filename is also accepted."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "File UUID or stored filename. Omit for the newest.",
                    },
                },
            },
        },
    },
]


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _clip(text: str | None, limit: int = 400) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= limit:
        return cleaned
    shortened = cleaned[:limit].rsplit(" ", 1)[0]
    return shortened or cleaned[:limit]


def _taxonomy_matches(taxonomy: dict | None, needle: str) -> bool:
    if not taxonomy:
        return False
    lowered = needle.lower()
    for key in ("professional_topics", "personal_topics", "upcoming_events"):
        for item in taxonomy.get(key) or []:
            if lowered in str(item).lower():
                return True
    return False


BLOCKED_NOTE = "This recording was blocked by the content safety check."


def _is_blocked(audio: AudioFile, analysis: Analysis | None) -> bool:
    return audio.status == "blocked" or (analysis is not None and analysis.status == "blocked")


def _blocked_payload(audio: AudioFile, analysis: Analysis | None) -> dict:
    """Metadata only. A blocked file's transcript and analysis never reach the model."""
    return {
        "id": str(audio.id),
        "filename": audio.original_filename,
        "status": "blocked",
        "duration_sec": audio.duration_sec,
        "created_at": audio.created_at.isoformat(timespec="seconds")
        if audio.created_at is not None
        else None,
        "block_reason": None if analysis is None else analysis.block_reason,
        "note": BLOCKED_NOTE,
    }


def _file_payload(audio: AudioFile, analysis: Analysis | None) -> dict:
    if _is_blocked(audio, analysis):
        return _blocked_payload(audio, analysis)
    taxonomy = None if analysis is None else analysis.taxonomy
    return {
        "id": str(audio.id),
        "filename": audio.original_filename,
        "status": audio.status,
        "duration_sec": audio.duration_sec,
        "created_at": audio.created_at.isoformat(timespec="seconds")
        if audio.created_at is not None
        else None,
        "summary": None if analysis is None else _clip(analysis.summary),
        "taxonomy": taxonomy,
        "layer2": None if analysis is None else analysis.layer2,
        "block_reason": None if analysis is None else analysis.block_reason,
    }


# Tool implementation: every query is filtered by user_id (tenant isolation).
def search_files(db: Session, user_id: uuid.UUID, args: SearchFilesArgs) -> dict:
    start = _naive(args.date_from)
    end = _naive(args.date_to)
    if start is not None and end is not None and start > end:
        return {"error": "invalid_arguments"}
    if (
        args.min_duration is not None
        and args.max_duration is not None
        and args.min_duration > args.max_duration
    ):
        return {"error": "invalid_arguments"}
    # Free-text filter is screened by content safety before it's used.
    if args.taxonomy and get_safety().analyze_content(args.taxonomy).blocked:
        return {"error": "rejected"}
    query = db.query(AudioFile).filter(AudioFile.user_id == user_id)
    if start is not None:
        query = query.filter(AudioFile.created_at >= start)
    if end is not None:
        query = query.filter(AudioFile.created_at <= end)
    if args.min_duration is not None:
        query = query.filter(AudioFile.duration_sec >= args.min_duration)
    if args.max_duration is not None:
        query = query.filter(AudioFile.duration_sec <= args.max_duration)
    rows = query.order_by(AudioFile.created_at.desc()).all()
    analyses = {
        item.file_id: item for item in db.query(Analysis).filter(Analysis.user_id == user_id).all()
    }
    matched: list[dict] = []
    for audio in rows:
        analysis = analyses.get(audio.id)
        if args.taxonomy and not _taxonomy_matches(
            None if analysis is None else analysis.taxonomy, args.taxonomy
        ):
            continue
        matched.append(_file_payload(audio, analysis))
    # Result capped at _SEARCH_LIMIT items to keep the LLM prompt small.
    return {"items": matched[:_SEARCH_LIMIT], "total": len(matched)}


# Tool implementation: one file's analysis, only if it belongs to this user.
def get_analysis(db: Session, user_id: uuid.UUID, args: GetAnalysisArgs) -> dict:
    audio = (
        db.query(AudioFile)
        .filter(AudioFile.user_id == user_id, AudioFile.id == args.file_id)
        .one_or_none()
    )
    if audio is None:
        return {"error": "not_found"}
    analysis = (
        db.query(Analysis)
        .filter(Analysis.user_id == user_id, Analysis.file_id == audio.id)
        .one_or_none()
    )
    transcript = (
        db.query(Transcript)
        .filter(Transcript.user_id == user_id, Transcript.file_id == audio.id)
        .one_or_none()
    )
    result = _file_payload(audio, analysis)
    if _is_blocked(audio, analysis):
        return result
    if transcript is not None and transcript.text:
        result["transcript"] = _clip(transcript.text, 1200)
    return result


# Tool implementation: on-demand rollup, trimmed so it fits in the prompt.
def run_summary(db: Session, user_id: uuid.UUID, args: RunSummaryArgs) -> dict:
    try:
        row = run_rollup(
            db,
            user_id,
            args.group_by,
            _naive(args.time_from),
            _naive(args.time_to),
            "on_demand",
        )
    except ValueError:
        return {"error": "invalid_arguments"}
    result = dict(row.result or {})
    result["id"] = str(row.id)
    if isinstance(result.get("overall_summary"), str):
        result["overall_summary"] = _clip(result["overall_summary"], 800)
    groups = []
    for group in (result.get("groups") or [])[:8]:
        groups.append(
            {
                "key": group.get("key"),
                "file_count": group.get("file_count"),
                "summary": _clip(group.get("summary"), 300),
            }
        )
    result["groups"] = groups
    return result


def _stored_file(db: Session, user_id: uuid.UUID, name: str) -> AudioFile | None:
    """The account's recording with this filename, newest first."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if not base:
        return None
    return (
        db.query(AudioFile)
        .filter(AudioFile.user_id == user_id)
        .filter(func.lower(AudioFile.original_filename) == base)
        .order_by(AudioFile.created_at.desc())
        .first()
    )


_FILENAME = re.compile(r"([A-Za-z0-9][A-Za-z0-9 _\-.()]*?\.(?:wav|mp3|m4a|ogg|flac))\b", re.I)


def named_filename(message: str) -> str | None:
    """A recording filename written in the question itself, such as audio_2.mp3."""
    for line in (message or "").splitlines():
        if line.strip().lower().startswith("file_id="):
            continue
        found = _FILENAME.findall(line)
        if found:
            return found[0].strip().rsplit(" ", 1)[-1]
    return None


def _same_name(db: Session, user_id: uuid.UUID, file_id: str, filename: str) -> bool:
    """True when file_id is one of this account's recordings with this filename."""
    try:
        wanted = uuid.UUID(file_id)
    except ValueError:
        return False
    row = (
        db.query(AudioFile.original_filename)
        .filter(AudioFile.user_id == user_id, AudioFile.id == wanted)
        .one_or_none()
    )
    return row is not None and str(row[0]).lower() == filename.lower()


def _local_time(value: object) -> str:
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(get_settings().display_timezone)
    except Exception:
        zone = timezone.utc
    return stamp.astimezone(zone).strftime("%H:%M")


def label_files(db: Session, user_id: uuid.UUID, result: object) -> dict[str, str]:
    """Give every recording in a tool result a readable label: its filename, plus the
    upload time when the account has several recordings with that name. Returns
    id -> label, so a reply can never show a raw id."""
    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("filename") and (node.get("id") or node.get("file_id")):
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(result)
    if not found:
        return {}
    counts = dict(
        db.query(func.lower(AudioFile.original_filename), func.count(AudioFile.id))
        .filter(AudioFile.user_id == user_id)
        .group_by(func.lower(AudioFile.original_filename))
        .all()
    )
    labels: dict[str, str] = {}
    for item in found:
        name = str(item["filename"])
        count = int(counts.get(name.lower(), 1))
        label = name
        if count > 1:
            when = _local_time(item.get("created_at"))
            if not when:
                row = (
                    db.query(AudioFile.created_at)
                    .filter(
                        AudioFile.user_id == user_id,
                        AudioFile.id == uuid.UUID(str(item.get("id") or item.get("file_id"))),
                    )
                    .one_or_none()
                )
                when = _local_time(row[0].isoformat()) if row and row[0] else ""
            label = f"{name} (uploaded {when})" if when else name
            item["same_name_count"] = count
        item["label"] = label
        labels[str(item.get("id") or item.get("file_id"))] = label
    return labels


def _pinned_file_id(message: str) -> str | None:
    for line in (message or "").splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("file_id="):
            continue
        raw = stripped.split("=", 1)[1].strip()
        try:
            return str(uuid.UUID(raw))
        except ValueError:
            return None
    return None


def prepare_arguments(
    db: Session | None,
    user_id: uuid.UUID,
    name: str,
    arguments: dict | None,
    message: str = "",
) -> dict:
    """Use a stored file when the tool was given a filename instead of a UUID."""
    cleaned = dict(arguments or {})
    if name not in {"get_analysis", "profile_speaker"}:
        return cleaned
    raw = cleaned.get("file_id")
    text = "" if raw is None else str(raw).strip()
    # A filename written in this question wins over a file from history or the model.
    # With several recordings of that name, the newest is used.
    named = named_filename(message)
    if named and db is not None:
        match = _stored_file(db, user_id, named)
        if match is not None and not _same_name(db, user_id, text, match.original_filename):
            cleaned["file_id"] = str(match.id)
            return cleaned
    if text:
        try:
            cleaned["file_id"] = str(uuid.UUID(text))
            return cleaned
        except ValueError:
            pass
        if db is not None:
            found = _stored_file(db, user_id, text)
            if found is not None:
                cleaned["file_id"] = str(found.id)
                return cleaned
    pinned = _pinned_file_id(message)
    if pinned:
        cleaned["file_id"] = pinned
        return cleaned
    if name == "profile_speaker":
        cleaned.pop("file_id", None)
    return cleaned


def _fetch_audio(storage_key: str) -> tuple[bytes | None, str]:
    """This recording's audio, fetched fresh for this turn, and how: "mcp" through the MCP
    fetch_audio tool (as transcription does), or "blob" when MCP is off or errored."""
    url = get_settings().mcp_audio_url
    if url:
        try:
            logger.info("chat MCP tool fetch_audio %s", storage_key)
            return fetch_audio_via_mcp(url, storage_key), "mcp"
        except Exception:
            logger.warning("MCP fetch_audio failed; reading the same blob directly", exc_info=True)
    try:
        return get_blob_store().download(storage_key), "blob"
    except FileNotFoundError:
        return None, "blob"


def profile_speaker(db: Session, user_id: uuid.UUID, args: ProfileSpeakerArgs) -> dict:
    query = db.query(AudioFile).filter(AudioFile.user_id == user_id)
    if args.file_id is not None:
        query = query.filter(AudioFile.id == args.file_id)
    audio = query.order_by(AudioFile.created_at.desc()).first()
    if audio is None:
        return {"error": "not_found"}
    analysis = (
        db.query(Analysis)
        .filter(Analysis.user_id == user_id, Analysis.file_id == audio.id)
        .one_or_none()
    )
    if _is_blocked(audio, analysis):
        # The audio carries the same content the safety check blocked. Do not send it.
        blocked = _blocked_payload(audio, analysis)
        blocked["file_id"] = blocked.pop("id")
        return blocked
    data, source = _fetch_audio(audio.storage_key)
    if data is None:
        return {"error": "not_found"}
    playable = ensure_pcm16_wav(data)
    try:
        result = get_intelligence().speaker_profile(playable, audio.original_filename)
    except Exception:
        logger.warning("speaker profile fell back to the audio measure", exc_info=True)
        result = acoustic_profile(playable, audio.original_filename)
    result["filename"] = audio.original_filename
    result["audio_fetched_via"] = source
    result["file_id"] = str(audio.id)
    return result


# Tool name -> Pydantic model that validates that tool's arguments.
_MODELS = {
    "search_files": SearchFilesArgs,
    "get_analysis": GetAnalysisArgs,
    "run_summary": RunSummaryArgs,
    "profile_speaker": ProfileSpeakerArgs,
}

# Tool name -> Python function that implements it (the dispatch table).
_FUNCS = {
    "search_files": search_files,
    "get_analysis": get_analysis,
    "run_summary": run_summary,
    "profile_speaker": profile_speaker,
}


# Single dispatcher: validate arguments, then call the mapped function.
def execute_tool(
    db: Session,
    user_id: uuid.UUID,
    name: str,
    arguments: dict,
    message: str = "",
) -> dict:
    if name not in TOOL_NAMES:
        return {"error": "unknown_tool"}
    arguments = prepare_arguments(db, user_id, name, arguments, message)
    model = _MODELS[name]
    try:
        # model_validate turns the raw dict into typed args (or raises ValidationError).
        parsed = model.model_validate(arguments or {})
    except ValidationError:
        return {"error": "invalid_arguments"}
    # Looks up the function by name and calls it with (db, user_id, parsed args).
    result = _FUNCS[name](db, user_id, parsed)
    label_files(db, user_id, result)
    return result
