import base64
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
from app.analysis.speaker_skill import SKILL_PROMPT
from app.analysis.tracing import record_generation
from app.config import get_settings

logger = logging.getLogger(__name__)


# Extra sampling params merged into the request body (empty dict = model defaults).
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


# JSON schema the model must follow for the rollup response (strict structured output).
_ROLLUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}

_PROFILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["voices"],
    "properties": {
        "voices": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "voice_signature",
                    "vocal_presentation",
                    "age_band",
                    "accent_region",
                    "speaking_style",
                    "evidence",
                    "limits",
                ],
                "properties": {
                    "voice_signature": {"type": "string"},
                    "vocal_presentation": {"type": "string"},
                    "age_band": {"type": "string"},
                    "accent_region": {"type": "string"},
                    "speaking_style": {"type": "string"},
                    "evidence": {"type": "string"},
                    "limits": {"type": "string"},
                },
            },
        }
    },
}


# Real provider: calls Azure OpenAI over plain REST with httpx (no SDK).
class AzureIntelligence:
    def _require(self) -> None:
        settings = get_settings()
        if not settings.azure_openai_endpoint or not settings.azure_openai_api_key:
            raise RuntimeError("Azure OpenAI is not configured")

    # Shared helper for every chat call: send messages, force JSON output, parse it.
    def _chat(self, messages: list[dict], schema: dict, name: str) -> dict:
        self._require()
        settings = get_settings()
        # Azure routes by *deployment* name (not model name) and requires an api-version
        # query param: {endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...
        url = (
            f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/"
            f"{settings.azure_openai_chat_deployment}/chat/completions"
            f"?api-version={settings.azure_openai_api_version}"
        )
        # Request shape: chat messages + response_format=json_schema (strict structured output).
        body = {
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
            # ** unpacks the optional sampling fields into the body.
            **chat_sampling_fields(),
        }
        # Azure key auth uses the 'api-key' header (not 'Authorization: Bearer').
        headers = {"api-key": settings.azure_openai_api_key, "Content-Type": "application/json"}
        last_error: Exception | None = None
        # Simple retry: up to 2 attempts, re-raise the last error if both fail.
        for _attempt in range(2):
            try:
                # httpx.Client in a `with`: opens a connection pool, closes it after the call.
                with httpx.Client(timeout=60) as client:
                    response = client.post(url, headers=headers, json=body)
                # Raises for 4xx/5xx so the retry/except path handles it.
                response.raise_for_status()
                # Response parsing: the JSON text lives at choices[0].message.content.
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                # Tracing: logs this LLM call (model, input, output) to Langfuse.
                record_generation(name, settings.azure_openai_chat_deployment, messages, parsed)
                return parsed
            except Exception as exc:
                last_error = exc
                logger.warning("azure chat call failed: %s", type(exc).__name__)
        assert last_error is not None
        raise last_error

    # Speech-to-text via the transcription deployment (multipart file upload).
    def transcribe(self, audio: bytes, filename: str) -> str:
        self._require()
        settings = get_settings()
        url = (
            f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/"
            f"{settings.azure_openai_transcribe_deployment}/audio/transcriptions"
            f"?api-version={settings.azure_openai_api_version}"
        )
        headers = {"api-key": settings.azure_openai_api_key}
        # Multipart file part: (filename, bytes, content-type).
        files = {"file": (filename or "audio.wav", audio, "audio/wav")}
        data = {"model": settings.azure_openai_transcribe_deployment}
        with httpx.Client(timeout=180) as client:
            # data= form fields, files= the audio; longer timeout for large files.
            response = client.post(url, headers=headers, data=data, files=files)
        response.raise_for_status()
        return str(response.json()["text"])

    # Public methods used by the graph: each maps to one prompt + one JSON schema.
    def summarize_and_classify(self, transcript: str) -> dict:
        return self._chat(build_layer1_messages(transcript), LAYER1_JSON_SCHEMA, "AudioLayer1")

    def summarize_chunk(self, chunk: str) -> dict:
        return self._chat(build_chunk_messages(chunk), CHUNK_JSON_SCHEMA, "AudioChunk")

    def reduce_summaries(self, partials: list[dict]) -> dict:
        return self._chat(build_reduce_messages(partials), LAYER1_JSON_SCHEMA, "AudioReduce")

    def rollup_summary(self, summaries: list[str]) -> str:
        parsed = self._chat(build_rollup_messages(summaries), _ROLLUP_SCHEMA, "AudioRollup")
        return str(parsed["summary"])

    def speaker_profile(self, audio: bytes, filename: str) -> dict:
        self._require()
        settings = get_settings()
        payload = base64.b64encode(audio[:4_000_000]).decode("ascii")
        audio_format = "mp3" if filename.lower().endswith(".mp3") else "wav"
        messages = [
            {"role": "system", "content": SKILL_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read this audio. Return one profile per voice."},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": payload, "format": audio_format},
                    },
                ],
            },
        ]
        deployments = []
        for name in (
            settings.azure_openai_chat_deployment,
            settings.azure_openai_transcribe_deployment,
        ):
            if name and name not in deployments:
                deployments.append(name)
        last_error: Exception | None = None
        for deployment in deployments:
            try:
                return self._profile_call(deployment, messages)
            except Exception as exc:
                last_error = exc
                logger.warning("speaker profile failed: %s", type(exc).__name__)
        assert last_error is not None
        raise last_error

    def _profile_call(self, deployment: str, messages: list[dict]) -> dict:
        settings = get_settings()
        url = (
            f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/"
            f"{deployment}/chat/completions"
            f"?api-version={settings.azure_openai_api_version}"
        )
        body = {
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "SpeakerProfile",
                    "strict": True,
                    "schema": _PROFILE_SCHEMA,
                },
            },
            **chat_sampling_fields(),
        }
        headers = {"api-key": settings.azure_openai_api_key, "Content-Type": "application/json"}
        with httpx.Client(timeout=120) as client:
            response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        record_generation("speaker-profile", deployment, [], parsed)
        return parsed
