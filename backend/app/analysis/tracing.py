import logging

from opentelemetry import trace

from app.config import get_settings

logger = logging.getLogger(__name__)
# Module-level Langfuse client, created lazily on first use (singleton).
_langfuse = None


# OpenTelemetry: start a span named `name` and make it the current span.
# Returns a context manager, used as `with analysis_span("layer1"):` in graph.py.
def analysis_span(name: str):
    return trace.get_tracer("voice.analysis").start_as_current_span(name)


# Langfuse: records one LLM call (prompt in, JSON out) as a 'generation' observation.
def record_generation(name: str, model: str, input_data: object, output_data: object) -> None:
    settings = get_settings()
    # No-op when Langfuse keys aren't configured.
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return
    try:
        client = _client()
        # start_observation(as_type="generation") opens the LLM-call record; end() closes it.
        observation = client.start_observation(
            name=name,
            as_type="generation",
            model=model,
            input=input_data,
            output=output_data,
        )
        observation.end()
        # flush() sends buffered events now (the process may exit before a background flush).
        client.flush()
    except Exception:
        # Tracing must never break the pipeline, so failures are only logged.
        logger.warning("langfuse export failed", exc_info=True)


def _client():
    global _langfuse
    if _langfuse is None:
        # Lazy import: the langfuse package is only loaded if tracing is enabled.
        from langfuse import Langfuse

        settings = get_settings()
        _langfuse = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return _langfuse
