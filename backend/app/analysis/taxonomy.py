import re

PROFESSIONAL_TERMS = (
    "project",
    "payments",
    "api",
    "latency",
    "budget",
    "client",
    "demo",
    "roadmap",
    "meeting",
    "contract",
    "deployment",
    "incident",
    "service",
    "quarterly",
    "sync",
)

PERSONAL_TERMS = (
    "dentist",
    "appointment",
    "family",
    "dinner",
    "weekend",
    "kids",
    "school",
    "personal",
)

EVENT_CUES = (
    "tomorrow",
    "next ",
    "friday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "saturday",
    "sunday",
    "weekend",
)


def _contains(text: str, term: str) -> bool:
    return re.search(rf"\b{re.escape(term)}\b", text.lower()) is not None


def _clean_list(items: list[str], limit: int = 12) -> list[str]:
    cleaned: list[str] = []
    for item in items:
        text = " ".join(str(item).split())[:180]
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def extract_events(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    events: list[str] = []
    for part in parts:
        lowered = part.lower()
        if any(cue in lowered for cue in EVENT_CUES):
            events.append(part.strip())
    return _clean_list(events, limit=8)


def classify_transcript(text: str) -> dict[str, list[str]]:
    professional = [term for term in PROFESSIONAL_TERMS if _contains(text, term)]
    personal = [term for term in PERSONAL_TERMS if _contains(text, term)]
    return {
        "professional_topics": _clean_list(professional),
        "personal_topics": _clean_list(personal),
        "upcoming_events": extract_events(text),
    }


def union_taxonomy(parts: list[dict]) -> dict[str, list[str]]:
    merged = {key: [] for key in ("professional_topics", "personal_topics", "upcoming_events")}
    for part in parts:
        source = part.get("taxonomy") if "taxonomy" in part else part
        if not isinstance(source, dict):
            continue
        for key in merged:
            merged[key].extend(source.get(key) or [])
    return {key: _clean_list(values) for key, values in merged.items()}


def render_summary(taxonomy: dict) -> str:
    professional = ", ".join(taxonomy.get("professional_topics") or []) or "no professional topics"
    personal = ", ".join(taxonomy.get("personal_topics") or []) or "no personal topics"
    events = "; ".join(taxonomy.get("upcoming_events") or []) or "none noted"
    return (
        f"The speaker discusses {professional}. "
        f"Personal context includes {personal}. "
        f"Upcoming items: {events}."
    )
