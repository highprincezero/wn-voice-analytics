"""Delimiters for untrusted text inside model prompts.

Transcripts, summaries, chat questions and tool results are wrapped in fixed tags.
Any copy of those tags inside the data is rewritten first, so spoken or typed text
cannot close the block early and pose as instructions.
"""

import json
import re

# Every tag the server uses to fence data in a prompt.
RESERVED_TAGS = (
    "transcript",
    "partial_analyses",
    "summaries",
    "question",
    "intent",
    "tool_name",
    "tool_result",
    "context",
    "previous_reply",
)

# "<", "&lt;" or a fullwidth "＜", optional "/", a reserved name (any case, spaces allowed),
# then up to 64 characters to the closing ">" when there is one.
_MARKER = re.compile(
    r"(?:<|&lt;|＜)\s*(?P<slash>/?)\s*(?P<name>"
    + "|".join(RESERVED_TAGS)
    + r")\b(?:(?P<rest>[^<>＜＞]{0,64}?)(?:>|&gt;|＞))?",
    re.IGNORECASE,
)


def neutralize_markers(text: object) -> str:
    """Rewrite '<transcript>' style tags in data as '[transcript]'. Other text is kept."""

    def repl(match: re.Match) -> str:
        rest = match.group("rest")
        tail = "" if rest is None else f"{rest}]"
        return f"[{match.group('slash')}{match.group('name').lower()}{tail}"

    return _MARKER.sub(repl, str(text))


def neutralize_data(value: object) -> object:
    """neutralize_markers on every string in a JSON-like value, keys included."""
    if isinstance(value, str):
        return neutralize_markers(value)
    if isinstance(value, dict):
        return {
            (neutralize_markers(key) if isinstance(key, str) else key): neutralize_data(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [neutralize_data(item) for item in value]
    return value


def wrap(tag: str, text: object) -> str:
    """Fence data in a reserved tag after neutralizing any copy of the tags inside it."""
    if tag not in RESERVED_TAGS:
        raise ValueError(f"unknown data tag {tag}")
    return f"<{tag}>\n{neutralize_markers(text)}\n</{tag}>"


def wrap_json(tag: str, value: object, limit: int | None = None) -> str:
    text = json.dumps(neutralize_data(value), ensure_ascii=False, default=str)
    if limit is not None:
        text = text[:limit]
    return wrap(tag, text)
