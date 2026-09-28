import logging
import threading
import time
from typing import Protocol

from app.config import get_settings
from app.storage.keys import assert_user_key

logger = logging.getLogger(__name__)


class BlobStore(Protocol):
    def upload(self, key: str, data: bytes, content_type: str) -> None: ...

    def download(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def ensure_container(self) -> None: ...


class MemoryBlobStore:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        with self._lock:
            self._objects[key] = (data, content_type)

    def download(self, key: str) -> bytes:
        with self._lock:
            try:
                return self._objects[key][0]
            except KeyError as exc:
                raise FileNotFoundError(key) from exc

    def delete(self, key: str) -> None:
        with self._lock:
            self._objects.pop(key, None)

    def ensure_container(self) -> None:
        return None

    def clear(self) -> None:
        with self._lock:
            self._objects.clear()


class AzureBlobStore:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.azure_storage_connection_string:
            raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is required")
        from azure.storage.blob import BlobServiceClient

        self._service = BlobServiceClient.from_connection_string(
            settings.azure_storage_connection_string
        )
        self._container = settings.azure_storage_container

    def _client(self, key: str):
        return self._service.get_blob_client(self._container, key)

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        self._client(key).upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    def download(self, key: str) -> bytes:
        return self._client(key).download_blob().readall()

    def delete(self, key: str) -> None:
        try:
            self._client(key).delete_blob()
        except Exception as exc:
            if "BlobNotFound" in type(exc).__name__ or "BlobNotFound" in str(exc):
                return
            raise

    def ensure_container(self) -> None:
        try:
            self._service.create_container(self._container)
        except Exception as exc:
            message = str(exc).lower()
            if "already exists" in message or "containeralreadyexists" in message:
                return
            raise


_memory = MemoryBlobStore()


def get_blob_store() -> BlobStore:
    if get_settings().blob_provider == "memory":
        return _memory
    return AzureBlobStore()


def clear_memory_store() -> None:
    _memory.clear()


def ensure_blob_container() -> None:
    if get_settings().blob_provider == "memory":
        return
    last_error: Exception | None = None
    for attempt in range(30):
        try:
            get_blob_store().ensure_container()
            logger.info("blob container ready")
            return
        except Exception as exc:
            last_error = exc
            logger.warning("blob store not ready (attempt %s): %s", attempt + 1, exc)
            time.sleep(1)
    assert last_error is not None
    raise last_error


def download_owned(user_id: str, key: str) -> bytes:
    assert_user_key(user_id, key)
    return get_blob_store().download(key)
