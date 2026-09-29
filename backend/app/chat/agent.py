"""LangGraph tool-calling agent. Mock mode picks tools with fixed rules."""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import TypedDict

import httpx
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.analysis.providers.azure import chat_sampling_fields
from app.analysis.tracing import analysis_span
from app.chat.prompts import (
    CHAT_REPLY_SCHEMA,
    build_compose_messages,
    build_planner_messages,
)
from app.chat.schemas import ChatReplyBody
from app.chat.tools import TOOL_NAMES, TOOL_SPECS, execute_tool
from app.config import get_settings
from app.timeutil import utcnow

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
    if "week" in text and "topic" not in text and "taxonomy" not in text:
        return "week"
    if "topic" in text or "taxonomy" in text or "label" in text:
        return "taxonomy_label"
    return "user"


def _wants_rollup(text: str) -> bool:
    return any(token in text for token in ("summarize", "summary", "rollup", "group by"))


# Mock-mode planner: keyword rules decide which tool to call and with what arguments.
def plan_with_rules(message: str) -> dict:
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
    if tool_name == "get_analysis":
        return "analysis"
    if tool_name == "search_files":
        lowered = message.lower()
        if "upcoming" in lowered or "event" in lowered:
            return "upcoming"
        return "inventory"
    return "help"


# Deterministic reply builder; also the fallback if the LLM compose step fails.
def compose_with_rules(intent: str, tool_result: dict) -> str:
    if tool_result.get("error") == "not_found":
        return "I could not find that recording on your account."
    if tool_result.get("error") == "rejected":
        return "That filter was rejected by content safety."
    if tool_result.get("error"):
        return (
            "I can search your files by date, duration, or taxonomy, "
            "open one file by id, or summarize them."
        )
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
            line = f"{line} Layer 2: {extra}."
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
            suffix = f" Layer 2: {extra}." if extra else ""
            lines.append(f"{item['filename']} ({item.get('duration_sec')}s): {summary}.{suffix}")
        return _bound("\n".join(lines))
    return (
        "Ask about your recordings. For example: what upcoming events did I mention this week, "
        "or summarize my files by topic. You can also ask for one file by its id."
    )


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


# Step 3 with the LLM: turn the tool result into a user-facing answer (structured JSON).
def compose_with_azure(message: str, tool_name: str, tool_result: dict) -> str:
    message_body = _azure_chat(
        build_compose_messages(message, tool_name, tool_result),
        None,
        CHAT_REPLY_SCHEMA,
    )
    content = message_body.get("content") or ""
    return parse_structured_reply(json.loads(content))


# Builds the agent graph: plan -> (tools) -> compose -> END. One tool call per turn,
# not an open-ended loop. Nodes are closures so they can use db/user_id.
def build_chat_graph(db: Session, user_id: uuid.UUID):
    # Node 1: decide which tool (if any) to call; LLM in azure mode, rules in mock mode.
    def plan(state: ChatState) -> ChatState:
        with analysis_span("chat.plan"):
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
            state["tool_result"] = execute_tool(
                db,
                user_id,
                state.get("tool_name") or "",
                state.get("tool_arguments") or {},
            )
        return state

    # Node 3: write the final reply from the tool result.
    def compose(state: ChatState) -> ChatState:
        with analysis_span("chat.compose"):
            intent = _compose_intent(
                state.get("intent") or "help",
                state.get("tool_name") or "",
                state.get("message") or "",
            )
            tool_result = state.get("tool_result") or {}
            fallback = compose_with_rules(intent, tool_result)
            if get_settings().llm_provider == "azure" and state.get("tool_name"):
                try:
                    state["reply"] = _bound(
                        compose_with_azure(
                            state.get("message") or "",
                            state.get("tool_name") or "",
                            tool_result,
                        )
                    )
                except Exception:
                    # If the LLM fails, fall back to the rule-based reply instead of erroring.
                    logger.warning("chat compose fell back to rules", exc_info=True)
                    state["reply"] = fallback
            else:
                state["reply"] = fallback
        return state

    # Routing function: go to 'tools' if a tool was chosen, otherwise straight to 'compose'.
    def route(state: ChatState) -> str:
        if state.get("tool_name"):
            return "tools"
        return "compose"

    # StateGraph + add_node/add_edge/add_conditional_edges: same pattern as analysis/graph.py.
    graph = StateGraph(ChatState)
    graph.add_node("plan", plan)
    graph.add_node("tools", tools)
    graph.add_node("compose", compose)
    graph.set_entry_point("plan")
    # Conditional edge: route(state)'s return value picks the next node from this map.
    graph.add_conditional_edges("plan", route, {"tools": "tools", "compose": "compose"})
    graph.add_edge("tools", "compose")
    graph.add_edge("compose", END)
    # compile() returns the runnable graph.
    return graph.compile()


# API entry point: invoke() runs the graph with an initial state and returns the final one.
def run_chat_agent(db: Session, user_id: uuid.UUID, message: str, history: list[dict]) -> dict:
    result = build_chat_graph(db, user_id).invoke(
        {
            "message": message,
            "history": history,
            "intent": "help",
            "tool_name": "",
            "tool_arguments": {},
            "tool_result": {},
            "reply": "",
        }
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
