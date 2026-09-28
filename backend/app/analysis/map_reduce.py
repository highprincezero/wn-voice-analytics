from collections.abc import Callable

from app.config import get_settings


def map_reduce_summaries(
    texts: list[str],
    summarize_many: Callable[[list[str]], str],
    depth: int = 0,
) -> str:
    """Binary map-reduce over summary strings so a rollup stays inside the chunk budget."""
    cleaned = [text.strip() for text in texts if text and text.strip()]
    if not cleaned:
        return "No recordings in this group."
    limit = get_settings().chunk_chars
    total = sum(len(text) for text in cleaned)
    if len(cleaned) == 1 or total <= limit or depth >= 6:
        return summarize_many(cleaned[:50])
    mid = max(1, len(cleaned) // 2)
    left = map_reduce_summaries(cleaned[:mid], summarize_many, depth + 1)
    right = map_reduce_summaries(cleaned[mid:], summarize_many, depth + 1)
    return summarize_many([left, right])
