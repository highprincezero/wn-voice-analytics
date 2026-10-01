"""All groupings report (Offline Collective Analysis).

One background job builds one stored report over the user's completed files for every
grouping: day, week, month, all files, taxonomy label, sentiment, and the Analytics
groupings tone, pace band, key entity, and action items.

Files are grouped in code so groups are exact, and the numbers (counts, durations,
averages, mixes) are computed in code. The AI writes the summary for every group, with
the fixed hardened rollup prompt over the files' Insights summaries. Blocked files are
never read: only completed analyses are loaded.
"""

import logging
import uuid
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session

from app.analysis.providers.intelligence import get_intelligence
from app.analysis.summarize_group import summarize_groups
from app.config import get_settings
from app.db.models import Analysis, GroupReport
from app.db.session import SessionLocal
from app.guardrails.catalog import REPORT_GROUPINGS
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

GROUPING_LABELS = {
    "day": "Day",
    "week": "Week",
    "month": "Month",
    "user": "All files",
    "taxonomy_label": "Topic",
    "sentiment": "Sentiment",
    "tone": "Tone",
    "pace_band": "Pace",
    "key_entity": "Key entity",
    "action_items": "Action items",
}

# Bound the number of model calls on accounts with many labels or days.


def validate_groupings(groupings: list[str] | None) -> list[str]:
    """Whitelist check. None or empty means every grouping, in the fixed order."""
    if not groupings:
        return list(REPORT_GROUPINGS)
    if not isinstance(groupings, list) or len(groupings) > len(REPORT_GROUPINGS):
        raise ValueError("unsupported groupings")
    chosen = set()
    for name in groupings:
        if name not in REPORT_GROUPINGS:
            raise ValueError("unsupported grouping")
        chosen.add(name)
    return [name for name in REPORT_GROUPINGS if name in chosen]


def _layer2(row: Analysis, option: str) -> dict:
    value = (row.layer2 or {}).get(option)
    if not isinstance(value, dict) or value.get("skipped"):
        return {}
    return value


def pace_band(words_per_minute: object) -> str | None:
    try:
        value = float(words_per_minute)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value < 110:
        return "slow (under 110 wpm)"
    if value <= 170:
        return "conversational (110 to 170 wpm)"
    return "brisk (over 170 wpm)"


def group_keys(row: Analysis, grouping: str) -> list[str]:
    """Which groups one file belongs to. Deterministic code, no model."""
    created: datetime = row.created_at
    if grouping == "day":
        return [created.strftime("%Y-%m-%d")]
    if grouping == "week":
        iso = created.isocalendar()
        return [f"{iso.year}-W{iso.week:02d}"]
    if grouping == "month":
        return [created.strftime("%Y-%m")]
    if grouping == "user":
        return ["all files"]
    if grouping == "taxonomy_label":
        taxonomy = row.taxonomy or {}
        labels: list[str] = []
        for key in ("professional_topics", "personal_topics", "upcoming_events"):
            for label in taxonomy.get(key) or []:
                if str(label) not in labels:
                    labels.append(str(label))
        return labels or ["unlabeled"]
    if grouping == "sentiment":
        return [str(_layer2(row, "sentiment_lexicon").get("label") or "unknown")]
    if grouping == "tone":
        return [str(_layer2(row, "tone").get("label") or "unknown")]
    if grouping == "pace_band":
        return [pace_band(_layer2(row, "speaking_pace").get("words_per_minute")) or "unknown"]
    if grouping == "key_entity":
        entities = _layer2(row, "key_entities")
        names: list[str] = []
        for key in ("people", "organizations", "places"):
            for name in entities.get(key) or []:
                if str(name) not in names:
                    names.append(str(name))
        return names or ["none named"]
    if grouping == "action_items":
        actions = _layer2(row, "action_items")
        if "items" not in actions:
            return ["unknown"]
        return ["with action items" if actions.get("items") else "no action items"]
    raise ValueError("unsupported grouping")


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def group_stats(rows: list[Analysis]) -> dict:
    """Numbers for one group. All computed in code from stored results."""
    durations = [float(row.duration_sec or 0) for row in rows]
    wpm = [
        float(value)
        for row in rows
        if (value := _layer2(row, "speaking_pace").get("words_per_minute")) is not None
    ]
    rms = [
        float(value)
        for row in rows
        if (value := _layer2(row, "rms_energy").get("rms_mean")) is not None
    ]
    sentiment = Counter(
        str(_layer2(row, "sentiment_lexicon").get("label"))
        for row in rows
        if _layer2(row, "sentiment_lexicon").get("label")
    )
    tone = Counter(
        str(_layer2(row, "tone").get("label")) for row in rows if _layer2(row, "tone").get("label")
    )
    actions = sum(len(_layer2(row, "action_items").get("items") or []) for row in rows)
    return {
        "file_count": len(rows),
        "total_duration_sec": round(sum(durations), 3),
        "avg_duration_sec": _average(durations),
        "avg_words_per_minute": _average(wpm),
        "avg_rms_mean": _average(rms),
        "sentiment_mix": dict(sorted(sentiment.items())),
        "tone_mix": dict(sorted(tone.items())),
        "action_item_count": actions,
    }


def build_report(rows: list[Analysis], groupings: list[str], summarize) -> dict:
    """Group in code, compute numbers in code, and ask the AI for every group's summary.

    Every group in every grouping gets an AI-written summary; none are dropped.
    summarize(list_of_summaries) -> str is the fixed rollup prompt call. Groups with the
    same files reuse that one written summary, since the model input is identical.
    """

    def members_key(members: list[Analysis]) -> str:
        return ",".join(sorted(str(row.id) for row in members))

    # Bucket in code first, so every distinct member set is known up front.
    bucketed: list[tuple[str, list[tuple[str, list[Analysis]]]]] = []
    for grouping in groupings:
        buckets: dict[str, list[Analysis]] = {}
        for row in rows:
            for key in group_keys(row, grouping):
                buckets.setdefault(key, []).append(row)
        ordered = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
        if grouping in {"day", "week", "month"}:
            ordered = sorted(buckets.items())
        bucketed.append((grouping, ordered))

    # One AI summary per distinct member set, written in parallel.
    texts: dict[str, list[str]] = {}
    for _grouping, ordered in bucketed:
        for _key, members in ordered:
            texts.setdefault(members_key(members), [row.summary or "" for row in members])
    written = summarize_groups(texts, summarize)
    calls = len(written)

    sections = []
    for grouping, ordered in bucketed:
        groups = []
        for key, members in ordered:
            groups.append(
                {
                    "key": key,
                    **group_stats(members),
                    "summary": written[members_key(members)],
                }
            )
        sections.append(
            {
                "grouping": grouping,
                "label": GROUPING_LABELS[grouping],
                "groups": groups,
            }
        )
    return {"sections": sections, "summary_calls": calls}


def create_report(
    db: Session,
    user_id: uuid.UUID,
    groupings: list[str] | None,
    time_from: datetime | None,
    time_to: datetime | None,
) -> GroupReport:
    row = GroupReport(
        user_id=user_id,
        id=uuid.uuid4(),
        status="queued",
        groupings=validate_groupings(groupings),
        time_from=time_from,
        time_to=time_to,
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def run_group_report(report_id: str, user_id: str, db: Session | None = None) -> None:
    """The job body. Runs in the worker (or inline in tests). Marks failures, never loops."""
    owns_session = db is None
    if db is None:
        db = SessionLocal()
    try:
        uid = uuid.UUID(str(user_id))
        rid = uuid.UUID(str(report_id))
        report = (
            db.query(GroupReport)
            .filter(GroupReport.user_id == uid, GroupReport.id == rid)
            .one_or_none()
        )
        if report is None:
            logger.warning("group report %s is missing", report_id)
            return
        report.status = "running"
        db.commit()
        try:
            groupings = validate_groupings(list(report.groupings or []))
            query = db.query(Analysis).filter(
                Analysis.user_id == uid,
                # Blocked and failed files are never read, so their text never reaches a model.
                Analysis.status == "completed",
            )
            if report.time_from is not None:
                query = query.filter(Analysis.created_at >= report.time_from)
            if report.time_to is not None:
                query = query.filter(Analysis.created_at <= report.time_to)
            rows = query.order_by(Analysis.created_at.asc()).all()
            blocked = (
                db.query(Analysis)
                .filter(Analysis.user_id == uid, Analysis.status == "blocked")
                .count()
            )
            built = build_report(rows, groupings, get_intelligence().rollup_summary)
            report.result = {
                "file_count": len(rows),
                "blocked_skipped": blocked,
                "groupings": groupings,
                "time_from": report.time_from.isoformat() if report.time_from else None,
                "time_to": report.time_to.isoformat() if report.time_to else None,
                "provider": get_settings().llm_provider,
                **built,
            }
            report.status = "completed"
            report.finished_at = utcnow()
            db.commit()
        except Exception as exc:
            db.rollback()
            report.status = "failed"
            report.error_message = type(exc).__name__[:200]
            report.finished_at = utcnow()
            db.commit()
            logger.exception("group report %s failed", report_id)
    finally:
        if owns_session:
            db.close()


def enqueue_group_report(report_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Same job routing as file analysis: inline, Service Bus, or Celery."""
    settings = get_settings()
    if settings.analysis_mode == "inline":
        run_group_report(str(report_id), str(user_id))
        return
    body = {"kind": "group_report", "report_id": str(report_id), "user_id": str(user_id)}
    if settings.broker == "servicebus":
        from app.jobs.publisher import publish_job

        publish_job("rollup", body)
        return
    from app.jobs.tasks import group_report_task

    group_report_task.apply_async((str(report_id), str(user_id)))
