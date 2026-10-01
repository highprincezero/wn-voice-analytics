import hashlib
import re

from app.analysis.prompts import (
    build_chunk_messages,
    build_layer1_messages,
    build_llm_options_messages,
    build_reduce_messages,
    build_rollup_messages,
)
from app.analysis.taxonomy import classify_transcript, render_summary, union_taxonomy
from app.analysis.tracing import record_generation

SAMPLE_CALL_SHA256 = "eb1dc760ad01fb710b73517fb7acd613601c846cfb77e8af9ec2076f9d58faa6"
SAMPLE_CALL_TRANSCRIPT = (
    "Good morning. This is the weekly project sync for the payments service. "
    "We made good progress on the API latency incident, and we still need to close "
    "the quarterly budget before the client demo. "
    "Please schedule the roadmap meeting next Tuesday and send the contract notes by Friday. "
    "On a personal note, I have a dentist appointment tomorrow and a family dinner this weekend. "
    "The kids have a school event on Saturday, so I will be offline that afternoon."
)

_PRO = (
    "The project sync covered the payments API latency and the quarterly budget.",
    "We reviewed the client contract and the deployment roadmap.",
)
_PER = (
    "I have a dentist appointment and a family dinner.",
    "The school event and the kids weekend plans came up.",
)
_EVT = (
    "Please send the notes by Friday and meet next Tuesday.",
    "The client demo is scheduled tomorrow.",
)


def deterministic_transcript(digest: str) -> str:
    pick = int(digest[:8], 16)
    return " ".join(
        (
            _PRO[pick % len(_PRO)],
            _PER[(pick // 3) % len(_PER)],
            _EVT[(pick // 7) % len(_EVT)],
        )
    )


_ACTION = re.compile(
    r"\b(?:please|need to|needs to|have to|remember to|will)\s+([^.,;!?]+)", re.IGNORECASE
)
_CAPITAL = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)")
_NOT_NAMES = {
    "I", "API", "OK", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
}  # fmt: skip
_ORG_SUFFIX = ("Inc", "Corp", "LLC", "Ltd", "Company", "Bank", "University")
_PLACE_WORDS = ("in", "at", "from", "to")


def _loudness_trend(windows: list[float]) -> str:
    if len(windows) < 3:
        return "too short to show a trend"
    third = max(1, len(windows) // 3)
    start = sum(windows[:third]) / third
    end = sum(windows[-third:]) / third
    if end > start * 1.2:
        return "rises near the end"
    if end < start * 0.8:
        return "falls toward the end"
    return "steady"


def _pace_word(words_per_minute: float) -> str:
    if words_per_minute < 110:
        return "slow"
    if words_per_minute <= 170:
        return "conversational"
    return "brisk"


def mock_llm_options(transcript: str, options: list[dict], tools) -> dict:
    """Deterministic stand-in for the predefined prompts call (tests and local mock mode).

    It calls the same measuring tools the model would, and uses spaCy and the fixed word
    list for the text options.
    """
    from app.analysis.spacy_features import pos_counts, sentiment_lexicon

    chosen = {item.get("option_id"): item.get("params") or {} for item in options}
    ids = set(chosen)
    result: dict = {}
    if "rms_energy" in ids:
        measured = tools.run("measure_rms", {"window_ms": tools.window_ms})
        windows = [float(value) for value in measured.get("windows") or []]
        result["rms_energy"] = {
            "rms_mean": measured.get("rms_mean", 0.0),
            "rms_peak": measured.get("rms_peak", 0.0),
            "interpretation": _loudness_trend(windows),
        }
    if "speaking_pace" in ids:
        measured = tools.run("measure_speaking_pace", {})
        result["speaking_pace"] = {
            "word_count": measured["word_count"],
            "words_per_minute": measured["words_per_minute"],
            "interpretation": _pace_word(float(measured["words_per_minute"])),
        }
    if "pos_counts" in ids:
        top_n = int(chosen["pos_counts"].get("top_n", 5))
        result["pos_counts"] = pos_counts(transcript, top_n)
    if "sentiment_lexicon" in ids:
        counts = sentiment_lexicon(transcript)
        total = counts["positive"] + counts["negative"]
        score = 0.0 if total == 0 else round((counts["positive"] - counts["negative"]) / total, 2)
        result["sentiment_lexicon"] = {
            "label": counts["label"],
            "score": score,
            "reason": (
                f"Mock rule: {counts['positive']} positive and {counts['negative']} negative words."
            ),
        }
    if "action_items" in ids:
        result["action_items"] = {
            "items": [" ".join(match.split()[:10]) for match in _ACTION.findall(transcript)]
        }
    if "tone" in ids:
        counts = sentiment_lexicon(transcript)
        label = {"positive": "friendly", "negative": "tense"}.get(counts["label"], "neutral")
        result["tone"] = {
            "label": label,
            "reason": "Mock rule: tone follows the balance of positive and negative words.",
        }
    if "key_entities" in ids:
        people: list[str] = []
        organizations: list[str] = []
        places: list[str] = []
        for match in _CAPITAL.finditer(transcript):
            name = match.group(1)
            if name in _NOT_NAMES:
                continue
            before = transcript[: match.start()].split()
            if name.endswith(_ORG_SUFFIX):
                organizations.append(name)
            elif before and before[-1].lower() in _PLACE_WORDS:
                places.append(name)
            else:
                people.append(name)
        result["key_entities"] = {
            "people": people,
            "organizations": organizations,
            "places": places,
        }
    return result


class MockIntelligence:
    def transcribe(self, audio: bytes, filename: str) -> str:
        digest = hashlib.sha256(audio).hexdigest()
        if digest == SAMPLE_CALL_SHA256 or filename == "sample_call.wav":
            return SAMPLE_CALL_TRANSCRIPT
        return deterministic_transcript(digest)

    def summarize_and_classify(self, transcript: str) -> dict:
        messages = build_layer1_messages(transcript)
        taxonomy = classify_transcript(transcript)
        result = {"summary": render_summary(taxonomy), "taxonomy": taxonomy}
        record_generation("layer1", "mock", messages, result)
        return result

    def summarize_chunk(self, chunk: str) -> dict:
        messages = build_chunk_messages(chunk)
        taxonomy = classify_transcript(chunk)
        result = {"partial_summary": render_summary(taxonomy), **taxonomy}
        record_generation("layer1-map", "mock", messages, result)
        return result

    def reduce_summaries(self, partials: list[dict]) -> dict:
        messages = build_reduce_messages(partials)
        taxonomy = union_taxonomy(partials)
        result = {"summary": render_summary(taxonomy), "taxonomy": taxonomy}
        record_generation("layer1-reduce", "mock", messages, result)
        return result

    def predefined_prompts(self, transcript: str, options: list[dict], tools) -> dict:
        messages = build_llm_options_messages(transcript, options)
        result = mock_llm_options(transcript, options, tools)
        record_generation("layer2-prompts", "mock", messages, result)
        return result

    def speaker_profile(self, audio: bytes, filename: str) -> dict:
        from app.analysis.speaker_skill import acoustic_profile

        result = acoustic_profile(audio, filename)
        record_generation("speaker-profile", "mock", [], result)
        return result

    def rollup_summary(self, summaries: list[str]) -> str:
        messages = build_rollup_messages(summaries)
        cleaned: list[str] = []
        for summary in summaries:
            text = summary.strip()
            if not text or text.startswith("No recordings"):
                continue
            marker = " recordings. "
            if marker in text[:48]:
                text = text.split(marker, 1)[1].strip()
            if text and text not in cleaned:
                cleaned.append(text)
        if not cleaned:
            text = "No recordings in this group."
        else:
            text = " ".join(cleaned)
            if len(text) > 1600:
                text = text[:1600].rsplit(" ", 1)[0] + "."
        record_generation("rollup", "mock", messages, text)
        return text
