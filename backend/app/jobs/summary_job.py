import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.analysis.map_reduce import map_reduce_summaries
from app.analysis.providers.factory import get_intelligence
from app.config import get_settings
from app.db.models import Analysis, RollupSummary
from app.db.session import SessionLocal
from app.guardrails.catalog import GROUP_BY_OPTIONS
from app.timeutil import utcnow

logger = logging.getLogger(__name__)


def _groups(rows: list[Analysis], group_by: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    if group_by == "user":
        groups["all"] = [row.summary or "" for row in rows]
        return groups
    if group_by == "taxonomy_label":
        for row in rows:
            taxonomy = row.taxonomy or {}
            labels: list[str] = []
            for key in ("professional_topics", "personal_topics", "upcoming_events"):
                labels.extend(taxonomy.get(key) or [])
            if not labels:
                labels = ["unlabeled"]
            for label in labels:
                groups.setdefault(str(label), []).append(row.summary or "")
        return groups
    if group_by == "week":
        for row in rows:
            iso = row.created_at.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            groups.setdefault(key, []).append(row.summary or "")
        return groups
    if group_by == "sentiment":
        for row in rows:
            label = ((row.layer2 or {}).get("sentiment_lexicon") or {}).get("label") or "unknown"
            groups.setdefault(str(label), []).append(row.summary or "")
        return groups
    raise ValueError("unsupported group_by")


def run_rollup(
    db: Session,
    user_id: uuid.UUID,
    group_by: str,
    time_from: datetime | None,
    time_to: datetime | None,
    trigger: str,
) -> RollupSummary:
    if group_by not in GROUP_BY_OPTIONS:
        raise ValueError("unsupported group_by")
    if trigger not in {"schedule", "on_demand"}:
        raise ValueError("unsupported trigger")
    query = db.query(Analysis).filter(
        Analysis.user_id == user_id,
        Analysis.status == "completed",
    )
    if time_from is not None:
        query = query.filter(Analysis.created_at >= time_from)
    if time_to is not None:
        query = query.filter(Analysis.created_at <= time_to)
    rows = query.order_by(Analysis.created_at.asc()).all()
    provider = get_intelligence()
    grouped = _groups(rows, group_by)
    built = []
    for key, summaries in sorted(grouped.items()):
        built.append(
            {
                "key": key,
                "file_count": len(summaries),
                "summary": map_reduce_summaries(summaries, provider.rollup_summary),
            }
        )
    overall = map_reduce_summaries([item["summary"] for item in built], provider.rollup_summary)
    result = {
        "group_by": group_by,
        "time_from": time_from.isoformat() if time_from else None,
        "time_to": time_to.isoformat() if time_to else None,
        "file_count": len(rows),
        "groups": built,
        "overall_summary": overall,
        "provider": get_settings().llm_provider,
    }
    row = RollupSummary(
        user_id=user_id,
        id=uuid.uuid4(),
        group_by=group_by,
        time_from=time_from,
        time_to=time_to,
        trigger=trigger,
        result=result,
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def scheduled_rollup_all_users(db: Session | None = None) -> int:
    owns_session = db is None
    if db is None:
        db = SessionLocal()
    try:
        cutoff = utcnow() - timedelta(seconds=get_settings().rollup_schedule_seconds)
        user_ids = [
            item[0]
            for item in db.query(Analysis.user_id)
            .filter(Analysis.status == "completed")
            .distinct()
            .all()
        ]
        created = 0
        for user_id in user_ids:
            latest = (
                db.query(RollupSummary)
                .filter(
                    RollupSummary.user_id == user_id,
                    RollupSummary.trigger == "schedule",
                    RollupSummary.group_by == "user",
                )
                .order_by(RollupSummary.created_at.desc())
                .first()
            )
            if latest is not None and latest.created_at >= cutoff:
                continue
            run_rollup(db, user_id, "user", None, None, "schedule")
            created += 1
        logger.info("scheduled rollups created: %s", created)
        return created
    finally:
        if owns_session:
            db.close()
