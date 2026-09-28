# Guardrails

Layer 2 is optional in the original brief and implemented here. The guardrails are not optional. They cover the prompt configuration a user saves, the parameters of a custom filter, and the transcript that comes out of the audio.

## Threats

| Source | What can go wrong |
| --- | --- |
| Prompt configuration | A user tries to replace the system prompt, add an unknown tool, or hide instructions in a parameter. |
| Custom filter | A filter string is used as a prompt fragment or a query injection. |
| Audio content | The transcript contains a jailbreak aimed at the model, or content the product should not send to the model. |
| Model output | The model returns extra fields, missing fields, or a summary that does not match the schema. |
| Other users | One account reads or deletes another account's files. |
| Chat question | The user hides instructions in the question or in history, names another user's file, or asks the agent to call a tool that is not in the set. |
| Chat tool output | A summary or taxonomy label is later treated as an instruction. |

## Controls

### 1. Whitelist only

`GET /api/v1/prompts/options` returns the catalog. A save is accepted only when every `option_id` is in that catalog, parameters are in the declared set, enums match the allowed values, and integers fall inside the declared range. Unknown keys are rejected. There is no text field for a system prompt, a developer prompt, or a free-form instruction.

The catalog today:

| Option | Parameters | What it computes |
| --- | --- | --- |
| `rms_energy` | `window_ms` in 100, 250, 500, 1000 | Windowed RMS of 16-bit PCM WAV |
| `pos_counts` | `top_n` from 1 to 20 | spaCy adjective and noun counts |
| `speaking_pace` | none | Words per minute from the transcript and the measured duration |
| `sentiment_lexicon` | none | Counts against a fixed word list |

Custom list filters use the same idea. `custom` must be `name:value`, and `name` must be one of `sentiment`, `adjective_count`, `noun_count`, `wpm`, `rms_mean`. Sentiment values must be `positive`, `neutral`, or `negative`. Numeric filters must sit inside a declared range.

Rollup `group_by` is `user`, `taxonomy_label`, `week`, or `sentiment`.

### 2. Prompt Shields on the way in

Before a configuration is stored, the server serializes the cleaned options and runs content safety on that string. Azure mode calls Azure AI Content Safety `text:shieldPrompt` and `text:analyze`. Mock mode flags jailbreak phrases such as "ignore previous instructions" and a small set of high-severity phrases. A hit returns HTTP 400 and nothing is saved.

The same shield runs on the custom filter string.

### 3. The transcript is data, not instructions

System prompts are constants in `backend/app/analysis/prompts.py`. The transcript is placed only in the user message, inside `<transcript>` markers. The system text tells the model to treat that text as untrusted and not to follow requests inside it. Tests assert that a jailbreak sentence does not appear in the system message.

After transcription, and before any summary call, the transcript goes through content safety:

- Prompt Shields for jailbreak or prompt injection.
- Category analysis for violence, self-harm, and hate. Azure uses four severity levels and blocks at `CONTENT_SAFETY_BLOCK_SEVERITY` (default 4). The mock provider uses the same decision shape.

If the check blocks, the file status becomes `blocked`, the reason is stored, the transcript is kept for the user's own record, and `summarize` is not called. Layer 2 does not run on a blocked file.

### 4. Structured output and a second validation

Azure chat calls set `response_format` to a strict JSON schema (`additionalProperties: false`, required fields listed). The request omits `temperature` unless `AZURE_OPENAI_CHAT_TEMPERATURE` is set. `gpt-5-mini` rejects an explicit temperature of 0. Duration is not requested from the model. It is measured from the file.

After the graph finishes, Pydantic validates:

- `duration_sec` is present and non-negative.
- `summary` is a non-empty string.
- `taxonomy` has `professional_topics`, `personal_topics`, and `upcoming_events`, each a list of strings.
- Layer 2 objects only use the known option keys.

A schema failure marks the file `failed`. The bad payload is not shown as a successful analysis.

Map-reduce has a recall guard: topics found on any chunk are unioned with the reduced taxonomy. A reduce step cannot silently drop a topic that a chunk already extracted.

### 5. Authorization

Every file, transcript, analysis, prompt row, and rollup is queried with the `user_id` from the JWT. A missing row is a 404, including when the id belongs to someone else. Blob downloads check that the key starts with `users/{user_id}/`. Passwords are hashed with bcrypt. Tokens are HS256 JWTs with an expiry.

### 6. Chat tools and the question text

`POST /api/v1/chat` runs the same content-safety check used for prompt configuration, on the new message and on every history turn. A hit is HTTP 400 and the graph does not run. History roles are only `user` and `assistant`. A `system` role or a `user_id` field on the body is rejected before the agent sees it.

The system text is a constant. The question is placed in the user message inside `<question>` tags. Tool results go in `<tool_result>` tags. Both are described as data. Tests check that a jailbreak sentence in the question does not appear in the system text.

The graph may execute only `search_files`, `get_analysis`, and `run_summary`. Any other name returns `unknown_tool` and is not called. Arguments are parsed with Pydantic models that forbid extra fields, so a model cannot pass `user_id`. Each query adds `AudioFile.user_id == <jwt subject>`. A foreign file id is `not_found`.

`get_analysis` returns the summary, taxonomy, and Layer 2 object. It does not return the transcript. The transcript was already shielded during analysis, and the chat agent does not need the raw words to answer questions about topics and upcoming events.

Azure mode asks for a JSON object whose only field is `reply`, then validates it again. Extra fields fail that check, and the API uses the rule-based reply instead of the model text. Mock mode never calls the chat deployment. It still runs the tool node, so the tests exercise the same whitelist and the same user filter.

The per-user rate limit sits in `get_current_user`, so chat is counted with the other authenticated routes.

`GET /api/v1/events` uses the same JWT filter. A caller cannot read another user's pipeline log, and the messages do not include transcript text.

## What this does not claim

- The mock safety provider is a stand-in for Azure AI Content Safety. It is deterministic and good enough for tests and the offline demo. Production must set `SAFETY_PROVIDER=azure`.
- Category lists in the mock are short on purpose. They are not a content policy.
- Schema validation checks shape, not factual accuracy. A model can still omit a topic. The union step only preserves topics the chunk step already returned.
- Blocked transcripts are stored because the user uploaded them. They are not sent to the chat model. A retention policy for blocked text is future work.
- This design does not scan audio for non-speech signals such as hidden ultrasonic content. Duration and RMS are the acoustic checks in this POC.
