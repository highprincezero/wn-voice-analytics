import io
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


def measure_duration(data: bytes) -> float:
    audio, rate = read_pcm16(data)
    if rate == 0:
        return 0.0
    return float(len(audio) / rate)


def rms_features(data: bytes, window_ms: int) -> dict:
    audio, rate = read_pcm16(data)
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
