from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

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


def summarize_groups(
    groups: dict[str, list[str]],
    summarize_many: Callable[[list[str]], str],
) -> dict[str, str]:
    """Write every group's summary, several model calls at a time.

    Each group still gets its own call (summarize_group). The calls are independent
    network requests, so running them side by side keeps a request with many groups
    (for example one per topic) well inside the HTTP timeouts.
    """
    if not groups:
        return {}
    workers = max(1, min(get_settings().summary_workers, len(groups)))
    if workers == 1:
        return {key: summarize_group(texts, summarize_many) for key, texts in groups.items()}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            key: pool.submit(summarize_group, texts, summarize_many)
            for key, texts in groups.items()
        }
        return {key: future.result() for key, future in futures.items()}
