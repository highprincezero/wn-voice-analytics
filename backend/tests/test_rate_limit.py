from app.api.rate_limit import RedisCounter, counter_key, reset_memory_counter
from app.config import get_settings


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)


def test_redis_counter_keys_on_user_and_sets_expiry():
    fake = FakeRedis()
    counter = RedisCounter(fake)
    first, _ = counter.hit("rl:user-a:1", 60)
    second, retry = counter.hit("rl:user-a:1", 60)
    assert (first, second) == (1, 2)
    assert fake.ttls["rl:user-a:1"] == 60
    assert retry == 60
    other, _ = counter.hit("rl:user-b:1", 60)
    assert other == 1
    assert counter_key("user-a", now=120.0) == "rl:user-a:2"


def test_http_rate_limit_is_per_user_and_skips_health(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    get_settings.cache_clear()
    reset_memory_counter()

    ada = client.post(
        "/api/v1/auth/signup",
        json={"email": "ada@example.com", "password": "correct-horse"},
    )
    bob = client.post(
        "/api/v1/auth/signup",
        json={"email": "bob@example.com", "password": "correct-horse"},
    )
    ada_headers = {"Authorization": f"Bearer {ada.json()['access_token']}"}
    bob_headers = {"Authorization": f"Bearer {bob.json()['access_token']}"}

    assert client.get("/api/v1/auth/me", headers=ada_headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=ada_headers).status_code == 200
    blocked = client.get("/api/v1/auth/me", headers=ada_headers)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "rate limit exceeded"
    assert int(blocked.headers["retry-after"]) >= 1
    assert client.get("/api/v1/auth/me", headers=bob_headers).status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    assert (
        client.post(
            "/api/v1/auth/signup",
            json={"email": "cara@example.com", "password": "correct-horse"},
        ).status_code
        == 201
    )
    for _ in range(5):
        assert client.get("/api/v1/health").status_code == 200

    get_settings.cache_clear()
