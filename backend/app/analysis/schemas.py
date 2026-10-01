from pydantic import BaseModel, ConfigDict, Field


class Taxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    professional_topics: list[str]
    personal_topics: list[str]
    upcoming_events: list[str]


class Layer1Core(BaseModel):
    model_config = ConfigDict(extra="allow")

    duration_sec: float = Field(ge=0)
    summary: str = Field(min_length=1)
    taxonomy: Taxonomy


class Layer2Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rms_energy: dict | None = None
    pos_counts: dict | None = None
    speaking_pace: dict | None = None
    sentiment_lexicon: dict | None = None
    action_items: dict | None = None
    tone: dict | None = None
    key_entities: dict | None = None


LAYER1_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "taxonomy"],
    "properties": {
        "summary": {"type": "string"},
        "taxonomy": {
            "type": "object",
            "additionalProperties": False,
            "required": ["professional_topics", "personal_topics", "upcoming_events"],
            "properties": {
                "professional_topics": {"type": "array", "items": {"type": "string"}},
                "personal_topics": {"type": "array", "items": {"type": "string"}},
                "upcoming_events": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}

CHUNK_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "partial_summary",
        "professional_topics",
        "personal_topics",
        "upcoming_events",
    ],
    "properties": {
        "partial_summary": {"type": "string"},
        "professional_topics": {"type": "array", "items": {"type": "string"}},
        "personal_topics": {"type": "array", "items": {"type": "string"}},
        "upcoming_events": {"type": "array", "items": {"type": "string"}},
    },
}


def validate_layer1(payload: dict) -> dict:
    model = Layer1Core.model_validate(payload)
    return model.model_dump()


def validate_layer2(payload: dict) -> dict:
    model = Layer2Payload.model_validate(payload)
    return {key: value for key, value in model.model_dump().items() if value is not None}
