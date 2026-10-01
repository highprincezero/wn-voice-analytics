"""Deterministic Analytics measures in code.

The pipeline runs every Analytics option as a predefined prompt (llm_options.py).
These functions are its measuring tools (measure_rms, measure_speaking_pace) and the
mock-mode stand-in for the model. run_layer2 computes them all without a model.
"""

from app.analysis.audio_features import rms_features
from app.analysis.spacy_features import pos_counts, sentiment_lexicon, speaking_pace


def run_layer2(
    audio: bytes,
    transcript: str,
    duration_sec: float,
    options: list[dict],
    wav_ok: bool,
) -> dict:
    result: dict = {}
    for option in options:
        option_id = option["option_id"]
        params = option.get("params") or {}
        if option_id == "rms_energy":
            if not wav_ok:
                result[option_id] = {"skipped": "audio_decode_failed"}
            else:
                result[option_id] = rms_features(audio, int(params["window_ms"]))
        elif option_id == "pos_counts":
            result[option_id] = pos_counts(transcript, int(params["top_n"]))
        elif option_id == "speaking_pace":
            result[option_id] = speaking_pace(transcript, duration_sec)
        elif option_id == "sentiment_lexicon":
            result[option_id] = sentiment_lexicon(transcript)
    return result
