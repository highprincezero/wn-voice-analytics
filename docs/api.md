# API reference

Base URL locally: `http://localhost:8000`. All routes below are under `/api/v1` except `GET /`.

Authenticated routes require:

```http
Authorization: Bearer <access_token>
```

Errors use `{"detail": "..."}`. Validation errors use FastAPI's default list shape.

## Auth

### POST /api/v1/auth/signup

Body: `{"email": "ada@example.com", "password": "correct-horse"}`.

Password length is 8 to 72 bytes. Email is stored lowercased. `201` returns:

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "user_id": "<uuid>",
  "email": "ada@example.com"
}
```

`409` if the email exists. `400` if the password is rejected.

### POST /api/v1/auth/login

Same body. `200` with the same token object. `401` on a bad email or password.

### GET /api/v1/auth/me

```json
{
  "id": "<uuid>",
  "email": "ada@example.com",
  "home_region": "local",
  "created_at": "2026-09-28T12:00:00"
}
```

## Files

### POST /api/v1/files

`multipart/form-data`, field name `files`, one or more parts, at most 10. Each part at most `MAX_UPLOAD_BYTES` (20 MB). Extensions: `wav`, `mp3`, `m4a`, `ogg`, `flac`.

`201`:

```json
{ "items": [ { "id": "<uuid>", "status": "uploaded", "storage_key": "users/<user_id>/audio/<file_id>.wav" } ] }
```

Each item also includes `original_filename`, `content_type`, `byte_size`, `duration_sec`, `created_at`, `error_message`, and, once analysis has finished in the same request (`ANALYSIS_MODE=inline`), `summary`, `taxonomy`, `layer2`, `summary_strategy`, `provider`, and `block_reason`.

The object is written, the row is committed, then analysis is queued. With Celery, the first response usually still says `uploaded`.

### GET /api/v1/files

Query parameters, all optional:

| Name | Meaning |
| --- | --- |
| `date_from` | ISO timestamp, inclusive |
| `date_to` | ISO timestamp, inclusive |
| `min_duration` | Seconds |
| `max_duration` | Seconds |
| `taxonomy` | Case-insensitive substring of any taxonomy list |
| `custom` | `name:value`. Names: `sentiment`, `adjective_count`, `noun_count`, `wpm`, `rms_mean` |
| `limit` | 1 to 200, default 50 |
| `offset` | Default 0 |

`custom` sentiment values are `positive`, `neutral`, `negative`. Numeric filters are greater-than-or-equal and must sit in the catalog range.

Response: `{"items": [...], "total": <int>}`. `total` is the filtered count before limit and offset. Rows always belong to the caller.

### GET /api/v1/files/{file_id}

Same object as a list item, plus `transcript` and `transcript_key`. `404` if the id is missing or belongs to someone else.

### GET /api/v1/files/{file_id}/audio

Raw bytes, `Content-Type` from the stored file.

### POST /api/v1/files/{file_id}/analyze

Sets status back to `uploaded` and queues the pipeline again. Returns the file object.

### DELETE /api/v1/files/{file_id}

`204`. Deletes the audio, transcript, and analysis blobs, then the row. Child rows cascade.

## Prompts

### GET /api/v1/prompts/options

No auth. Returns `{ "options": [ { "id", "label", "description", "params" } ] }`.

Option ids: `rms_energy` (`window_ms` enum 100, 250, 500, 1000), `pos_counts` (`top_n` integer 1 to 20), `speaking_pace` (no params), `sentiment_lexicon` (no params).

### GET /api/v1/prompts/config

`{"selections": [{"option_id": "rms_energy", "params": {"window_ms": 250}}]}`. Empty list if nothing is saved. An empty list means a later analysis stores Layer 2 as an empty object. Layer 1 still runs.

### PUT /api/v1/prompts/config

Body: `{"selections": [{"option_id": "pos_counts", "params": {"top_n": 5}}]}`.

Unknown option ids, unknown parameter names, values outside the enum or range, and content-safety hits return `400`. The response is the cleaned selection list.

## Summaries

### POST /api/v1/summaries

```json
{
  "group_by": "taxonomy_label",
  "time_from": "2026-09-01T00:00:00",
  "time_to": "2026-09-28T23:59:59"
}
```

`group_by` is `user`, `taxonomy_label`, `week`, or `sentiment`. Times are optional. `201` returns the stored rollup. `trigger` is `on_demand`. `result` contains `file_count`, `groups` (`key`, `file_count`, `summary`), `overall_summary`, and `provider`.

Only analyses with status `completed` are included.

### GET /api/v1/summaries

`{"items": [ ... ]}`, newest first, at most 50. Scheduled runs have `trigger` `schedule`.

## Chat

### POST /api/v1/chat

```json
{
  "message": "what upcoming events did I mention this week?",
  "history": [
    {"role": "user", "content": "Attached 1 file(s): sample_call.wav."},
    {"role": "assistant", "content": "Processing has started for 1 file(s): sample_call.wav."}
  ]
}
```

`message` is 1 to 2000 characters. `history` is optional, at most 8 turns, each `role` of `user` or `assistant`. Extra fields, including a `user_id`, are rejected with `422`. The server uses the JWT subject and ignores any identity in the body.

`200`:

```json
{
  "reply": "Upcoming events:\nsample_call.wav: Please send the notes by Friday.",
  "tool_calls": [
    {
      "name": "search_files",
      "arguments": {"date_from": "2026-09-28T00:00:00"},
      "result": {
        "total": 1,
        "items": [
          {
            "id": "<uuid>",
            "filename": "sample_call.wav",
            "taxonomy": {"upcoming_events": ["Please send the notes by Friday."]}
          }
        ]
      }
    }
  ]
}
```

`tool_calls` has at most one entry. `name` is `search_files`, `get_analysis`, or `run_summary`. `search_files` accepts `date_from`, `date_to`, `min_duration`, `max_duration`, and `taxonomy`. `get_analysis` accepts `file_id`. `run_summary` accepts `group_by` (`user`, `taxonomy_label`, `week`, `sentiment`) and optional times. A miss, including another user's file id, is `result.error` of `not_found` and a reply that says the recording is not on this account.

`400` when content safety blocks the message or any history turn. `401` without a token.

With `LLM_PROVIDER=mock` the plan step is a fixed set of rules. "what upcoming events did I mention this week?" calls `search_files` with the start of the current week. "summarize my files by topic" calls `run_summary` with `group_by` `taxonomy_label`. A question that names a file UUID calls `get_analysis`. The reply is built from the tool result, not from a model.

Authenticated routes, including this one, are also counted by the per-user rate limit. Over the limit is `429` with a `Retry-After` header and `{"detail": "rate limit exceeded"}`. Health, sign-up, login, meta, and the prompt catalog are not counted.

## Meta

### GET /api/v1/health

`{"status": "ok"}`.

### GET /api/v1/health/ready

Checks the database. `503` when it cannot connect.

### GET /api/v1/meta

`{"llm_provider", "safety_provider", "blob_provider", "analysis_mode", "broker", "home_region"}`. The UI uses this to show the mock-mode caption.

### GET /

`{"service": "voice-analytics"}`.
