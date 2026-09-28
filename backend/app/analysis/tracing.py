import logging

from opentelemetry import trace

from app.config import get_settings

logger = logging.getLogger(__name__)
_langfuse = None


def analysis_span(name: str):
    return trace.get_tracer("voice.analysis").start_as_current_span(name)


def record_generation(name: str, model: str, input_data: object, output_data: object) -> None:
    settings = get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return
    try:
        client = _client()
        observation = client.start_observation(
            name=name,
            as_type="generation",
            model=model,
            input=input_data,
            output=output_data,
        )
        observation.end()
        client.flush()
    except Exception:
        logger.warning("langfuse export failed", exc_info=True)


def _client():
    global _langfuse
    if _langfuse is None:
        from langfuse import Langfuse

        settings = get_settings()
        _langfuse = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return _langfuse
