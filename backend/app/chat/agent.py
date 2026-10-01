"""Microsoft Agent Framework chat: plan -> tools -> compose.

Azure mode: the compose model writes every reply. Mock mode uses fixed rules.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import TypedDict

import httpx
from agent_framework import WorkflowBuilder
from sqlalchemy.orm import Session

from app.analysis.providers.azure import chat_sampling_fields
from app.analysis.tracing import analysis_span
from app.chat.memory import asks_earlier_question, earlier_question_reply, previous_question
from app.chat.prompts import (
    CAPABILITIES,
    CHAT_REPLY_SCHEMA,
    build_compose_messages,
    build_planner_messages,
)
from app.chat.schemas import ChatReplyBody
from app.chat.tools import TOOL_NAMES, TOOL_SPECS, execute_tool, prepare_arguments
from app.config import get_settings
from app.db.models import AudioFile
from app.timeutil import utcnow
from app.workflow import Finish, Step, run_workflow

logger = logging.getLogger(__name__)

# Regexes used by the rule-based (mock) planner to pull filters out of the question.
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_LONGER = re.compile(
    r"(?:longer than|at least|over|minimum)\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_SHORTER = re.compile(
    r"(?:shorter than|under|at most|maximum)\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_TAXONOMY = re.compile(
    r"\b(?:about|taxonomy|topic|mentioning|labeled)\s+([a-z0-9]{2,40})",
    re.IGNORECASE,
)
# Fixed replies below are for mock mode, or when the compose model call fails.
_HELP_REPLY = (
    "Ask about your recordings. For example: summarize this recording, what topics came up, "
    "or summarize my files by topic. You can also ask for one file by its id."
)
_CAPABILITIES_REPLY = (
    "I transcribe a recording, write the summary and the topics, "
    "and measure pace and sentiment. "
    "Ask what events are coming up, for a summary by topic, or for one recording by its id."
)
_GREETING_REPLY = "Hello. Ask about a recording, or send one."
_CANT_DETERMINE = "I can't seem to determine that from this recording."
_GREETING = re.compile(r"^(?:hey|hi|hello|hiya|yo)(?:\s+there)?$")


# Chat graph state: the question, the chosen tool + args, the tool output, and the reply.
class ChatState(TypedDict, total=False):
    message: str
    history: list
    intent: str
    tool_name: str
    tool_arguments: dict
    tool_result: dict
    reply: str


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _time_window(text: str) -> dict:
    now = utcnow()
    if "this week" in text or "past week" in text:
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return {"date_from": _iso(start)}
    if "today" in text:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return {"date_from": _iso(start)}
    if "yesterday" in text:
        day = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = day.replace(hour=23, minute=59, second=59)
        return {"date_from": _iso(day), "date_to": _iso(end)}
    return {}


def _search_arguments(text: str) -> dict:
    arguments = _time_window(text)
    longer = _LONGER.search(text)
    shorter = _SHORTER.search(text)
    if longer:
        arguments["min_duration"] = float(longer.group(1))
    if shorter:
        arguments["max_duration"] = float(shorter.group(1))
    taxonomy = _TAXONOMY.search(text)
    if taxonomy and "upcoming" not in text and "event" not in text:
        arguments["taxonomy"] = taxonomy.group(1).lower()
    return arguments


def _group_by(text: str) -> str:
    if "sentiment" in text:
        return "sentiment"
    if any(token in text for token in ("per day", "by day", "each day", "daily")):
        return "day"
    if any(token in text for token in ("per month", "by month", "each month", "monthly")):
        return "month"
    if "week" in text and "topic" not in text and "taxonomy" not in text:
        return "week"
    if "topic" in text or "taxonomy" in text or "label" in text:
        return "taxonomy_label"
    return "user"


def _wants_rollup(text: str) -> bool:
    return any(token in text for token in ("summarize", "summary", "rollup", "group by", "trend"))


# Voice-trait questions go to profile_speaker: gender, age, accent, emotion, who is speaking.
_VOICE_TRAIT = re.compile(
    r"\b(?:gender|sex|male|female|man or (?:a )?woman|woman or (?:a )?man|accent|dialect"
    r"|emotions?|emotional|mood|age|how old)\b"
)
_ALWAYS_VOICE = re.compile(
    r"\b(?:gender|male|female|man or (?:a )?woman|woman or (?:a )?man|accent)\b"
)
_VOICE_SUBJECT = re.compile(
    r"\b(?:speakers?|voices?|person|caller|narrator|he|she|they|him|her|them|sounds?)\b"
)
_WHO_SPEAKS = re.compile(r"\bwho(?:'s| is| was)? (?:the )?(?:speaking|talking|speaker)\b")


def _wants_profile(text: str) -> bool:
    lowered = " ".join(text.replace("\u2019", "'").lower().split())
    if "profile" in lowered and ("voice" in lowered or "speaker" in lowered):
        return True
    if _WHO_SPEAKS.search(lowered) or _ALWAYS_VOICE.search(lowered):
        return True
    if _VOICE_TRAIT.search(lowered) and _VOICE_SUBJECT.search(lowered):
        return True
    phrases = (
        "analyze the voice",
        "analyse the voice",
        "analyze the speaker",
        "analyse the speaker",
        "describe the voice",
        "describe the speaker",
    )
    return any(phrase in lowered for phrase in phrases)


def _profile_choice(message: str) -> dict | None:
    if not _wants_profile(message):
        return None
    arguments: dict = {}
    found = _UUID.findall(message)
    if found:
        arguments["file_id"] = found[0]
    return {"intent": "profile", "tool_name": "profile_speaker", "arguments": arguments}


# Mock-mode planner: keyword rules decide which tool to call and with what arguments.
def _avoid_repeat(reply: str, history: list) -> str:
    previous = ""
    for item in reversed(history or []):
        if item.get("role") == "assistant":
            previous = (item.get("content") or "").strip()
            break
    if not previous or previous != reply.strip():
        return reply
    if reply.strip() == _CAPABILITIES_REPLY:
        return _HELP_REPLY
    return _CAPABILITIES_REPLY


def _asks_greeting(message: str) -> bool:
    """True for hi or hello, including extra punctuation such as hello???."""
    bare = re.sub(r"[^a-z\s]", "", " ".join((message or "").lower().split()))
    return bool(_GREETING.fullmatch(bare.strip()))


def _asks_capabilities(message: str) -> bool:
    text = " ".join(message.lower().split())
    phrases = (
        "what can you do",
        "what can u do",
        "what do you do",
        "how can you help",
        "what can you help",
        "who are you",
        "what are you",
    )
    if any(phrase in text for phrase in phrases):
        return True
    return text in {"help", "help me"}


_SET_WORD = re.compile(r"\b(recordings?|files?|calls?)\b")


def _asks_for_the_set(text: str) -> bool:
    """True when the question asks to see the set of recordings."""
    if not _SET_WORD.search(text):
        return False
    asks = ("list", "show", "which", "how many", "all my")
    return any(phrase in text for phrase in asks)


def _named_file_choice(message: str) -> dict | None:
    """One file id written in the question opens that recording."""
    if _wants_rollup(message.lower()):
        return None
    found = _UUID.findall(message)
    if len(found) != 1:
        return None
    return {
        "intent": "analysis",
        "tool_name": "get_analysis",
        "arguments": {"file_id": found[0]},
    }


def _about_what_it_says(message: str) -> bool:
    """True when the question is about what one recording says."""
    text = " ".join((message or "").replace("\u2019", "'").lower().split())
    if _asks_for_the_set(text):
        return False
    if "first word" in text:
        return True
    return any(word in text for word in ("say", "said", "saying"))


def _newest_file_choice(db: Session, user_id: uuid.UUID) -> dict | None:
    audio = (
        db.query(AudioFile)
        .filter(AudioFile.user_id == user_id)
        .order_by(AudioFile.created_at.desc())
        .first()
    )
    if audio is None:
        return None
    return {
        "intent": "analysis",
        "tool_name": "get_analysis",
        "arguments": {"file_id": str(audio.id)},
    }


def plan_with_rules(message: str) -> dict:
    if _asks_greeting(message):
        return {"intent": "greeting", "tool_name": "", "arguments": {}}
    if _asks_capabilities(message):
        return {"intent": "capabilities", "tool_name": "", "arguments": {}}
    profile = _profile_choice(message)
    if profile is not None:
        return profile
    text = message.lower()
    found = _UUID.findall(message)
    if found and not _wants_rollup(text):
        return {
            "intent": "analysis",
            "tool_name": "get_analysis",
            "arguments": {"file_id": found[0]},
        }
    if "upcoming" in text or "event" in text:
        return {
            "intent": "upcoming",
            "tool_name": "search_files",
            "arguments": _search_arguments(text),
        }
    if _wants_rollup(text):
        arguments: dict = {"group_by": _group_by(text)}
        window = _time_window(text)
        if "date_from" in window:
            arguments["time_from"] = window["date_from"]
        if "date_to" in window:
            arguments["time_to"] = window["date_to"]
        return {"intent": "summary", "tool_name": "run_summary", "arguments": arguments}
    if any(
        token in text for token in ("file", "recording", "duration", "longer", "shorter", "about")
    ):
        return {
            "intent": "inventory",
            "tool_name": "search_files",
            "arguments": _search_arguments(text),
        }
    return {"intent": "help", "tool_name": "", "arguments": {}}


# Parses the model's tool call: OpenAI format message.tool_calls[0].function.{name,arguments}.
def interpret_tool_message(message: dict) -> dict:
    calls = message.get("tool_calls") or []
    if not calls:
        return {"intent": "help", "tool_name": "", "arguments": {}}
    function = (calls[0] or {}).get("function") or {}
    name = str(function.get("name") or "")
    # Allow-list check: only our three tools can ever be executed.
    if name not in TOOL_NAMES:
        return {"intent": "help", "tool_name": "", "arguments": {}, "error": "unknown_tool"}
    # The model returns arguments as a JSON string, so decode it.
    raw = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except json.JSONDecodeError:
        return {"intent": "help", "tool_name": "", "arguments": {}, "error": "invalid_arguments"}
    if not isinstance(arguments, dict):
        return {"intent": "help", "tool_name": "", "arguments": {}, "error": "invalid_arguments"}
    return {"intent": "model", "tool_name": name, "arguments": arguments}


# Validates the model's JSON reply with Pydantic and returns the reply text.
def parse_structured_reply(payload: dict) -> str:
    return ChatReplyBody.model_validate(payload).reply.strip()


def _layer2_bits(layer2: dict | None) -> str:
    if not layer2:
        return ""
    bits: list[str] = []
    sentiment = (layer2.get("sentiment_lexicon") or {}).get("label")
    if sentiment:
        bits.append(f"sentiment {sentiment}")
    pace = (layer2.get("speaking_pace") or {}).get("words_per_minute")
    if pace is not None:
        bits.append(f"{pace} wpm")
    rms = (layer2.get("rms_energy") or {}).get("rms_mean")
    if rms is not None:
        bits.append(f"rms {rms}")
    counts = layer2.get("pos_counts") or {}
    if counts.get("noun_count") is not None:
        bits.append(f"{counts['noun_count']} nouns")
    if counts.get("adjective_count") is not None:
        bits.append(f"{counts['adjective_count']} adjectives")
    return ", ".join(str(bit) for bit in bits)


def _topics(taxonomy: dict | None) -> str:
    taxonomy = taxonomy or {}
    professional = ", ".join(taxonomy.get("professional_topics") or []) or "none"
    personal = ", ".join(taxonomy.get("personal_topics") or []) or "none"
    events = "; ".join(taxonomy.get("upcoming_events") or []) or "none"
    return f"Professional: {professional}. Personal: {personal}. Upcoming: {events}."


def _asked(message: str) -> str:
    """The question, without the file id line added for the planner."""
    kept = []
    for line in (message or "").splitlines():
        if line.strip().lower().startswith("file_id="):
            continue
        kept.append(line)
    text = "\n".join(kept).strip()
    return text or (message or "")


def _fit_reply(question: str, reply: str) -> str:
    """A repeated question or a raw tool error becomes a plain sentence."""
    text = (reply or "").strip()
    if not text:
        return _CANT_DETERMINE
    flat = " ".join(text.split())
    lowered = flat.lower()
    if "invalid_arguments" in lowered or "{'error'" in lowered or '{"error"' in lowered:
        return _CANT_DETERMINE
    asked = " ".join(_asked(question).split())
    # Short messages such as "hi" are not trimmed: "Hi there" is not a repeated question.
    repeated = (
        len(asked.split()) >= 3
        and lowered.startswith(asked.lower())
        and not flat[len(asked) : len(asked) + 1].isalnum()
    )
    if repeated:
        rest = flat[len(asked) :].lstrip(" ?.!:;")
        if not rest:
            return _CANT_DETERMINE
        return rest if rest.endswith((".", "!", "?")) else f"{rest}."
    return text


def _answer_sentence(question: str, reply: str) -> str:
    """A one-word reply to a first-word question becomes a sentence."""
    answer = " ".join((reply or "").split())
    asked = " ".join((question or "").replace("\u2019", "'").lower().split())
    if "first word" not in asked or not answer:
        return (reply or "").strip()
    if "first word" in answer.lower():
        return answer if answer.endswith(".") else f"{answer}."
    tokens = answer.strip(".,!?:;\"'").split()
    if len(tokens) == 1 and tokens[0]:
        return f"The first word is {tokens[0]}."
    return answer if answer.endswith(".") else f"{answer}."


def _bound(text: str) -> str:
    cleaned = " ".join(text.split()) if "\n" not in text else text.strip()
    cleaned = cleaned.strip() or "I could not build an answer from the tools."
    if len(cleaned) <= 4000:
        return cleaned
    shortened = cleaned[:4000].rsplit(" ", 1)[0]
    return shortened or cleaned[:4000]


def _compose_intent(intent: str, tool_name: str, message: str) -> str:
    if intent != "model":
        return intent
    if tool_name == "run_summary":
        return "summary"
    if tool_name == "profile_speaker":
        return "profile"
    if tool_name == "get_analysis":
        return "analysis"
    if tool_name == "search_files":
        lowered = message.lower()
        if "upcoming" in lowered or "event" in lowered:
            return "upcoming"
        return "inventory"
    return "help"


_UNHEARD = "not enough voice to tell"


def _heard(value: object) -> str:
    text = str(value or "").strip().rstrip(".")
    if text.lower() in {"", _UNHEARD}:
        return ""
    return text


def _profile_sentence(voice: dict) -> str:
    """One line. Empty traits stay out of the sentence."""
    signature = _heard(voice.get("voice_signature"))
    traits = []
    labels = (
        ("Presentation", "vocal_presentation"),
        ("Age band", "age_band"),
        ("Accent", "accent_region"),
        ("Style", "speaking_style"),
    )
    for label, key in labels:
        value = _heard(voice.get(key))
        if value:
            traits.append(f"{label}: {value}.")
    evidence = _heard(voice.get("evidence"))
    limits = _heard(voice.get("limits"))
    if not signature and not traits:
        reason = evidence or limits or "Not enough voice to tell."
        return reason if reason.endswith(".") else f"{reason}."
    parts = []
    if signature:
        lead = signature if signature.lower().startswith("estimate") else f"Estimate. {signature}"
        parts.append(lead if lead.endswith(".") else f"{lead}.")
    parts.extend(traits)
    for extra in (evidence, limits):
        if extra:
            parts.append(extra if extra.endswith(".") else f"{extra}.")
    return " ".join(parts)


# Deterministic reply builder; also the fallback if the LLM compose step fails.
def compose_with_rules(intent: str, tool_result: dict) -> str:
    if tool_result.get("error") == "not_found":
        if intent == "profile":
            return "I could not find a recording to profile."
        return "I could not find that recording on your account."
    if tool_result.get("error") == "rejected":
        return "That filter was rejected by content safety."
    if tool_result.get("error"):
        return _CANT_DETERMINE
    if intent == "profile":
        voices = tool_result.get("voices") or []
        filename = tool_result.get("filename") or "recording"
        if not voices:
            return f"No voice was heard on {filename}."
        lines = [filename]
        for voice in voices:
            if isinstance(voice, dict):
                lines.append(_profile_sentence(voice))
        return _bound("\n".join(lines))
    if intent == "upcoming":
        lines: list[str] = []
        for item in tool_result.get("items") or []:
            events = (item.get("taxonomy") or {}).get("upcoming_events") or []
            if events:
                lines.append(f"{item['filename']}: " + "; ".join(events))
        if not lines:
            return "No upcoming events in that set of recordings."
        return _bound("Upcoming events:\n" + "\n".join(lines))
    if intent == "summary":
        count = tool_result.get("file_count", 0)
        group_by = tool_result.get("group_by", "user")
        overall = tool_result.get("overall_summary") or "No recordings in this range."
        if not count:
            return "No completed recordings matched that summary."
        parts = [f"Summary grouped by {group_by} across {count} completed file(s). {overall}"]
        for group in tool_result.get("groups") or []:
            parts.append(f"{group.get('key')} ({group.get('file_count')}): {group.get('summary')}")
        return _bound("\n".join(parts))
    if intent == "analysis":
        filename = tool_result.get("filename") or "recording"
        summary = tool_result.get("summary") or tool_result.get("status") or "no summary"
        extra = _layer2_bits(tool_result.get("layer2"))
        line = f"{filename}: {summary} {_topics(tool_result.get('taxonomy'))}"
        if extra:
            line = f"{line} Analytics: {extra}."
        spoken = " ".join(str(tool_result.get("transcript") or "").split())
        if spoken:
            line = f"{line} Transcript: {spoken[:240]}"
        return _bound(line)
    if intent in {"inventory", "model"}:
        total = tool_result.get("total")
        if total is None and tool_result.get("filename"):
            return compose_with_rules("analysis", tool_result)
        if total == 0:
            return "No recordings matched that filter."
        lines = [f"{total} recording(s) matched."]
        for item in (tool_result.get("items") or [])[:8]:
            summary = item.get("summary") or item.get("status")
            extra = _layer2_bits(item.get("layer2"))
            suffix = f" Analytics: {extra}." if extra else ""
            lines.append(f"{item['filename']} ({item.get('duration_sec')}s): {summary}.{suffix}")
        return _bound("\n".join(lines))
    if intent == "greeting":
        return _GREETING_REPLY
    if intent == "capabilities":
        return _CAPABILITIES_REPLY
    return _HELP_REPLY


# Raw REST chat call; optionally advertises tools and/or forces a JSON schema reply.
def _azure_chat(messages: list[dict], tools: list[dict] | None, schema: dict | None) -> dict:
    settings = get_settings()
    if not settings.azure_openai_endpoint or not settings.azure_openai_api_key:
        raise RuntimeError("Azure OpenAI is not configured")
    url = (
        f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/"
        f"{settings.azure_openai_chat_deployment}/chat/completions"
        f"?api-version={settings.azure_openai_api_version}"
    )
    body: dict = {"messages": messages, **chat_sampling_fields()}
    # Tool calling: send the tool schemas; tool_choice="auto" lets the model decide.
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "ChatReply", "strict": True, "schema": schema},
        }
    headers = {"api-key": settings.azure_openai_api_key, "Content-Type": "application/json"}
    with httpx.Client(timeout=60) as client:
        response = client.post(url, headers=headers, json=body)
    response.raise_for_status()
    # Returns the assistant message (either content or tool_calls).
    return response.json()["choices"][0]["message"]


# Step 1 with the LLM: the model picks a tool (function calling) from TOOL_SPECS.
def plan_with_azure(message: str, history: list[dict]) -> dict:
    message_body = _azure_chat(build_planner_messages(message, history), TOOL_SPECS, None)
    choice = interpret_tool_message(message_body)
    if choice.get("error"):
        return {"intent": "help", "tool_name": "", "arguments": {}}
    return choice


# Step 3 with the LLM: write the user-facing reply (structured JSON).
def compose_with_azure(
    message: str,
    tool_name: str,
    tool_result: dict,
    intent: str = "",
    context: dict | None = None,
    previous_reply: str = "",
) -> str:
    message_body = _azure_chat(
        build_compose_messages(message, tool_name, tool_result, intent, context, previous_reply),
        None,
        CHAT_REPLY_SCHEMA,
    )
    content = message_body.get("content") or ""
    return parse_structured_reply(json.loads(content))


# Plain meanings of tool errors. The model sees these, never the raw error.
_ERROR_SUMMARIES = {
    "not_found": "That recording is not on this account.",
    "rejected": "Content safety rejected that filter.",
    "invalid_arguments": "The request did not match a recording or a filter the tools accept.",
    "unknown_tool": "That request is not one of the things this assistant can do.",
}
_ERROR_DEFAULT = "The tools could not answer that from this recording."
_NO_TOOL_INTENTS = {"greeting", "capabilities", "help"}


def _error_summary(intent: str, error: object) -> str:
    if error == "not_found" and intent == "profile":
        return "No recording was found on this account to profile."
    return _ERROR_SUMMARIES.get(str(error), _ERROR_DEFAULT)


def _previous_reply(history: list) -> str:
    for item in reversed(history or []):
        if item.get("role") == "assistant":
            return str(item.get("content") or "").strip()
    return ""


def compose_inputs(intent: str, tool_name: str, tool_result: dict, history: list) -> dict:
    """What the compose model sees: the turn kind, the tool result, and context."""
    if intent == "memory":
        return {
            "intent": "memory",
            "tool_result": {},
            "context": {"previous_question": previous_question(history)},
        }
    if tool_result.get("error"):
        return {
            "intent": "tool_error",
            "tool_result": {},
            "context": {"error_summary": _error_summary(intent, tool_result.get("error"))},
        }
    if not tool_name or intent in _NO_TOOL_INTENTS:
        turn = intent if intent in _NO_TOOL_INTENTS else "help"
        return {
            "intent": turn,
            "tool_result": {},
            "context": {"capabilities": list(CAPABILITIES)},
        }
    return {"intent": intent, "tool_result": tool_result, "context": {}}


def _rules_reply(intent: str, asked: str, tool_result: dict, history: list) -> str:
    """Mock-mode reply, also used when the compose model call fails."""
    if intent == "memory":
        return earlier_question_reply(history)
    if intent == "greeting":
        return _GREETING_REPLY
    reply = _avoid_repeat(compose_with_rules(intent, tool_result), history)
    reply = _fit_reply(asked, reply)
    return _answer_sentence(asked, reply)


def _has_tool(state: dict) -> bool:
    return bool(state.get("tool_name"))


def _no_tool(state: dict) -> bool:
    return not state.get("tool_name")


# plan -> tools -> compose, or plan -> compose. One tool call per turn.
# Nodes are closures so they can use db and user_id.
def build_chat_graph(db: Session, user_id: uuid.UUID):
    # Node 1: decide which tool (if any) to call; LLM in azure mode, rules in mock mode.
    # Memory, greeting, and capabilities pick no tool here; compose still writes the reply.
    # Profile, a named file id, and "what it says" pick their tool without the planner.
    def plan(state: ChatState) -> ChatState:
        with analysis_span("chat.plan"):
            if asks_earlier_question(state.get("message") or ""):
                state["intent"] = "memory"
                state["tool_name"] = ""
                state["tool_arguments"] = {}
                return state
            if _asks_greeting(state.get("message") or ""):
                state["intent"] = "greeting"
                state["tool_name"] = ""
                state["tool_arguments"] = {}
                return state
            if _asks_capabilities(state.get("message") or ""):
                state["intent"] = "capabilities"
                state["tool_name"] = ""
                state["tool_arguments"] = {}
                return state
            profile = _profile_choice(state.get("message") or "")
            if profile is not None:
                state["intent"] = profile["intent"]
                state["tool_name"] = profile["tool_name"]
                state["tool_arguments"] = profile["arguments"]
                return state
            message = state.get("message") or ""
            named = _named_file_choice(message)
            if named is not None:
                state["intent"] = named["intent"]
                state["tool_name"] = named["tool_name"]
                state["tool_arguments"] = named["arguments"]
                logger.info("chat plan user=%s tool=%s", user_id, state["tool_name"])
                return state
            if _about_what_it_says(message):
                newest = _newest_file_choice(db, user_id)
                if newest is not None:
                    state["intent"] = newest["intent"]
                    state["tool_name"] = newest["tool_name"]
                    state["tool_arguments"] = newest["arguments"]
                    logger.info("chat plan user=%s tool=%s", user_id, state["tool_name"])
                    return state
            if get_settings().llm_provider == "azure":
                choice = plan_with_azure(state.get("message") or "", state.get("history") or [])
            else:
                choice = plan_with_rules(state.get("message") or "")
            state["intent"] = choice.get("intent") or "help"
            state["tool_name"] = choice.get("tool_name") or ""
            state["tool_arguments"] = choice.get("arguments") or {}
            logger.info("chat plan user=%s tool=%s", user_id, state["tool_name"] or "none")
        return state

    # Node 2: run the chosen tool, always scoped to the caller's user_id.
    def tools(state: ChatState) -> ChatState:
        with analysis_span("chat.tool"):
            name = state.get("tool_name") or ""
            arguments = prepare_arguments(
                db,
                user_id,
                name,
                state.get("tool_arguments") or {},
                state.get("message") or "",
            )
            state["tool_arguments"] = arguments
            state["tool_result"] = execute_tool(
                db,
                user_id,
                name,
                arguments,
                state.get("message") or "",
            )
        return state

    # Node 3: write the final reply. In azure mode the compose model always writes it.
    def compose(state: ChatState) -> ChatState:
        with analysis_span("chat.compose"):
            tool_name = state.get("tool_name") or ""
            intent = _compose_intent(
                state.get("intent") or "help",
                tool_name,
                state.get("message") or "",
            )
            asked = _asked(state.get("message") or "")
            history = state.get("history") or []
            tool_result = state.get("tool_result") or {}
            if get_settings().llm_provider != "azure":
                state["reply"] = _rules_reply(intent, asked, tool_result, history)
                return state
            inputs = compose_inputs(intent, tool_name, tool_result, history)
            try:
                written = compose_with_azure(
                    asked,
                    tool_name,
                    inputs["tool_result"],
                    intent=inputs["intent"],
                    context=inputs["context"],
                    previous_reply=_previous_reply(history),
                )
            except Exception:
                # Only a failed model call gets the fixed reply.
                logger.warning("chat compose fell back to rules", exc_info=True)
                state["reply"] = _rules_reply(intent, asked, tool_result, history)
                return state
            reply = _fit_reply(asked, _bound(written))
            state["reply"] = _answer_sentence(asked, reply)
        return state

    plan_step = Step("plan", plan)
    tools_step = Step("tools", tools)
    compose_step = Finish("compose", compose)
    workflow = (
        WorkflowBuilder(start_executor=plan_step, name="chat")
        .add_edge(plan_step, tools_step, condition=_has_tool)
        .add_edge(plan_step, compose_step, condition=_no_tool)
        .add_edge(tools_step, compose_step)
        .build()
    )
    return workflow


def run_chat_agent(db: Session, user_id: uuid.UUID, message: str, history: list[dict]) -> dict:
    result = run_workflow(
        build_chat_graph(db, user_id),
        {
            "message": message,
            "history": history,
            "intent": "help",
            "tool_name": "",
            "tool_arguments": {},
            "tool_result": {},
            "reply": "",
        },
    )
    # Echo the tool call back to the UI so it can show which tool ran and with what.
    tool_calls = []
    if result.get("tool_name"):
        tool_calls.append(
            {
                "name": result["tool_name"],
                "arguments": result.get("tool_arguments") or {},
                "result": result.get("tool_result") or {},
            }
        )
    return {"reply": result.get("reply") or "", "tool_calls": tool_calls}
