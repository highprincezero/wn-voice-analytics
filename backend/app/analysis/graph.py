from typing import TypedDict

# StateGraph builds the pipeline as a graph of nodes; END is the built-in terminal node.
from langgraph.graph import END, StateGraph

from app.analysis.audio_features import measure_duration
from app.analysis.chunking import chunk_text
from app.analysis.layer2 import run_layer2
from app.analysis.progress import current_tracker
from app.analysis.providers.factory import get_intelligence
from app.analysis.schemas import validate_layer1, validate_layer2
from app.analysis.taxonomy import union_taxonomy
from app.analysis.tracing import analysis_span
from app.config import get_settings
from app.guardrails.safety import get_safety


# Shared state passed between every node. TypedDict gives it typed keys;
# total=False makes every key optional so nodes can fill them in as the pipeline runs.
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


# Progress helpers: report stage start/finish to the live tracker, if one is active.
def _begin(stage: str, message: str) -> None:
    tracker = current_tracker()
    if tracker is not None:
        tracker.begin(stage, message)


def _finish(stage: str, message: str) -> None:
    tracker = current_tracker()
    if tracker is not None:
        tracker.finish(stage, message)


def _note(message: str, *, stage: str | None = None) -> None:
    tracker = current_tracker()
    if tracker is not None:
        tracker.note(message, stage=stage)


# Node: every node takes the current state and returns the updated state;
# LangGraph merges the returned keys into the shared state before the next node runs.
def _prepare(state: AnalysisState) -> AnalysisState:
    # Records this step as a child span in the trace (timing + errors).
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
    _note(f"Audio prepared ({state.get('duration_sec') or 0} s)")
    return state


def _transcribe(state: AnalysisState) -> AnalysisState:
    _begin("transcribe", "Transcribe started")
    with analysis_span("transcribe"):
        # Provider (real or mock) is chosen by config; returns the transcript text.
        state["transcript"] = get_intelligence().transcribe(
            state["audio_bytes"], state.get("filename") or "audio.wav"
        )
    _finish("transcribe", "Transcribe finished")
    return state


def _shield(state: AnalysisState) -> AnalysisState:
    _begin("safety", "Safety check started")
    with analysis_span("content_safety"):
        # Content-safety check; a blocked result short-circuits the rest of the graph.
        result = get_safety().analyze_content(state.get("transcript") or "")
        state["blocked"] = result.blocked
        state["block_reason"] = result.reason
    _finish("safety", "Safety check finished")
    if state.get("blocked"):
        _note("Layer 1 skipped because content safety blocked the transcript", stage="layer1")
        _note("Layer 2 skipped because content safety blocked the transcript", stage="layer2")
    return state


# Routing function for add_conditional_edges: reads the state and returns a label
# ('stop' or 'go') that is mapped to the next node in build_graph().
def _route_after_shield(state: AnalysisState) -> str:
    return "stop" if state.get("blocked") else "go"


def _layer1(state: AnalysisState) -> AnalysisState:
    _begin("layer1", "Layer 1 started")
    with analysis_span("layer1"):
        provider = get_intelligence()
        settings = get_settings()
        text = state.get("transcript") or ""
        # Short transcript: one LLM call. Long transcript: map-reduce over chunks.
        if len(text) <= settings.chunk_chars:
            result = provider.summarize_and_classify(text)
            strategy = "single"
            chunk_count = 1
        else:
            parts = chunk_text(text, settings.chunk_chars)[: settings.max_chunks]
            # Map step: summarize each chunk independently.
            partials = [provider.summarize_chunk(part) for part in parts]
            # Reduce step: merge the chunk summaries into one summary + taxonomy.
            result = provider.reduce_summaries(partials)
            result["taxonomy"] = union_taxonomy([result.get("taxonomy") or {}, *partials])
            strategy = "map_reduce"
            chunk_count = len(parts)
        state["summary"] = str(result["summary"]).strip()
        state["taxonomy"] = result["taxonomy"]
        state["summary_strategy"] = strategy
        state["chunk_count"] = chunk_count
    _finish("layer1", "Layer 1 finished")
    return state


def _layer2(state: AnalysisState) -> AnalysisState:
    _begin("layer2", "Layer 2 started")
    with analysis_span("layer2"):
        # Layer 2: extra analytics (audio + text features) selected via the request options.
        state["layer2"] = run_layer2(
            audio=state["audio_bytes"],
            transcript=state.get("transcript") or "",
            duration_sec=float(state.get("duration_sec") or 0),
            options=state.get("options") or [],
            wav_ok=bool(state.get("wav_ok")),
        )
    _finish("layer2", "Layer 2 finished")
    return state


def _validate(state: AnalysisState) -> AnalysisState:
    with analysis_span("validate"):
        # Schema-validate / normalise the model output before it is stored.
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
    _note("Validated summary and Layer 2 output", stage="layer2")
    return state


# Wires the nodes together and compiles them into a runnable graph.
def build_graph():
    # StateGraph(AnalysisState): the state schema every node reads from and writes to.
    graph = StateGraph(AnalysisState)
    # add_node(name, fn): registers a step; the name is what edges refer to.
    graph.add_node("prepare", _prepare)
    graph.add_node("transcribe", _transcribe)
    graph.add_node("shield", _shield)
    graph.add_node("layer1", _layer1)
    graph.add_node("layer2", _layer2)
    graph.add_node("validate", _validate)
    # set_entry_point: the first node to run (same as add_edge(START, "prepare")).
    graph.set_entry_point("prepare")
    # add_edge(a, b): fixed transition, b always runs after a.
    graph.add_edge("prepare", "transcribe")
    graph.add_edge("transcribe", "shield")
    # add_conditional_edges: after 'shield', call _route_after_shield(state) and
    # use its return value as a key into the map: 'stop' ends the run, 'go' continues.
    graph.add_conditional_edges("shield", _route_after_shield, {"stop": END, "go": "layer1"})
    graph.add_edge("layer1", "layer2")
    graph.add_edge("layer2", "validate")
    # END: finishing here makes invoke() return the final state.
    graph.add_edge("validate", END)
    # compile() validates the wiring and returns a runnable graph with .invoke()/.stream().
    return graph.compile()


# Compiled graph is cached at module level so it is only built once per process.
_compiled = None


def run_graph(audio_bytes: bytes, filename: str, options: list[dict]) -> dict:
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    # invoke(initial_state) runs the graph synchronously and returns the final state dict.
    return _compiled.invoke({"audio_bytes": audio_bytes, "filename": filename, "options": options})
