from datetime import datetime
from pathlib import Path

from app.analysis.summarize_group import summarize_group
from app.config import get_settings
from app.db.models import Analysis, User
from app.db.session import SessionLocal
from app.jobs.summary_job import run_rollup, scheduled_rollup_all_users
from tests.conftest import wav_bytes

# backend/tests/<file> -> parents[2] is the repo root.
SAMPLE_CALL = Path(__file__).resolve().parents[2] / "samples" / "sample_call.wav"


def test_summarize_group_calls_summarizer_more_than_once(monkeypatch):
    monkeypatch.setattr(get_settings(), "chunk_chars", 40)
    calls = {"count": 0}

    def summarize(items):
        calls["count"] += 1
        return " | ".join(items)[:40]

    texts = [f"summary number {index} about budget" for index in range(8)]
    result = summarize_group(texts, summarize)
    assert calls["count"] > 1
    assert result


def test_on_demand_rollup_groups_by_taxonomy(client, auth):
    sample = SAMPLE_CALL.read_bytes()
    for name in ("one.wav", "two.wav"):
        response = client.post(
            "/api/v1/files",
            headers=auth,
            files=[("files", (name, sample, "audio/wav"))],
        )
        assert response.status_code == 201, response.text
    rollup = client.post(
        "/api/v1/summaries",
        headers=auth,
        json={"group_by": "taxonomy_label"},
    )
    assert rollup.status_code == 201, rollup.text
    result = rollup.json()["result"]
    assert result["file_count"] == 2
    assert rollup.json()["trigger"] == "on_demand"
    keys = {group["key"] for group in result["groups"]}
    assert "budget" in keys
    assert "dentist" in keys
    assert result["overall_summary"]
    listed = client.get("/api/v1/summaries", headers=auth)
    assert listed.json()["items"]
    bad = client.post("/api/v1/summaries", headers=auth, json={"group_by": "password"})
    assert bad.status_code == 400


def test_day_and_month_buckets_and_rejected_template_slot(client, auth):
    for name in ("aug.wav", "sep.wav"):
        response = client.post(
            "/api/v1/files",
            headers=auth,
            files=[("files", (name, wav_bytes(), "audio/wav"))],
        )
        assert response.status_code == 201, response.text
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ada@example.com").one()
        rows = (
            db.query(Analysis)
            .filter(Analysis.user_id == user.id)
            .order_by(Analysis.created_at.asc())
            .all()
        )
        assert len(rows) == 2
        rows[0].created_at = datetime(2026, 8, 30, 12, 0, 0)
        rows[1].created_at = datetime(2026, 9, 2, 12, 0, 0)
        db.commit()
    finally:
        db.close()
    rejected = client.post(
        "/api/v1/summaries",
        headers=auth,
        json={"template_id": "trend", "slot": "year"},
    )
    assert rejected.status_code == 400
    unknown = client.post(
        "/api/v1/summaries",
        headers=auth,
        json={"group_by": "year"},
    )
    assert unknown.status_code == 400
    month = client.post(
        "/api/v1/summaries",
        headers=auth,
        json={"template_id": "trend", "slot": "month"},
    )
    assert month.status_code == 201, month.text
    result = month.json()["result"]
    assert result["group_by"] == "month"
    assert result["file_count"] == 2
    assert {group["key"] for group in result["groups"]} == {"2026-08", "2026-09"}
    counts = {group["key"]: group["file_count"] for group in result["groups"]}
    assert counts == {"2026-08": 1, "2026-09": 1}
    day = client.post("/api/v1/summaries", headers=auth, json={"group_by": "day"})
    assert day.status_code == 201, day.text
    assert {group["key"] for group in day.json()["result"]["groups"]} == {
        "2026-08-30",
        "2026-09-02",
    }
    topic = client.post("/api/v1/summaries", headers=auth, json={"template_id": "by_topic"})
    assert topic.status_code == 201, topic.text
    assert topic.json()["result"]["group_by"] == "taxonomy_label"


def test_time_range_and_scheduled_rollup_are_idempotent_within_interval(client, auth):
    uploaded = client.post(
        "/api/v1/files",
        headers=auth,
        files=[("files", ("note.wav", wav_bytes(), "audio/wav"))],
    )
    assert uploaded.status_code == 201
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ada@example.com").one()
        analysis = db.query(Analysis).filter(Analysis.user_id == user.id).one()
        analysis.created_at = datetime(2020, 1, 1, 12, 0, 0)
        db.commit()
        empty = run_rollup(
            db,
            user.id,
            "user",
            datetime(2024, 1, 1),
            datetime(2024, 2, 1),
            "on_demand",
        )
        assert empty.result["file_count"] == 0
        analysis.created_at = datetime(2024, 1, 15, 12, 0, 0)
        db.commit()
        window = run_rollup(
            db,
            user.id,
            "week",
            datetime(2024, 1, 1),
            datetime(2024, 2, 1),
            "on_demand",
        )
        assert window.result["file_count"] == 1
        assert window.result["groups"][0]["key"].startswith("2024-W")
        first = scheduled_rollup_all_users(db)
        second = scheduled_rollup_all_users(db)
        assert first == 1
        assert second == 0
    finally:
        db.close()


def test_group_summaries_run_in_parallel_and_each_group_gets_its_own_call(monkeypatch):
    import threading
    import time as clock

    from app.analysis.summarize_group import summarize_groups
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "summary_workers", 4)
    threads: set[int] = set()
    seen: list[tuple[str, ...]] = []

    def summarize(texts):
        threads.add(threading.get_ident())
        seen.append(tuple(texts))
        clock.sleep(0.05)
        return "summary of " + " / ".join(texts)

    groups = {f"label {index}": [f"note {index}"] for index in range(8)}
    started = clock.monotonic()
    written = summarize_groups(groups, summarize)
    elapsed = clock.monotonic() - started
    assert written == {key: f"summary of {texts[0]}" for key, texts in groups.items()}
    assert sorted(seen) == sorted((texts[0],) for texts in groups.values())
    assert len(threads) > 1
    assert elapsed < 0.05 * 8
