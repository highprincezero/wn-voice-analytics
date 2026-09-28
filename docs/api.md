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

## Meta

### GET /api/v1/health

`{"status": "ok"}`.

### GET /api/v1/health/ready

Checks the database. `503` when it cannot connect.

### GET /api/v1/meta

`{"llm_provider", "safety_provider", "blob_provider", "analysis_mode", "broker", "home_region"}`. The UI uses this to show the mock-mode caption.

### GET /

`{"service": "voice-analytics"}`.
