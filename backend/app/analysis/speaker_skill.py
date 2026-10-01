"""Speaker-profile skill. Sent with the audio when a profile is requested."""

from app.analysis.audio_features import measure_duration, rms_features

SKILL_PROMPT = (
    "Read each speaker from the raw audio. Do not infer the voice from words. "
    "Return one profile per voice, in the order the voices first appear. "
    "Fields for each voice: voice_signature (pitch range, pace, loudness, timbre; "
    "do not invent an embedding), vocal_presentation (lower or higher pitch, "
    "with confidence; this is not gender), age_band (teen, young adult, adult, "
    "or older adult, plus confidence; no exact age), accent_region (language or "
    "accent, plus confidence; this is not nationality), speaking_style (how the "
    "clip sounds, plus confidence; this is not a personality diagnosis), evidence "
    "(one sentence of audible cues; quote no transcript), limits (say the clip is "
    "too short, noisy, or single-tone when the estimate is weak). "
    "Label every trait as an estimate. A short greeting is not enough for age, "
    "accent, or style. Prefer not enough voice to tell over a guess."
)


def _blank(limits: str, evidence: str) -> dict:
    unknown = "not enough voice to tell"
    return {
        "voice_signature": unknown,
        "vocal_presentation": unknown,
        "age_band": unknown,
        "accent_region": unknown,
        "speaking_style": unknown,
        "evidence": evidence,
        "limits": limits,
    }


def acoustic_profile(audio: bytes, filename: str) -> dict:
    """Skill-shaped profile from the audio when no voice model answers."""
    try:
        duration = measure_duration(audio)
        loud = rms_features(audio, 250).get("rms_mean") or 0.0
        readable = True
    except ValueError:
        duration = 0.0
        loud = 0.0
        readable = False
    if not readable:
        voice = _blank(
            "This file could not be read as a voice clip.",
            "I could not read a voice from this file.",
        )
    elif duration < 1.5:
        voice = _blank(
            "The clip is too short to hear a voice.",
            "The clip is too short to hear a voice.",
        )
    else:
        level = "quiet" if loud < 0.05 else "moderate"
        voice = _blank(
            "Age, accent, and style are not estimated.",
            f"Estimate. The clip is about {int(duration)} seconds and {level}.",
        )
        voice["voice_signature"] = (
            f"Estimate. One voice, about {int(duration)} seconds, {level} loudness."
        )
    return {"filename": filename, "voices": [voice]}
