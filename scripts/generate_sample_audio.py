"""Write the bundled 4 second PCM16 sample used by the local demo."""

import hashlib
import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "samples" / "sample_call.wav"


def generate(path: Path = OUTPUT, seconds: float = 4.0, rate: int = 16000) -> str:
    count = int(seconds * rate)
    frames = bytearray()
    for index in range(count):
        instant = index / rate
        amplitude = 0.12 if instant < seconds / 2 else 0.55
        frequency = 220.0 if instant < seconds / 2 else 440.0
        sample = amplitude * math.sin(2 * math.pi * frequency * instant)
        packed = int(max(-1.0, min(1.0, sample)) * 32767)
        frames += struct.pack("<h", packed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    digest = generate()
    print(f"{digest}  {OUTPUT}")
