from datetime import datetime

from app.analysis.map_reduce import map_reduce_summaries
from app.config import get_settings
from app.db.models import Analysis, User
from app.db.session import SessionLocal
from app.jobs.summary_job import run_rollup, scheduled_rollup_all_users
from tests.conftest import wav_bytes


def test_map_reduce_calls_summarizer_more_than_once(monkeypatch):
    monkeypatch.setattr(get_settings(), "chunk_chars", 40)
    calls = {"count": 0}

    def summarize(items):
        calls["count"] += 1
        return " | ".join(items)[:40]

    texts = [f"summary number {index} about budget" for index in range(8)]
    result = map_reduce_summaries(texts, summarize)
    assert calls["count"] > 1
    assert result


def test_on_demand_rollup_groups_by_taxonomy(client, auth):
    sample = open("/workspace/samples/sample_call.wav", "rb").read()
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
