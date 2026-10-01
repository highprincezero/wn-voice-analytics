from collections.abc import Callable

from app.config import get_settings


def summarize_group(
    texts: list[str],
    summarize_many: Callable[[list[str]], str],
    depth: int = 0,
) -> str:
    """Split a long group of summaries so one summary stays inside the chunk budget."""
    cleaned = [text.strip() for text in texts if text and text.strip()]
    if not cleaned:
        return "No recordings in this group."
    limit = get_settings().chunk_chars
    total = sum(len(text) for text in cleaned)
    if len(cleaned) == 1 or total <= limit or depth >= 6:
        return summarize_many(cleaned[:50])
    mid = max(1, len(cleaned) // 2)
    left = summarize_group(cleaned[:mid], summarize_many, depth + 1)
    right = summarize_group(cleaned[mid:], summarize_many, depth + 1)
    return summarize_many([left, right])
