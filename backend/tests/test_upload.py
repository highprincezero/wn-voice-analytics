from tests.conftest import wav_bytes


def test_upload_lists_and_isolates_users(client, auth):
    payload = wav_bytes()
    response = client.post(
        "/api/v1/files",
        headers=auth,
        files=[("files", ("note.wav", payload, "audio/wav"))],
    )
    assert response.status_code == 201, response.text
    item = response.json()["items"][0]
    assert item["status"] == "completed"
    assert item["storage_key"].startswith("users/")
    assert item["storage_key"].endswith(f"/audio/{item['id']}.wav")
    assert item["duration_sec"] == 0.5
    assert item["summary"]
    assert item["taxonomy"]["professional_topics"]

    listed = client.get("/api/v1/files", headers=auth)
    assert listed.json()["total"] == 1
    detail = client.get(f"/api/v1/files/{item['id']}", headers=auth)
    transcript = detail.json()["transcript"].lower()
    assert "client" in transcript or "project" in transcript
    audio = client.get(f"/api/v1/files/{item['id']}/audio", headers=auth)
    assert audio.status_code == 200
    assert audio.content == payload

    other = client.post(
        "/api/v1/auth/signup",
        json={"email": "other@example.com", "password": "correct-horse"},
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert client.get("/api/v1/files", headers=other_headers).json()["total"] == 0
    assert client.get(f"/api/v1/files/{item['id']}", headers=other_headers).status_code == 404


def test_upload_rejects_bad_extension_and_empty_file(client, auth):
    bad = client.post(
        "/api/v1/files",
        headers=auth,
        files=[("files", ("notes.exe", b"hello", "application/octet-stream"))],
    )
    assert bad.status_code == 400
    empty = client.post(
        "/api/v1/files",
        headers=auth,
        files=[("files", ("empty.wav", b"", "audio/wav"))],
    )
    assert empty.status_code == 400


def test_filters_and_delete(client, auth):
    client.put(
        "/api/v1/prompts/config",
        headers=auth,
        json={
            "selections": [
                {"option_id": "sentiment_lexicon", "params": {}},
                {"option_id": "pos_counts", "params": {"top_n": 5}},
            ]
        },
    )
    sample = open("/workspace/samples/sample_call.wav", "rb").read()
    uploaded = client.post(
        "/api/v1/files",
        headers=auth,
        files=[("files", ("sample_call.wav", sample, "audio/wav"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    item = uploaded.json()["items"][0]
    assert item["layer2"]["sentiment_lexicon"]["label"] == "positive"
    positive = client.get("/api/v1/files", headers=auth, params={"custom": "sentiment:positive"})
    assert positive.json()["total"] == 1
    negative = client.get("/api/v1/files", headers=auth, params={"custom": "sentiment:negative"})
    assert negative.json()["total"] == 0
    budget = client.get("/api/v1/files", headers=auth, params={"taxonomy": "budget"})
    assert budget.json()["total"] == 1
    missing = client.get("/api/v1/files", headers=auth, params={"taxonomy": "not-a-topic"})
    assert missing.json()["total"] == 0
    unknown = client.get("/api/v1/files", headers=auth, params={"custom": "drop:table"})
    assert unknown.status_code == 400
    deleted = client.delete(f"/api/v1/files/{item['id']}", headers=auth)
    assert deleted.status_code == 204
    assert client.get("/api/v1/files", headers=auth).json()["total"] == 0
