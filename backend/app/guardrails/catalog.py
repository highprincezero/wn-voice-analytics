"""Whitelist of Analytics options. Callers never supply a system prompt.

Every option is a predefined prompt: its instruction text is fixed on the server in
app/analysis/prompts.py, keyed by the option id. Callers only pick ids and typed params.
Options with a "tool" get their numbers from server code (measure_rms, speaking pace);
the model calls the tool and adds a short interpretation.
"""

_MAX_ITEMS = {"type": "int", "min": 1, "max": 10, "default": 5}

CATALOG: dict[str, dict] = {
    "rms_energy": {
        "label": "Loudness (RMS)",
        "kind": "llm",
        "tool": "measure_rms",
        "description": "AI calls a loudness tool on the decoded audio, then describes the trend.",
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
        "kind": "llm",
        "description": "AI counts nouns and adjectives and lists the most common ones.",
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
        "kind": "llm",
        "tool": "measure_speaking_pace",
        "description": "AI calls a pace tool (words per minute), then describes the pace.",
        "params": {},
    },
    # Id kept from the earlier lexicon measure so saved choices and filters still work.
    "sentiment_lexicon": {
        "label": "Sentiment",
        "kind": "llm",
        "description": "Positive, neutral, or negative, with a score and a one-sentence reason.",
        "params": {},
    },
    "action_items": {
        "label": "Action items",
        "kind": "llm",
        "description": "Tasks and follow-ups mentioned in the recording, as short phrases.",
        "params": {"max_items": dict(_MAX_ITEMS)},
    },
    "tone": {
        "label": "Tone",
        "kind": "llm",
        "description": "Formal, casual, tense, friendly, or neutral, with a one-sentence reason.",
        "params": {},
    },
    "key_entities": {
        "label": "Key entities",
        "kind": "llm",
        "description": "People, organizations, and places named in the recording.",
        "params": {"max_items": dict(_MAX_ITEMS)},
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

# "All groupings" report. Files are bucketed in code; the AI writes every group's summary.
# The last four come from Analytics (Layer 2) results.
REPORT_GROUPINGS = (
    "day",
    "week",
    "month",
    "user",
    "taxonomy_label",
    "sentiment",
    "tone",
    "pace_band",
    "key_entity",
    "action_items",
)

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
                "kind": spec.get("kind", "llm"),
                "tool": spec.get("tool"),
                "description": spec["description"],
                "params": spec["params"],
            }
        )
    return options
