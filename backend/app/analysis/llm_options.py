"""Analytics (Layer 2): every option is a predefined prompt.

The user ticks option ids. The server builds one model call from the fixed prompt blocks
of the ticked options, offers the measuring tools those options need, and asks for JSON
that matches a schema built from only the ticked options. Loudness and pace numbers come
from server code (the tools), never from the model: they are written over whatever the
model returns. A failed call or a bad reply leaves that option marked skipped. It never
fails the analysis.
"""

import copy
import json
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.analysis.audio_features import rms_features
from app.analysis.spacy_features import speaking_pace
from app.guardrails.catalog import CATALOG

logger = logging.getLogger(__name__)

# One call per recording. Very long transcripts are cut to keep the call bounded.
TRANSCRIPT_LIMIT = 24000
_ITEM_CHARS = 120
_REASON_CHARS = 300

_STRING_LIST = {"type": "array", "items": {"type": "string"}}
_LEMMA_LIST = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["lemma", "count"],
        "properties": {"lemma": {"type": "string"}, "count": {"type": "integer"}},
    },
}


def _object(properties: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


# Strict structured-output schema for each option. Every object forbids extra keys.
OPTION_SCHEMAS: dict[str, dict] = {
    "rms_energy": _object(
        {
            "rms_mean": {"type": "number"},
            "rms_peak": {"type": "number"},
            "interpretation": {"type": "string"},
        }
    ),
    "speaking_pace": _object(
        {
            "word_count": {"type": "integer"},
            "words_per_minute": {"type": "number"},
            "interpretation": {"type": "string"},
        }
    ),
    "pos_counts": _object(
        {
            "noun_count": {"type": "integer"},
            "adjective_count": {"type": "integer"},
            "top_nouns": _LEMMA_LIST,
            "top_adjectives": _LEMMA_LIST,
        }
    ),
    "sentiment_lexicon": _object(
        {
            "label": {"type": "string", "enum": ["positive", "neutral", "negative"]},
            "score": {"type": "number"},
            "reason": {"type": "string"},
        }
    ),
    "action_items": _object({"items": _STRING_LIST}),
    "tone": _object(
        {
            "label": {
                "type": "string",
                "enum": ["formal", "casual", "tense", "friendly", "neutral"],
            },
            "reason": {"type": "string"},
        }
    ),
    "key_entities": _object(
        {"people": _STRING_LIST, "organizations": _STRING_LIST, "places": _STRING_LIST}
    ),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Signal(_Strict):
    interpretation: str


class _Rms(_Signal):
    rms_mean: float
    rms_peak: float


class _Pace(_Signal):
    word_count: int
    words_per_minute: float


class _Lemma(_Strict):
    lemma: str
    count: int = Field(ge=0)


class _Pos(_Strict):
    noun_count: int = Field(ge=0)
    adjective_count: int = Field(ge=0)
    top_nouns: list[_Lemma]
    top_adjectives: list[_Lemma]


class _Sentiment(_Strict):
    label: Literal["positive", "neutral", "negative"]
    score: float = Field(ge=-1, le=1)
    reason: str


class _ActionItems(_Strict):
    items: list[str]


class _Tone(_Strict):
    label: Literal["formal", "casual", "tense", "friendly", "neutral"]
    reason: str


class _KeyEntities(_Strict):
    people: list[str]
    organizations: list[str]
    places: list[str]


_MODELS: dict[str, type[BaseModel]] = {
    "rms_energy": _Rms,
    "speaking_pace": _Pace,
    "pos_counts": _Pos,
    "sentiment_lexicon": _Sentiment,
    "action_items": _ActionItems,
    "tone": _Tone,
    "key_entities": _KeyEntities,
}


class SignalTools:
    """The measuring tools the model may call. Deterministic server code, cached per file."""

    def __init__(
        self,
        audio: bytes,
        transcript: str,
        duration_sec: float,
        wav_ok: bool,
        options: list[dict],
    ) -> None:
        self.audio = audio
        self.transcript = transcript
        self.duration_sec = duration_sec
        self.wav_ok = wav_ok
        chosen = {item["option_id"]: item.get("params") or {} for item in options}
        self.window_ms = int((chosen.get("rms_energy") or {}).get("window_ms", 250))
        self.names = [
            CATALOG[option_id]["tool"]
            for option_id in chosen
            if CATALOG.get(option_id, {}).get("tool")
        ]
        self.results: dict[str, dict] = {}
        self.called: list[str] = []

    def measure_rms(self) -> dict:
        if "measure_rms" not in self.results:
            if not self.wav_ok:
                self.results["measure_rms"] = {"skipped": "audio_decode_failed"}
            else:
                self.results["measure_rms"] = rms_features(self.audio, self.window_ms)
        return self.results["measure_rms"]

    def measure_speaking_pace(self) -> dict:
        if "measure_speaking_pace" not in self.results:
            self.results["measure_speaking_pace"] = speaking_pace(
                self.transcript, self.duration_sec
            )
        return self.results["measure_speaking_pace"]

    def run(self, name: str, arguments: object = None) -> dict:
        """Execute a tool call from the model. Only offered tools run; arguments are fixed."""
        if name not in self.names:
            return {"error": "unknown_tool"}
        # window_ms always comes from the user's saved setting, not from the model.
        del arguments
        self.called.append(name)
        return self.measure_rms() if name == "measure_rms" else self.measure_speaking_pace()

    def specs(self) -> list[dict]:
        """Function-calling specs for the offered tools only."""
        specs = []
        if "measure_rms" in self.names:
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": "measure_rms",
                        "description": (
                            "Windowed RMS loudness of this recording's decoded audio. "
                            "Returns rms_mean, rms_peak, window_ms, and up to 50 window values."
                        ),
                        "strict": True,
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["window_ms"],
                            "properties": {
                                "window_ms": {"type": "integer", "enum": [self.window_ms]}
                            },
                        },
                    },
                }
            )
        if "measure_speaking_pace" in self.names:
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": "measure_speaking_pace",
                        "description": (
                            "Word count and words per minute from this recording's transcript "
                            "and measured duration."
                        ),
                        "strict": True,
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [],
                            "properties": {},
                        },
                    },
                }
            )
        return specs

    @staticmethod
    def as_message(result: dict) -> str:
        return json.dumps(result)


def llm_selected(options: list[dict]) -> list[dict]:
    """The ticked options that have a predefined prompt, in catalog order."""
    chosen = {item.get("option_id"): item for item in options or [] if isinstance(item, dict)}
    return [
        chosen[option_id] for option_id in CATALOG if option_id in chosen and option_id in _MODELS
    ]


def build_llm_schema(options: list[dict]) -> dict:
    """Top-level JSON schema with one property per ticked option, and nothing else."""
    ids = [item["option_id"] for item in llm_selected(options)]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ids,
        "properties": {option_id: copy.deepcopy(OPTION_SCHEMAS[option_id]) for option_id in ids},
    }


def _param(option: dict, name: str, default: int, low: int, high: int) -> int:
    value = (option.get("params") or {}).get(name, default)
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _clean_list(values: list[str], limit: int) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        text = " ".join(str(value).split())[:_ITEM_CHARS]
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:limit]


def _short(text: str) -> str:
    return " ".join(str(text).split())[:_REASON_CHARS]


def validate_llm_output(payload: object, options: list[dict], tools: SignalTools) -> dict:
    """Validate the reply per ticked option. Bad or missing parts become skipped.

    Loudness and pace numbers are replaced with the tool's exact values.
    """
    data = payload if isinstance(payload, dict) else {}
    result: dict = {}
    for option in llm_selected(options):
        option_id = option["option_id"]
        raw = data.get(option_id)
        if isinstance(raw, dict) and isinstance(raw.get("label"), str):
            raw = {**raw, "label": raw["label"].strip().lower()}
        try:
            value = _MODELS[option_id].model_validate(raw).model_dump()
        except ValidationError:
            result[option_id] = {"skipped": "invalid_output"}
            continue
        if option_id == "rms_energy":
            measured = tools.measure_rms()
            if measured.get("skipped"):
                result[option_id] = dict(measured)
                continue
            value = {
                **measured,
                "interpretation": _short(value["interpretation"]),
                "tool": "measure_rms",
            }
        elif option_id == "speaking_pace":
            measured = tools.measure_speaking_pace()
            value = {
                **measured,
                "interpretation": _short(value["interpretation"]),
                "tool": "measure_speaking_pace",
            }
        elif option_id == "pos_counts":
            limit = _param(option, "top_n", 5, 1, 20)
            value["top_nouns"] = value["top_nouns"][:limit]
            value["top_adjectives"] = value["top_adjectives"][:limit]
        elif option_id == "sentiment_lexicon":
            value["reason"] = _short(value["reason"])
        elif option_id == "action_items":
            value["items"] = _clean_list(value["items"], _param(option, "max_items", 5, 1, 10))
        elif option_id == "key_entities":
            limit = _param(option, "max_items", 5, 1, 10)
            for key in ("people", "organizations", "places"):
                value[key] = _clean_list(value[key], limit)
        elif option_id == "tone":
            value["reason"] = _short(value["reason"])
        result[option_id] = value
    return result


def run_predefined_prompts(
    provider,
    transcript: str,
    options: list[dict],
    tools: SignalTools,
    blocked: bool = False,
) -> dict:
    """One model call (with a tool round trip when needed) for every ticked option.

    Never raises.
    """
    selected = llm_selected(options)
    if not selected:
        return {}
    ids = [item["option_id"] for item in selected]
    if blocked:
        return {option_id: {"skipped": "content_safety_blocked"} for option_id in ids}
    text = (transcript or "")[:TRANSCRIPT_LIMIT]
    if not text.strip():
        return {option_id: {"skipped": "no_speech"} for option_id in ids}
    try:
        raw = provider.predefined_prompts(text, selected, tools)
    except Exception as exc:
        logger.warning("predefined prompts call failed: %s", type(exc).__name__)
        return {option_id: {"skipped": "llm_failed"} for option_id in ids}
    try:
        return validate_llm_output(raw, selected, tools)
    except Exception as exc:  # a tool failing on odd audio must not fail the file
        logger.warning("predefined prompts validation failed: %s", type(exc).__name__)
        return {option_id: {"skipped": "invalid_output"} for option_id in ids}
