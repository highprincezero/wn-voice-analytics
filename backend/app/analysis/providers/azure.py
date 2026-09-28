import json
import logging

import httpx

from app.analysis.prompts import (
    build_chunk_messages,
    build_layer1_messages,
    build_reduce_messages,
    build_rollup_messages,
)
from app.analysis.schemas import CHUNK_JSON_SCHEMA, LAYER1_JSON_SCHEMA
from app.analysis.tracing import record_generation
from app.config import get_settings

logger = logging.getLogger(__name__)


def chat_sampling_fields() -> dict:
    """Optional chat-completion fields.

    Temperature is omitted unless AZURE_OPENAI_CHAT_TEMPERATURE is set.
    gpt-5-mini rejects an explicit temperature of 0 and rejects max_tokens.
    A token cap, if one is added later, belongs in max_completion_tokens.
    """
    temperature = get_settings().azure_openai_chat_temperature
    if temperature is None:
        return {}
    return {"temperature": float(temperature)}


_ROLLUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}


class AzureIntelligence:
    def _require(self) -> None:
        settings = get_settings()
        if not settings.azure_openai_endpoint or not settings.azure_openai_api_key:
            raise RuntimeError("Azure OpenAI is not configured")

    def _chat(self, messages: list[dict], schema: dict, name: str) -> dict:
        self._require()
        settings = get_settings()
        url = (
            f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/"
            f"{settings.azure_openai_chat_deployment}/chat/completions"
            f"?api-version={settings.azure_openai_api_version}"
        )
        body = {
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
            **chat_sampling_fields(),
        }
        headers = {"api-key": settings.azure_openai_api_key, "Content-Type": "application/json"}
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                with httpx.Client(timeout=60) as client:
                    response = client.post(url, headers=headers, json=body)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                record_generation(name, settings.azure_openai_chat_deployment, messages, parsed)
                return parsed
            except Exception as exc:
                last_error = exc
                logger.warning("azure chat call failed: %s", type(exc).__name__)
        assert last_error is not None
        raise last_error

    def transcribe(self, audio: bytes, filename: str) -> str:
        self._require()
        settings = get_settings()
        url = (
            f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/"
            f"{settings.azure_openai_transcribe_deployment}/audio/transcriptions"
            f"?api-version={settings.azure_openai_api_version}"
        )
        headers = {"api-key": settings.azure_openai_api_key}
        files = {"file": (filename or "audio.wav", audio, "audio/wav")}
        data = {"model": settings.azure_openai_transcribe_deployment}
        with httpx.Client(timeout=180) as client:
            response = client.post(url, headers=headers, data=data, files=files)
        response.raise_for_status()
        return str(response.json()["text"])

    def summarize_and_classify(self, transcript: str) -> dict:
        return self._chat(build_layer1_messages(transcript), LAYER1_JSON_SCHEMA, "VoiceLayer1")

    def summarize_chunk(self, chunk: str) -> dict:
        return self._chat(build_chunk_messages(chunk), CHUNK_JSON_SCHEMA, "VoiceChunk")

    def reduce_summaries(self, partials: list[dict]) -> dict:
        return self._chat(build_reduce_messages(partials), LAYER1_JSON_SCHEMA, "VoiceReduce")

    def rollup_summary(self, summaries: list[str]) -> str:
        parsed = self._chat(build_rollup_messages(summaries), _ROLLUP_SCHEMA, "VoiceRollup")
        return str(parsed["summary"])
