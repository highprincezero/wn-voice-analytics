from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.analysis.audio_features import measure_duration
from app.analysis.chunking import chunk_text
from app.analysis.layer2 import run_layer2
from app.analysis.providers.factory import get_intelligence
from app.analysis.schemas import validate_layer1, validate_layer2
from app.analysis.taxonomy import union_taxonomy
from app.analysis.tracing import analysis_span
from app.config import get_settings
from app.guardrails.safety import get_safety


class AnalysisState(TypedDict, total=False):
    filename: str
    audio_bytes: bytes
    options: list
    duration_sec: float
    wav_ok: bool
    transcript: str
    blocked: bool
    block_reason: str
    summary: str
    taxonomy: dict
    summary_strategy: str
    chunk_count: int
    layer2: dict


def _prepare(state: AnalysisState) -> AnalysisState:
    with analysis_span("prepare"):
        try:
            state["duration_sec"] = round(measure_duration(state["audio_bytes"]), 3)
            state["wav_ok"] = True
        except ValueError:
            state["duration_sec"] = 0.0
            state["wav_ok"] = False
        state["blocked"] = False
        state["block_reason"] = ""
        state["options"] = state.get("options") or []
    return state


def _transcribe(state: AnalysisState) -> AnalysisState:
    with analysis_span("transcribe"):
        state["transcript"] = get_intelligence().transcribe(
            state["audio_bytes"], state.get("filename") or "audio.wav"
        )
    return state


def _shield(state: AnalysisState) -> AnalysisState:
    with analysis_span("content_safety"):
        result = get_safety().analyze_content(state.get("transcript") or "")
        state["blocked"] = result.blocked
        state["block_reason"] = result.reason
    return state


def _route_after_shield(state: AnalysisState) -> str:
    return "stop" if state.get("blocked") else "go"


def _layer1(state: AnalysisState) -> AnalysisState:
    with analysis_span("layer1"):
        provider = get_intelligence()
        settings = get_settings()
        text = state.get("transcript") or ""
        if len(text) <= settings.chunk_chars:
            result = provider.summarize_and_classify(text)
            strategy = "single"
            chunk_count = 1
        else:
            parts = chunk_text(text, settings.chunk_chars)[: settings.max_chunks]
            partials = [provider.summarize_chunk(part) for part in parts]
            result = provider.reduce_summaries(partials)
            result["taxonomy"] = union_taxonomy([result.get("taxonomy") or {}, *partials])
            strategy = "map_reduce"
            chunk_count = len(parts)
        state["summary"] = str(result["summary"]).strip()
        state["taxonomy"] = result["taxonomy"]
        state["summary_strategy"] = strategy
        state["chunk_count"] = chunk_count
    return state


def _layer2(state: AnalysisState) -> AnalysisState:
    with analysis_span("layer2"):
        state["layer2"] = run_layer2(
            audio=state["audio_bytes"],
            transcript=state.get("transcript") or "",
            duration_sec=float(state.get("duration_sec") or 0),
            options=state.get("options") or [],
            wav_ok=bool(state.get("wav_ok")),
        )
    return state


def _validate(state: AnalysisState) -> AnalysisState:
    with analysis_span("validate"):
        validated = validate_layer1(
            {
                "duration_sec": state.get("duration_sec") or 0,
                "summary": state.get("summary") or "",
                "taxonomy": state.get("taxonomy") or {},
            }
        )
        state["summary"] = validated["summary"]
        state["taxonomy"] = validated["taxonomy"]
        state["layer2"] = validate_layer2(state.get("layer2") or {})
    return state


def build_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("prepare", _prepare)
    graph.add_node("transcribe", _transcribe)
    graph.add_node("shield", _shield)
    graph.add_node("layer1", _layer1)
    graph.add_node("layer2", _layer2)
    graph.add_node("validate", _validate)
    graph.set_entry_point("prepare")
    graph.add_edge("prepare", "transcribe")
    graph.add_edge("transcribe", "shield")
    graph.add_conditional_edges("shield", _route_after_shield, {"stop": END, "go": "layer1"})
    graph.add_edge("layer1", "layer2")
    graph.add_edge("layer2", "validate")
    graph.add_edge("validate", END)
    return graph.compile()


_compiled = None


def run_graph(audio_bytes: bytes, filename: str, options: list[dict]) -> dict:
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled.invoke({"audio_bytes": audio_bytes, "filename": filename, "options": options})
