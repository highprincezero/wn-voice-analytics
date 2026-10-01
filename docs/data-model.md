# Data model

## Names

| Short form | Meaning |
| --- | --- |
| ID | identifier |
| PK | primary key |
| UK | unique key |
| UUID | universally unique identifier |
| JSON | JavaScript Object Notation |
| jsonb | binary JSON, the column type used for structured fields |

[backend/sql/001_schema.sql](../backend/sql/001_schema.sql)

Each box is a table in the PostgreSQL database named voice. The account is the signed-in person. The users table holds that person.

```mermaid
erDiagram
  USERS ||--o{ AUDIO_FILES : owns
  USERS ||--o| PROMPT_CONFIGS : sets
  USERS ||--o{ TRANSCRIPTS : owns
  USERS ||--o{ ANALYSES : owns
  USERS ||--o{ ROLLUP_SUMMARIES : owns
  USERS ||--o{ GROUP_REPORTS : owns
  USERS ||--o{ FILE_EVENTS : logs
  USERS ||--o{ CHAT_SESSIONS : owns
  AUDIO_FILES ||--o| TRANSCRIPTS : has
  AUDIO_FILES ||--o| ANALYSES : has
  AUDIO_FILES ||--o{ FILE_EVENTS : logs
  CHAT_SESSIONS ||--o{ CHAT_MESSAGES : holds

  USERS {
    uuid id PK
    text email UK
    text password_hash
    text home_region
    timestamp created_at
  }
  AUDIO_FILES {
    uuid user_id PK
    uuid id PK
    text storage_key
    text status
    float duration_sec
    timestamp created_at
  }
  TRANSCRIPTS {
    uuid user_id PK
    uuid id PK
    uuid file_id
    text storage_key
    text provider
  }
  ANALYSES {
    uuid user_id PK
    uuid id PK
    uuid file_id
    text summary
    jsonb taxonomy
    jsonb layer2
  }
  PROMPT_CONFIGS {
    uuid user_id PK
    jsonb selections
    timestamp updated_at
  }
  ROLLUP_SUMMARIES {
    uuid user_id PK
    uuid id PK
    text group_by
    text trigger
    jsonb result
  }
  GROUP_REPORTS {
    uuid user_id PK
    uuid id PK
    text status
    jsonb groupings
    jsonb result
  }
  FILE_EVENTS {
    uuid user_id PK
    uuid id PK
    uuid file_id
    text stage
    text level
  }
  CHAT_SESSIONS {
    uuid user_id PK
    uuid id PK
    timestamp created_at
  }
  CHAT_MESSAGES {
    uuid user_id PK
    uuid id PK
    uuid session_id
    text role
    text content
    int seq
  }
```

| Table | What it stores |
| --- | --- |
| `users` | Email, hashed password, home region |
| `audio_files` | The recording, its length, and where processing stands |
| `transcripts` | The words |
| `analyses` | Summary, topics, and Analytics |
| `prompt_configs` | Which Analytics this account turned on |
| `rollup_summaries` | Combined summary of completed recordings |
| `group_reports` | The All groupings report: every grouping, code stats, and an AI summary per group |
| `file_events` | The processing log |
| `chat_sessions` | One chat for this account |
| `chat_messages` | The questions and answers in that chat |

## Rows

Where one account's rows and files live. Account is the person. PostgreSQL splits each table below into 16 pieces and keeps that account's rows in the same piece.

```mermaid
flowchart TB
  db[("PostgreSQL. Database named voice.")] --> users["users. Table. Kept whole so the email stays unique."]
  person["Account. The signed-in person."] --> users
  person --> uid["user_id. Column. That account's identifier."]
  uid --> parts["16 pieces of each table. Same account, same piece."]
  parts --> audio["audio_files. Table. One row per recording."]
  parts --> transcripts["transcripts. Table. The words."]
  parts --> analyses["analyses. Table. Summary, topics, and Analytics."]
  parts --> prompts["prompt_configs. Table. Which Analytics is turned on."]
  parts --> summaries["rollup_summaries. Table. Grouped summaries of completed recordings."]
  parts --> reports["group_reports. Table. All groupings reports."]
  parts --> events["file_events. Table. The processing log."]
  parts --> chatSessions["chat_sessions. Table. One chat for this account."]
  parts --> chatMessages["chat_messages. Table. The questions and answers in that chat."]
  person --> blob["Blob. File store. The audio, the transcript, and the analysis file."]
```

## Status

Values of the status column on the audio_files table.

```mermaid
flowchart LR
  uploaded["status uploaded"] --> processing["status processing"]
  processing --> completed["status completed"]
  processing --> blocked["status blocked"]
  processing --> failed["status failed"]
```

## Stages

The order of work recorded for one file. layer1 is Insights. layer2 is Analytics.

```mermaid
flowchart LR
  upload["stage upload"] --> queued["stage queued"] --> transcribe["stage transcribe"] --> safety["stage safety"] --> layer1["stage layer1. Insights"] --> layer2["stage layer2. Analytics"] --> saved["stage saved"]
```

## Summaries `group_by`

| `group_by` | |
| --- | --- |
| `user` | All recordings. The key is `all` |
| `taxonomy_label` | Topic |
| `day` `week` `month` | Calendar |
| `sentiment` | Sentiment |

## Blob keys

Paths in the Blob file store. These are files, not database tables.

```text
users/{user_id}/audio/{file_id}.{ext}
users/{user_id}/transcripts/{file_id}.json
users/{user_id}/analysis/{file_id}.json
```

| | |
| --- | --- |
| `ext` | `wav` `mp3` `m4a` `ogg` `flac` |
| Download | Under that account's prefix |
| Delete | The three objects and the rows. In Azure, Blob versioning keeps each old version for 7 days, counted from when it was written. Then a lifecycle rule purges it |
| Run again | Updates the transcript and the analysis. In Azure the replaced versions are purged the same way |
| `home_region` | `local` on this machine. One cloud region holds the account |
