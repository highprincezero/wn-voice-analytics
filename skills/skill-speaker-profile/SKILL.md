---
name: skill-speaker-profile
description: Estimate a speaker profile from one stored recording's audio when the user asks about the voice in chat. Returns a voice signature plus estimated vocal presentation, age band, accent region, and speaking style. Estimates only. Never treat them as identity facts.
---

# Speaker profile

The chat agent runs this skill on demand through its `profile_speaker` tool. It does not run during upload or transcription, and its result is not stored.

## When it runs

- The user asks for a speaker profile or to analyze the voice, or asks how the speaker sounds: gender, age, accent, emotion, or who is speaking. A fixed rule sends these questions to `profile_speaker` before the planner runs, and the Azure planner can pick it too.
- The recording is the one named in the question (file id or stored filename). With no name, it is the account's newest recording. Every lookup is scoped to the signed-in account.
- One tool call per chat turn. The profile goes back to the chat as the tool result and the reply is written from it. Nothing is written to the database or to Blob.

## Where the audio comes from

1. The stored audio is fetched fresh for the turn through the MCP `fetch_audio` tool when `MCP_AUDIO_URL` is set (Compose). When it is unset, or the call fails, the same blob is read directly.
2. Non-WAV uploads are converted to 16-bit PCM WAV with ffmpeg first.
3. A recording blocked by the content safety check is never sent to the model. The tool returns its metadata and the block reason only.

## Who answers

- `LLM_PROVIDER=azure`: the audio (up to about 4 MB) and the prompt below go to the `gpt-5-mini` chat deployment, then to the transcription deployment if that call fails. The reply must match a strict JSON schema. If every call fails, the acoustic fallback answers.
- Mock mode, and the fallback: `acoustic_profile()` in `backend/app/analysis/speaker_skill.py` measures duration and loudness only and says that age, accent, and style are not estimated.

## What it returns

`{"filename", "voices": [...], "file_id", "audio_fetched_via"}`, where `audio_fetched_via` is `mcp` or `blob`. Each voice has `voice_signature`, `vocal_presentation`, `age_band`, `accent_region`, `speaking_style`, `evidence`, and `limits`.

## Prompt

The system prompt sent with the audio. The code copy is `SKILL_PROMPT` in `backend/app/analysis/speaker_skill.py`; keep the two the same.

```text
Read each speaker from the raw audio. Do not infer the voice from words. Return one
profile per voice, in the order the voices first appear. Fields for each voice:
voice_signature (pitch range, pace, loudness, timbre; do not invent an embedding),
vocal_presentation (lower or higher pitch, with confidence; this is not gender), age_band
(teen, young adult, adult, or older adult, plus confidence; no exact age), accent_region
(language or accent, plus confidence; this is not nationality), speaking_style (how the
clip sounds, plus confidence; this is not a personality diagnosis), evidence (one sentence
of audible cues; quote no transcript), limits (say the clip is too short, noisy, or
single-tone when the estimate is weak). Label every trait as an estimate. A short greeting
is not enough for age, accent, or style. Prefer not enough voice to tell over a guess.
```
