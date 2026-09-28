def test_health(client):
    assert client.get("/health").status_code == 404
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert client.get("/api/v1/health/ready").json()["status"] == "ready"


def test_signup_login_and_me(client):
    signup = client.post(
        "/api/v1/auth/signup",
        json={"email": "Ada@Example.com", "password": "correct-horse"},
    )
    assert signup.status_code == 201
    body = signup.json()
    assert body["email"] == "ada@example.com"
    assert body["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == body["user_id"]
    assert me.json()["home_region"] == "local"

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "correct-horse"},
    )
    assert login.status_code == 200
    assert login.json()["user_id"] == body["user_id"]


def test_signup_rejects_short_password_and_duplicate_email(client):
    bad = client.post("/api/v1/auth/signup", json={"email": "a@example.com", "password": "short"})
    assert bad.status_code == 400
    first = client.post(
        "/api/v1/auth/signup",
        json={"email": "a@example.com", "password": "correct-horse"},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/auth/signup",
        json={"email": "a@example.com", "password": "correct-horse"},
    )
    assert second.status_code == 409


def test_login_failure_and_missing_token(client):
    client.post(
        "/api/v1/auth/signup",
        json={"email": "a@example.com", "password": "correct-horse"},
    )
    wrong = client.post(
        "/api/v1/auth/login",
        json={"email": "a@example.com", "password": "wrong-password"},
    )
    assert wrong.status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.get("/api/v1/files", headers={"Authorization": "Bearer not-a-token"}).status_code
        == 401
    )
