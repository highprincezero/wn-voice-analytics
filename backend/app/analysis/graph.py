from typing import TypedDict

from agent_framework import WorkflowBuilder

from app.analysis.audio_features import measure_duration
from app.analysis.chunking import chunk_text
from app.analysis.layer2 import run_layer2
from app.analysis.mcp_audio import fetch_audio_via_mcp
from app.analysis.progress import current_tracker
from app.analysis.providers.intelligence import get_intelligence
from app.analysis.schemas import validate_layer1, validate_layer2
from app.analysis.taxonomy import union_taxonomy
from app.analysis.tracing import analysis_span
from app.config import get_settings
from app.guardrails.safety import get_safety
from app.workflow import Emit, Finish, Step, run_workflow


# Shared state passed between every node. TypedDict gives it typed keys;
# total=False makes every key optional so nodes can fill them in as the pipeline runs.
class AnalysisState(TypedDict, total=False):
    filename: str
    audio_bytes: bytes
    storage_key: str
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


# Each node takes the current state and returns it. The next node receives that dict.
def _prepare(state: AnalysisState) -> AnalysisState:
    # Records this step as a child span in the trace (timing + errors).
    with analysis_span("prepare"):
        try:
            # Measured from the decoded samples (wav, mp3, m4a, ogg, flac), never the LLM.
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


def _audio_for_transcription(state: AnalysisState) -> bytes:
    """Load the clip through MCP when a server URL and blob key are configured."""
    settings = get_settings()
    key = state.get("storage_key") or ""
    if settings.mcp_audio_url and key:
        _note(f"MCP tool fetch_audio {key}", stage="transcribe")
        audio = fetch_audio_via_mcp(settings.mcp_audio_url, key)
        state["audio_bytes"] = audio
        return audio
    return state["audio_bytes"]


def _transcribe(state: AnalysisState) -> AnalysisState:
    _begin("transcribe", "Transcribe started")
    with analysis_span("transcribe"):
        audio = _audio_for_transcription(state)
        # Provider (real or mock) is chosen by config; returns the transcript text.
        state["transcript"] = get_intelligence().transcribe(
            audio, state.get("filename") or "audio.wav"
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
        _note("Insights skipped because content safety blocked the transcript", stage="layer1")
        _note("Analytics skipped because content safety blocked the transcript", stage="layer2")
    return state


# After safety, exactly one of these edges runs.
def _allowed(state: dict) -> bool:
    return not state.get("blocked")


def _blocked(state: dict) -> bool:
    return bool(state.get("blocked"))


def _layer1(state: AnalysisState) -> AnalysisState:
    _begin("layer1", "Insights started")
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
    _finish("layer1", "Insights finished")
    return state


def _layer2(state: AnalysisState) -> AnalysisState:
    _begin("layer2", "Analytics started")
    with analysis_span("layer2"):
        # Analytics: optional measures (audio and text) selected from the saved options.
        state["layer2"] = run_layer2(
            audio=state["audio_bytes"],
            transcript=state.get("transcript") or "",
            duration_sec=float(state.get("duration_sec") or 0),
            options=state.get("options") or [],
            wav_ok=bool(state.get("wav_ok")),
        )
    _finish("layer2", "Analytics finished")
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
    _note("Validated summary and Analytics output", stage="layer2")
    return state


# A new workflow each recording. The framework keeps run state on the instance.
def build_graph():
    prepare = Step("prepare", _prepare)
    transcribe = Step("transcribe", _transcribe)
    shield = Step("shield", _shield)
    layer1 = Step("layer1", _layer1)
    layer2 = Step("layer2", _layer2)
    validate = Finish("validate", _validate)
    stop = Emit("stop")
    return (
        WorkflowBuilder(start_executor=prepare, name="analysis")
        .add_edge(prepare, transcribe)
        .add_edge(transcribe, shield)
        .add_edge(shield, layer1, condition=_allowed)
        .add_edge(shield, stop, condition=_blocked)
        .add_edge(layer1, layer2)
        .add_edge(layer2, validate)
        .build()
    )


def run_graph(
    audio_bytes: bytes,
    filename: str,
    options: list[dict],
    storage_key: str = "",
) -> dict:
    return run_workflow(
        build_graph(),
        {
            "audio_bytes": audio_bytes,
            "filename": filename,
            "options": options,
            "storage_key": storage_key,
        },
    )
