import re
from collections import Counter

POSITIVE_WORDS = {
    "good",
    "great",
    "thanks",
    "happy",
    "glad",
    "excellent",
    "progress",
    "win",
    "love",
}
NEGATIVE_WORDS = {
    "issue",
    "problem",
    "delay",
    "angry",
    "bad",
    "worried",
    "fail",
    "error",
}

_nlp = None


def get_nlp():
    global _nlp
    if _nlp is None:
        import spacy

        _nlp = spacy.load("en_core_web_sm")
    return _nlp


def _top(words: list[str], top_n: int) -> list[dict]:
    return [{"lemma": lemma, "count": count} for lemma, count in Counter(words).most_common(top_n)]


def pos_counts(text: str, top_n: int) -> dict:
    doc = get_nlp()(text)
    adjectives = [token.lemma_.lower() for token in doc if token.pos_ == "ADJ" and token.is_alpha]
    nouns = [token.lemma_.lower() for token in doc if token.pos_ == "NOUN" and token.is_alpha]
    return {
        "adjective_count": len(adjectives),
        "noun_count": len(nouns),
        "top_adjectives": _top(adjectives, top_n),
        "top_nouns": _top(nouns, top_n),
    }


def sentiment_lexicon(text: str) -> dict:
    words = re.findall(r"[a-zA-Z']+", text.lower())
    positive = sum(1 for word in words if word in POSITIVE_WORDS)
    negative = sum(1 for word in words if word in NEGATIVE_WORDS)
    if positive > negative:
        label = "positive"
    elif negative > positive:
        label = "negative"
    else:
        label = "neutral"
    return {"positive": positive, "negative": negative, "label": label}


def speaking_pace(text: str, duration_sec: float) -> dict:
    words = re.findall(r"[A-Za-z0-9']+", text)
    per_minute = (len(words) / duration_sec * 60.0) if duration_sec > 0 else 0.0
    return {"word_count": len(words), "words_per_minute": round(per_minute, 2)}
