import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SAFETY_PROVIDER"] = "mock"
os.environ["BLOB_PROVIDER"] = "memory"
os.environ["ANALYSIS_MODE"] = "inline"
os.environ["JWT_SECRET"] = "test-secret-key-for-unit-tests-32"
os.environ["JWT_EXPIRE_MINUTES"] = "60"
os.environ["OTEL_ENABLED"] = "false"
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["AZURE_STORAGE_CONTAINER"] = "voice"
os.environ["HOME_REGION"] = "local"
os.environ["CHUNK_CHARS"] = "4000"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["RATE_LIMIT_BACKEND"] = "memory"
os.environ["MOCK_STAGE_DELAY_SEC"] = "0"

import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import engine
from app.main import app
from app.storage.blob import clear_memory_store


@pytest.fixture(autouse=True)
def _reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    clear_memory_store()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth(client):
    response = client.post(
        "/api/v1/auth/signup",
        json={"email": "ada@example.com", "password": "correct-horse"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def wav_bytes(
    seconds: float = 0.5, amp: float = 0.4, freq: float = 440.0, rate: int = 16000
) -> bytes:
    import io
    import math
    import struct
    import wave

    count = int(seconds * rate)
    frames = bytearray()
    for index in range(count):
        sample = int(max(-1.0, min(1.0, amp * math.sin(2 * math.pi * freq * index / rate))) * 32767)
        frames += struct.pack("<h", sample)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return buffer.getvalue()
