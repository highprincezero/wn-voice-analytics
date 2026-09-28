"""Whitelist of Layer 2 options. Users select these; they never supply a system prompt."""

CATALOG: dict[str, dict] = {
    "rms_energy": {
        "label": "RMS energy",
        "description": "Windowed root-mean-square energy of a 16-bit PCM WAV file.",
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

GROUP_BY_OPTIONS = ("user", "taxonomy_label", "week", "sentiment")


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
