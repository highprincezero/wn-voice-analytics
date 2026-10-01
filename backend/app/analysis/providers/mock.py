import hashlib

from app.analysis.prompts import (
    build_chunk_messages,
    build_layer1_messages,
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
