import uuid
from pathlib import Path

ALLOWED_EXTENSIONS = {"wav", "mp3", "m4a", "ogg", "flac"}

CONTENT_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
}


def safe_filename(filename: str) -> str:
    name = Path(filename or "audio.wav").name.strip()
    if not name or name in {".", ".."}:
        raise ValueError("invalid filename")
    return name


def safe_extension(filename: str) -> str:
    name = safe_filename(filename)
    if "." not in name:
        raise ValueError("file extension is required")
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == "wave":
        ext = "wav"
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("unsupported audio extension")
    return ext


def audio_key(user_id: uuid.UUID | str, file_id: uuid.UUID | str, ext: str) -> str:
    cleaned = ext.lower().lstrip(".")
    if cleaned not in ALLOWED_EXTENSIONS:
        raise ValueError("unsupported audio extension")
    return f"users/{user_id}/audio/{file_id}.{cleaned}"


def transcript_key(user_id: uuid.UUID | str, file_id: uuid.UUID | str) -> str:
    return f"users/{user_id}/transcripts/{file_id}.json"


def analysis_key(user_id: uuid.UUID | str, file_id: uuid.UUID | str) -> str:
    return f"users/{user_id}/analysis/{file_id}.json"


def assert_user_key(user_id: uuid.UUID | str, key: str) -> None:
    prefix = f"users/{user_id}/"
    if not key.startswith(prefix) or ".." in key or key.startswith("/"):
        raise PermissionError("storage key is outside the user prefix")
