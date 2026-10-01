"""Whitelist of Analytics measures. Callers never supply a system prompt."""

CATALOG: dict[str, dict] = {
    "rms_energy": {
        "label": "RMS energy",
        "description": "Windowed RMS energy of the decoded audio (WAV, MP3, M4A, OGG, FLAC).",
        "params": {
            "window_ms": {
                "type": "enum",
                "values": [100, 250, 500, 1000],
                "default": 250,
            }
        },
    },
    "pos_counts": {
        "label": "Nouns and adjectives",
        "description": "spaCy part-of-speech counts and the most common lemmas.",
        "params": {
            "top_n": {
                "type": "int",
                "min": 1,
                "max": 20,
                "default": 5,
            }
        },
    },
    "speaking_pace": {
        "label": "Speaking pace",
        "description": "Words per minute from the transcript and the measured duration.",
        "params": {},
    },
    "sentiment_lexicon": {
        "label": "Lexicon sentiment",
        "description": "Positive and negative word counts from a fixed lexicon.",
        "params": {},
    },
}

CUSTOM_FILTERS: dict[str, dict] = {
    "sentiment": {
        "path": ("sentiment_lexicon", "label"),
        "op": "eq",
        "values": ["positive", "neutral", "negative"],
    },
    "adjective_count": {
        "path": ("pos_counts", "adjective_count"),
        "op": "gte",
        "min": 0,
        "max": 100000,
    },
    "noun_count": {
        "path": ("pos_counts", "noun_count"),
        "op": "gte",
        "min": 0,
        "max": 100000,
    },
    "wpm": {
        "path": ("speaking_pace", "words_per_minute"),
        "op": "gte",
        "min": 0,
        "max": 5000,
    },
    "rms_mean": {
        "path": ("rms_energy", "rms_mean"),
        "op": "gte",
        "min": 0,
        "max": 1,
    },
}

GROUP_BY_OPTIONS = ("user", "taxonomy_label", "day", "week", "month", "sentiment")

# Fixed questions. trend takes one slot. The sentence itself is not a request field.
TEMPLATES: dict[str, dict] = {
    "trend": {"slot": "bucket", "values": ("day", "week", "month")},
    "by_topic": {"group_by": "taxonomy_label"},
    "by_sentiment": {"group_by": "sentiment"},
}


def resolve_template(template_id: str, slot: str | None) -> str:
    spec = TEMPLATES.get(template_id)
    if spec is None:
        raise ValueError("unsupported template")
    fixed = spec.get("group_by")
    if fixed:
        if slot:
            raise ValueError("unsupported slot")
        return str(fixed)
    if slot not in spec["values"]:
        raise ValueError("unsupported slot")
    return str(slot)


def default_selections() -> list[dict]:
    """Every Analytics measure, with each parameter at its catalog default."""
    selections = []
    for option_id, spec in CATALOG.items():
        params = {name: rule["default"] for name, rule in spec["params"].items()}
        selections.append({"option_id": option_id, "params": params})
    return selections


def public_catalog() -> list[dict]:
    options = []
    for option_id, spec in CATALOG.items():
        options.append(
            {
                "id": option_id,
                "label": spec["label"],
                "description": spec["description"],
                "params": spec["params"],
            }
        )
    return options
