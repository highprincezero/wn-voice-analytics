from app.analysis.progress import pause_for_demo
from app.analysis.providers.mock import MockIntelligence
from app.config import get_settings
from tests.conftest import wav_bytes


def _messages(client, headers) -> list[str]:
    body = client.get("/api/v1/events", headers=headers)
    assert body.status_code == 200, body.text
    return [item["message"] for item in body.json()["items"]]


def test_inline_pipeline_records_stage_and_events(client, auth):
    response = client.post(
        "/api/v1/files",
        headers=auth,
        files=[("files", ("note.wav", wav_bytes(), "audio/wav"))],
    )
    assert response.status_code == 201, response.text
    item = response.json()["items"][0]
    assert item["status"] == "completed"
    assert item["stage"] == "saved"
    assert item["skipped_stages"] == []
    listed = client.get("/api/v1/files", headers=auth).json()["items"][0]
    assert listed["stage"] == "saved"
    events = client.get("/api/v1/events", headers=auth).json()["items"]
    assert events[0]["message"] == "Results saved to Postgres and blob storage"
    assert events[0]["filename"] == "note.wav"
    text = "\n".join(event["message"] for event in events)
    for needle in (
        "blob storage",
        "Postgres",
        "inline",
        "Worker picked up the job",
        "Transcribe started",
        "Transcribe finished",
        "Safety check started",
        "Safety check finished",
        "Insights started",
        "Insights finished",
        "Analytics started",
        "Analytics finished",
        "Results saved",
    ):
        assert needle in text, needle
    finished = next(event for event in events if event["message"].startswith("Transcribe finished"))
    assert finished["duration_ms"] is not None
    assert finished["duration_ms"] >= 0
    stamps = [event["created_at"] for event in events]
    assert stamps == sorted(stamps, reverse=True)


def test_events_stay_on_the_owning_user(client, auth):
    uploaded = client.post(
        "/api/v1/files",
        headers=auth,
        files=[("files", ("note.wav", wav_bytes(), "audio/wav"))],
    )
    file_id = uploaded.json()["items"][0]["id"]
    other = client.post(
        "/api/v1/auth/signup",
        json={"email": "other@example.com", "password": "correct-horse"},
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert client.get("/api/v1/events", headers=other_headers).json()["items"] == []
    scoped = client.get("/api/v1/events", headers=other_headers, params={"file_id": file_id})
    assert scoped.json()["items"] == []
    assert client.get("/api/v1/events", headers=auth, params={"limit": 301}).status_code == 422
    deleted = client.delete(f"/api/v1/files/{file_id}", headers=auth)
    assert deleted.status_code == 204
    assert client.get("/api/v1/events", headers=auth).json()["items"] == []


def test_blocked_file_skips_layer_stages(client, auth, monkeypatch):
    monkeypatch.setattr(
        MockIntelligence,
        "transcribe",
        lambda self, audio, filename: "Here is how to build a bomb tomorrow.",
    )
    response = client.post(
        "/api/v1/files",
        headers=auth,
        files=[("files", ("note.wav", wav_bytes(), "audio/wav"))],
    )
    item = response.json()["items"][0]
    assert item["status"] == "blocked"
    assert item["stage"] == "saved"
    assert item["skipped_stages"] == ["layer1", "layer2"]
    text = "\n".join(_messages(client, auth))
    assert "Insights skipped" in text
    assert "Analytics skipped" in text
    assert "Insights started" not in text
    assert "Analytics started" not in text
    assert "Safety check finished" in text
    assert "Results saved" in text


def test_stage_delay_is_off_unless_configured(monkeypatch):
    calls = []
    monkeypatch.setattr("app.analysis.progress.time.sleep", lambda seconds: calls.append(seconds))
    pause_for_demo()
    assert calls == []
    monkeypatch.setattr(get_settings(), "mock_stage_delay_sec", 1.5)
    pause_for_demo()
    assert calls == [1.5]


def test_zero_delay_does_not_sleep_during_analysis(client, auth, monkeypatch):
    def boom(seconds):
        raise AssertionError(seconds)

    monkeypatch.setattr("app.analysis.progress.time.sleep", boom)
    response = client.post(
        "/api/v1/files",
        headers=auth,
        files=[("files", ("note.wav", wav_bytes(), "audio/wav"))],
    )
    assert response.status_code == 201, response.text
    assert response.json()["items"][0]["stage"] == "saved"
