"""Fixed tool set for the chat agent. Every query filters on the caller's user id."""

import uuid
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.chat.schemas import GetAnalysisArgs, RunSummaryArgs, SearchFilesArgs
from app.db.models import Analysis, AudioFile
from app.guardrails.safety import get_safety
from app.jobs.summary_job import run_rollup

# Allow-list of tool names the agent may call.
TOOL_NAMES = frozenset({"search_files", "get_analysis", "run_summary"})
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
            "description": "Fetch one recording's summary, taxonomy, and Layer 2 results.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file_id"],
                "properties": {"file_id": {"type": "string", "description": "File UUID"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_summary",
            "description": (
                "Run an on-demand rollup of the user's completed recordings. "
                "group_by is user, taxonomy_label, week, or sentiment."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["group_by"],
                "properties": {
                    "group_by": {
                        "type": "string",
                        "enum": ["user", "taxonomy_label", "week", "sentiment"],
                    },
                    "time_from": {"type": "string"},
                    "time_to": {"type": "string"},
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


def _file_payload(audio: AudioFile, analysis: Analysis | None) -> dict:
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
    return _file_payload(audio, analysis)


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


# Tool name -> Pydantic model that validates that tool's arguments.
_MODELS = {
    "search_files": SearchFilesArgs,
    "get_analysis": GetAnalysisArgs,
    "run_summary": RunSummaryArgs,
}

# Tool name -> Python function that implements it (the dispatch table).
_FUNCS = {
    "search_files": search_files,
    "get_analysis": get_analysis,
    "run_summary": run_summary,
}


# Single dispatcher: validate arguments, then call the mapped function.
def execute_tool(db: Session, user_id: uuid.UUID, name: str, arguments: dict) -> dict:
    if name not in TOOL_NAMES:
        return {"error": "unknown_tool"}
    model = _MODELS[name]
    try:
        # model_validate turns the raw dict into typed args (or raises ValidationError).
        parsed = model.model_validate(arguments or {})
    except ValidationError:
        return {"error": "invalid_arguments"}
    # Looks up the function by name and calls it with (db, user_id, parsed args).
    return _FUNCS[name](db, user_id, parsed)
