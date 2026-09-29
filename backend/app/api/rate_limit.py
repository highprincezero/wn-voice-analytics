"""Per-user request counter. Production and Compose use Redis. Tests use memory."""

import threading
import time

import redis

from app.config import get_settings

# One Redis client per URL, reused across requests (connection pooling).
_redis_clients: dict[str, redis.Redis] = {}


# Raised when Redis can't be reached; deps.py turns it into HTTP 503 (fail closed).
class RateLimiterUnavailable(RuntimeError):
    pass


# Fixed-window counter stored in Redis (shared across all API replicas).
class RedisCounter:
    def __init__(self, client: redis.Redis) -> None:
        self.client = client

    def hit(self, key: str, window: int) -> tuple[int, int]:
        try:
            # INCR is atomic, so concurrent requests can't lose counts.
            count = int(self.client.incr(key))
            # First hit in this window: set the key to expire when the window ends.
            if count == 1:
                self.client.expire(key, window)
            # TTL = seconds until the key expires, used for the Retry-After header.
            ttl = int(self.client.ttl(key))
        except redis.RedisError as exc:
            raise RateLimiterUnavailable("redis unavailable") from exc
        retry = ttl if ttl > 0 else window
        return count, max(1, retry)


# In-process counter for tests (no Redis needed).
class MemoryCounter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, window: int) -> tuple[int, int]:
        del window
        with self._lock:
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
        retry = _seconds_left_in_window()
        return count, retry

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


_memory = MemoryCounter()


def _seconds_left_in_window() -> int:
    window = max(1, get_settings().rate_limit_window_seconds)
    remaining = window - (int(time.time()) % window)
    return max(1, remaining)


def reset_memory_counter() -> None:
    _memory.reset()


# Cached Redis client; short timeouts so an outage fails fast instead of hanging requests.
def redis_client() -> redis.Redis:
    settings = get_settings()
    url = settings.redis_url or settings.celery_broker_url
    cached = _redis_clients.get(url)
    if cached is not None:
        return cached
    client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    _redis_clients[url] = client
    return client


# Picks the counter implementation from settings (redis in Compose/prod, memory in tests).
def get_counter() -> RedisCounter | MemoryCounter:
    settings = get_settings()
    if settings.rate_limit_backend == "memory":
        return _memory
    if settings.rate_limit_backend == "redis":
        return RedisCounter(redis_client())
    raise RateLimiterUnavailable("RATE_LIMIT_BACKEND must be redis or memory")


# Key per user per time window, e.g. rl:<user_id>:<window number>.
def counter_key(user_id: str, now: float | None = None) -> str:
    settings = get_settings()
    window = max(1, settings.rate_limit_window_seconds)
    instant = time.time() if now is None else now
    # Integer division groups timestamps into fixed windows.
    bucket = int(instant // window)
    return f"rl:{user_id}:{bucket}"


# Called on every authenticated request; returns (allowed, retry_after_seconds).
def consume(user_id: str) -> tuple[bool, int]:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return True, 0
    count, retry_after = get_counter().hit(
        counter_key(user_id), max(1, settings.rate_limit_window_seconds)
    )
    # Allowed while the count in this window is within the configured limit.
    return count <= settings.rate_limit_requests, retry_after
