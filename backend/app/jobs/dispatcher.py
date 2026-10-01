"""Shared job dispatcher for Celery and the Service Bus worker."""

from app.analysis.service import run_file_analysis
from app.db.session import SessionLocal
from app.guardrails.catalog import GROUP_BY_OPTIONS
from app.jobs.summary_job import run_rollup, scheduled_rollup_all_users

_ANALYSIS_KINDS = {"analyze_file", "transcription", "llm-layer1", "llm-layer2"}


def dispatch(message: dict, queue: str | None = None) -> None:
    if not isinstance(message, dict):
        raise ValueError("message must be an object")
    kind = str(message.get("kind") or queue or "")
    if kind in _ANALYSIS_KINDS:
        file_id = message.get("file_id")
        user_id = message.get("user_id")
        if not file_id or not user_id:
            raise ValueError("file_id and user_id are required")
        run_file_analysis(str(file_id), str(user_id))
        return
    if kind == "rollup":
        user_id = message.get("user_id")
        group_by = message.get("group_by") or "user"
        if not user_id:
            raise ValueError("user_id is required")
        if group_by not in GROUP_BY_OPTIONS:
            raise ValueError("unsupported group_by")
        db = SessionLocal()
        try:
            import uuid

            run_rollup(
                db,
                uuid.UUID(str(user_id)),
                str(group_by),
                None,
                None,
                str(message.get("trigger") or "on_demand"),
            )
        finally:
            db.close()
        return
    if kind == "group_report":
        from app.jobs.report_job import run_group_report

        report_id = message.get("report_id")
        user_id = message.get("user_id")
        if not report_id or not user_id:
            raise ValueError("report_id and user_id are required")
        run_group_report(str(report_id), str(user_id))
        return
    if kind == "scheduled_rollup":
        scheduled_rollup_all_users()
        return
    raise ValueError(f"unknown job kind: {kind}")
