import io
import subprocess
import wave

import numpy as np


def read_pcm16(data: bytes) -> tuple[np.ndarray, int]:
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            if handle.getsampwidth() != 2:
                raise ValueError("wav_pcm16_required")
            rate = handle.getframerate()
            channels = handle.getnchannels()
            frames = handle.readframes(handle.getnframes())
    except wave.Error as exc:
        raise ValueError("wav_pcm16_required") from exc
    if rate <= 0:
        raise ValueError("wav_pcm16_required")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if channels > 1:
        usable = len(audio) - (len(audio) % channels)
        audio = audio[:usable].reshape(-1, channels).mean(axis=1)
    audio /= 32768.0
    return audio, rate


def _ffmpeg_wav(data: bytes) -> bytes | None:
    """Decode a stored clip to mono wav. No ffmpeg leaves the original bytes."""
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        "pipe:0",
        "-f",
        "wav",
        "-ac",
        "1",
        "-ar",
        "16000",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            command,
            input=data,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout


def ensure_pcm16_wav(data: bytes) -> bytes:
    """Wav PCM16 for a profile. Bytes that are already wav stay as they are."""
    try:
        read_pcm16(data)
        return data
    except ValueError:
        pass
    decoded = _ffmpeg_wav(data)
    if decoded is None:
        return data
    try:
        read_pcm16(decoded)
    except ValueError:
        return data
    return decoded


# Compressed clips (mp3, m4a, ogg, flac, other wav encodings) are decoded by ffmpeg to
# mono 32-bit float at this rate. Duration and RMS come from these samples.
DECODE_RATE = 16000


def _ffmpeg_float(data: bytes) -> np.ndarray | None:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        "pipe:0",
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "1",
        "-ar",
        str(DECODE_RATE),
        "pipe:1",
    ]
    try:
        proc = subprocess.run(command, input=data, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    usable = len(proc.stdout) - (len(proc.stdout) % 4)
    return np.frombuffer(proc.stdout[:usable], dtype="<f4").astype(np.float32)


def decode_audio(data: bytes) -> tuple[np.ndarray, int]:
    """Mono float samples in [-1, 1] and their rate, for any format ffmpeg reads.

    16-bit PCM WAV is read directly; anything else goes through ffmpeg.
    Raises ValueError("audio_decode_failed") when neither can read the bytes.
    """
    try:
        return read_pcm16(data)
    except ValueError:
        pass
    audio = _ffmpeg_float(data)
    if audio is None or len(audio) == 0:
        raise ValueError("audio_decode_failed")
    return np.clip(audio, -1.0, 1.0), DECODE_RATE


def measure_duration(data: bytes) -> float:
    audio, rate = decode_audio(data)
    if rate == 0:
        return 0.0
    return float(len(audio) / rate)


def rms_features(data: bytes, window_ms: int) -> dict:
    audio, rate = decode_audio(data)
    window = max(1, int(rate * window_ms / 1000))
    if len(audio) == 0:
        return {"rms_mean": 0.0, "rms_peak": 0.0, "window_ms": window_ms, "windows": []}
    count = len(audio) // window
    if count == 0:
        value = float(np.sqrt(np.mean(np.square(audio))))
        return {
            "rms_mean": round(value, 5),
            "rms_peak": round(value, 5),
            "window_ms": window_ms,
            "windows": [round(value, 5)],
        }
    trimmed = audio[: count * window].reshape(count, window)
    rms = np.sqrt(np.mean(np.square(trimmed), axis=1))
    if len(rms) > 50:
        indexes = np.linspace(0, len(rms) - 1, 50).astype(int)
        sampled = rms[indexes]
    else:
        sampled = rms
    return {
        "rms_mean": round(float(rms.mean()), 5),
        "rms_peak": round(float(rms.max()), 5),
        "window_ms": window_ms,
        "windows": [round(float(value), 5) for value in sampled],
    }
