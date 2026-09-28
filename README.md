# Voice Analytics

Proof of concept for a voice analytics platform. A person signs up, uploads one or more audio files, and the service stores the audio, a transcript, a duration, a summary, and a taxonomy. Optional Layer 2 features (energy, parts of speech, pace, lexicon sentiment) come from a whitelist the user picks. A background job rolls those file summaries up on a schedule and on demand.

The local stack runs with `docker compose up` and no cloud API keys. Mock providers stand in for transcription, the chat model, and content safety. Set `LLM_PROVIDER=azure` and `SAFETY_PROVIDER=azure` when Azure OpenAI and Azure AI Content Safety are available.

## What it does

- Sign up and log in. Passwords are hashed with bcrypt. The API issues an HS256 JWT.
- Upload one file or up to ten at a time. Accepted extensions are wav, mp3, m4a, ogg, and flac. Size limit is 20 MB.
- List files with filters for date, duration, taxonomy text, and Layer 2 values (sentiment, adjective count, noun count, words per minute, RMS).
- Configure Layer 2 from a fixed catalog. Users never type a system prompt.
- Per file (Layer 1): measured duration, summary, and taxonomy (`professional_topics`, `personal_topics`, `upcoming_events`).
- Layer 2: RMS energy, spaCy noun and adjective counts, speaking pace, and a fixed sentiment lexicon.
- Roll up a user's completed files by user, taxonomy label, week, or sentiment. The same job runs on a schedule and when the user asks.
- Optional Assistant mode: a chat that uploads through the same APIs, shows the pipeline stage and a live job log from the API, then answers follow-up questions with a fixed set of tools. Mock mode answers with deterministic rules and still calls those tools.

## Repository layout

```text
backend/            FastAPI app, LangGraph pipeline, Celery worker, SQL schema, tests
frontend/           Streamlit client
samples/            Bundled 4 second WAV used by the demo
scripts/            Regenerates that WAV
docs/               Architecture, data model, guardrails, scaling, API, diagrams
infra/terraform/    Azure Terraform, one module per region
docker-compose.yml  API, Streamlit, Postgres, Redis, worker, Azurite
.github/workflows/  Lint, pytest, terraform fmt and validate
DEMO_SCRIPT.md      5 to 7 minute screen-recording script
```

## Architecture

Three layers. Streamlit is the only UI. It calls FastAPI over HTTP. FastAPI owns auth, the database, and blob keys. Workers run the LangGraph pipeline against Azure OpenAI, or against the mock providers when no keys are configured.

![System architecture](docs/diagrams/system-architecture.png)

[SVG](docs/diagrams/system-architecture.svg) · [source](docs/diagrams/system-architecture.mmd)

```mermaid
flowchart TB
  subgraph interfaceLayer [Interface layer]
    UI[Streamlit classic and assistant]
  end

  subgraph implementationLayer [Implementation layer]
    FD[Azure Front Door]
    APIM[API Management Basic JWT and per-user limit]
    API[FastAPI JWT and Redis rate limit]
    SB[Service Bus queues]
    WK[Container Apps workers]
    JOB[Scheduled rollup job]
    PG[(PostgreSQL zone-redundant HA)]
    BLOB[(Blob storage GZRS)]
    CACHE[(Redis cache and rate limit)]
    OBS[App Insights and OpenTelemetry]
  end

  subgraph intelligenceLayer [Intelligence layer]
    LG[LangGraph analysis pipeline]
    CHAT[LangGraph chat agent]
    STT[gpt-4o-transcribe]
    LLM[gpt-4.1-mini structured output]
    CS[Content Safety Prompt Shields]
    FEAT[RMS energy and spaCy]
  end

  UI --> FD --> APIM --> API
  API --> CHAT
  API --> PG
  API --> BLOB
  API --> SB
  API --> CACHE
  CHAT --> CS
  CHAT --> LLM
  SB --> WK
  JOB --> PG
  WK --> LG
  LG --> STT
  LG --> CS
  LG --> LLM
  LG --> FEAT
  WK --> PG
  WK --> BLOB
  API --> OBS
  WK --> OBS
```

Longer write-up: [docs/architecture.md](docs/architecture.md).

### Analysis pipeline

LangGraph order: measure duration, transcribe, content safety, then either one structured summary or a map-reduce over chunks, then the selected Layer 2 options, then schema validation, then persist. A blocked transcript is stored and the chat model is not called.

![Analysis pipeline](docs/diagrams/analysis-pipeline.png)

[SVG](docs/diagrams/analysis-pipeline.svg) · [source](docs/diagrams/analysis-pipeline.mmd)

```mermaid
flowchart TD
  startNode[Start] --> prepare[Measure duration]
  prepare --> transcribe[Transcribe audio]
  transcribe --> shield[Prompt Shields and content safety]
  shield -->|blocked| stopNode[Store blocked result]
  shield -->|allowed| split{Longer than chunk size}
  split -->|no| single[Single structured summary and taxonomy]
  split -->|yes| mapNode[Map each chunk]
  mapNode --> reduceNode[Reduce summaries and union topics]
  single --> layer2[Layer 2 whitelist options]
  reduceNode --> layer2
  layer2 --> validate[Validate JSON schema]
  validate --> persist[Store transcript and analysis]
  persist --> stopNode
```

### Multi-region topology

N is the length of the Terraform `regions` list. Each region is a full stack. Front Door is the only global entry. A user has one `home_region`. Audio and analysis rows are not copied across regions.

![Scaling topology](docs/diagrams/scaling-topology.png)

[SVG](docs/diagrams/scaling-topology.svg) · [source](docs/diagrams/scaling-topology.mmd)

```mermaid
flowchart TB
  users[Clients] --> fd[Azure Front Door]
  fd --> r1[Region 1]
  fd --> rn[Region N]

  subgraph regionBox [Each region]
    apim[API Management Basic per-user limit]
    api[API replicas min 2 max 8]
    chat[Chat agent]
    queues[Queues transcription llm layer2 rollup]
    workers[Service Bus workers]
    rollup[Cron rollup job]
    db[(Postgres zone-redundant HA)]
    blob[(Blob GZRS under user id)]
    cache[(Redis Standard rate limit)]
    oai[Azure OpenAI and Content Safety]
    obs[Log Analytics and Langfuse]
  end

  r1 --> apim
  rn --> apim
  apim --> api
  api --> chat
  api --> cache
  api --> queues
  queues --> workers
  workers --> db
  workers --> blob
  workers --> oai
  rollup --> db
  api --> obs
  workers --> obs
```

Capacity math, cost notes, trade-offs, and future work: [docs/scaling.md](docs/scaling.md).

Planning case for one region (10,000 registered users, 2,000 concurrent, one 3 minute file per user per day, 80% of uploads in a 4 hour peak):

```text
peak file rate = 10000 * 0.80 / (4 * 3600) ≈ 0.56 files/s
in-flight pipeline ≈ 0.56 * 20 s ≈ 11 tasks
interactive request rate ≈ 187 requests/s
```

Multiply the regional lines by N. Front Door stays one profile.

### Data model

`users` is unpartitioned so email can be unique. Every other tenant table is `PARTITION BY HASH (user_id)` with modulus 16. Primary keys include `user_id`.

![Data model](docs/diagrams/data-model.png)

[SVG](docs/diagrams/data-model.svg) · [source](docs/diagrams/data-model.mmd)

```mermaid
erDiagram
  USERS ||--o{ AUDIO_FILES : owns
  USERS ||--o| PROMPT_CONFIGS : sets
  USERS ||--o{ TRANSCRIPTS : owns
  USERS ||--o{ ANALYSES : owns
  USERS ||--o{ ROLLUP_SUMMARIES : owns
  AUDIO_FILES ||--o| TRANSCRIPTS : has
  AUDIO_FILES ||--o| ANALYSES : has

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
```

Blob keys, in container `voice`:

```text
users/{user_id}/audio/{file_id}.{ext}
users/{user_id}/transcripts/{file_id}.json
users/{user_id}/analysis/{file_id}.json
```

Details: [docs/data-model.md](docs/data-model.md). SQL: [backend/sql/001_schema.sql](backend/sql/001_schema.sql).

## Run locally (mock mode, no API keys)

Requirements: Docker with Compose, and about 2 GB of image space (the API image includes spaCy `en_core_web_sm`).

```bash
docker compose up --build
```

Then open http://localhost:8501. The caption says mock mode is on. Create an account (password at least 8 characters), save the Layer 2 options, and use **Upload bundled sample**. The worker analyzes the file. The library shows duration, summary, taxonomy, and Layer 2. **Rollup** builds a collective summary.

The sidebar switches between Classic and Assistant. Classic is the pages above and stays within the phone-width layout. Assistant is wider: a flowchart of the selected file stays at the top, the chat scrolls underneath, and a live log sits in a right-hand column (or in the sidebar on a narrow window). Attach one to ten files (or the bundled sample), pick Layer 2 options, and wait. Completed steps turn green as the worker updates `stage`. Each result opens as its own panel (transcript, summary, topics, Layer 2) with a player for the recording. Tool calls show up as chips named `search_files`, `get_analysis`, or `run_summary`. Export conversation downloads one HTML file of that chat. Then ask a follow-up such as "what upcoming events did I mention this week?" or "summarize my files by topic". That calls `POST /api/v1/chat`. In mock mode the reply is rule-based and still runs the tools. Chat history stays in the browser session. Compose sets `MOCK_STAGE_DELAY_SEC=1.5` so the flowchart and log move during a demo.

Services:

| Service | Port | Role |
| --- | --- | --- |
| web | 8501 | Streamlit |
| api | 8000 | FastAPI |
| postgres | 5432 | Postgres 16, user `voice`, database `voice` |
| redis | 6379 | Celery broker |
| worker | none | Celery worker and beat |
| azurite | 10000 | Blob emulator |

Compose sets `LLM_PROVIDER=mock`, `SAFETY_PROVIDER=mock`, `BLOB_PROVIDER=azurite`, and `ANALYSIS_MODE=celery`. The bundled sample is `samples/sample_call.wav` (4.0 seconds, 16 kHz, 16-bit mono). Regenerate it with:

```bash
python3 scripts/generate_sample_audio.py
```

The mock transcriber returns a fixed transcript for that file (or for any upload named `sample_call.wav`). Duration and RMS are measured from the WAV itself. Other uploads get a deterministic transcript derived from the file hash, so taxonomy is still populated. The UI must stay in mock mode for that behavior. Azure mode sends the bytes to `gpt-4o-transcribe`.

A screen-recording walkthrough is in [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

### Tests and lint

```bash
python3 -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
ruff check backend frontend
ruff format --check backend frontend
cd backend && pytest
```

`make test` and `make lint` do the same. Tests use SQLite in memory, mock providers, and `ANALYSIS_MODE=inline`. They do not need Docker.

The partition integration test runs only when `POSTGRES_TEST_URL` is set, for example `postgresql://voice:voice@localhost:5432/voice_test`. It applies `001_schema.sql`, inserts rows for many users, and checks that they land in more than one hash partition. GitHub Actions sets this variable against a Postgres 16 service. The workflow also runs `terraform fmt -check`, `terraform init -backend=false`, and `terraform validate`.

## Azure

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # set db_admin_password and jwt_secret
terraform init
terraform plan
```

`regions` defaults to eastus and westeurope. Add or remove entries to change N. Each entry builds a `region_stack` module: resource group, VNet, Postgres Flexible Server (zone-redundant HA and geo-redundant backups), GZRS blob storage, Redis Standard, Key Vault, Service Bus Standard (four queues), Log Analytics, Application Insights, Azure OpenAI (`gpt-4o-transcribe` and `gpt-4.1-mini`), Content Safety, a zone-redundant Container Apps environment (API and worker), a cron rollup job, and API Management Basic. A global resource group holds Azure Front Door. Each origin is that region's API Management gateway, which forwards to the Container App. The API ingress allowlists API Management's public IPs.

Validate without credentials:

```bash
cd infra/terraform
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

Notes for an apply, image names, and required variables: [infra/terraform/README.md](infra/terraform/README.md).

Production process settings (not the compose defaults):

```text
LLM_PROVIDER=azure
SAFETY_PROVIDER=azure
BLOB_PROVIDER=azure
BROKER=servicebus
ANALYSIS_MODE=celery
```

The API publishes analysis work to the `transcription` queue. The worker command is `python -m app.jobs.service_bus_worker`. The schedule is a Container Apps Job, cron `*/15 * * * *`, command `python -m app.jobs.run_scheduled_rollup`. On-demand rollup still runs inside the API so the UI can return the result in the same request. Both paths call `run_rollup`.

## Environment variables

See [.env.example](.env.example). Compose already exports the mock-mode set.

| Variable | Local default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | compose Postgres URL | SQLAlchemy URL |
| `JWT_SECRET` | dev-only value in compose | HMAC secret, use a long random value in Azure |
| `JWT_EXPIRE_MINUTES` | 1440 | Access token lifetime |
| `LLM_PROVIDER` | `mock` | `mock` or `azure` |
| `SAFETY_PROVIDER` | `mock` | `mock` or `azure` |
| `BLOB_PROVIDER` | `azurite` in compose, `memory` in tests | `memory`, `azurite`, or `azure` |
| `ANALYSIS_MODE` | `celery` in compose, `inline` in tests | `inline` runs the pipeline in the API |
| `BROKER` | `celery` | `celery` or `servicebus` |
| `AZURE_STORAGE_CONNECTION_STRING` | Azurite devstore string | Blob account |
| `AZURE_STORAGE_CONTAINER` | `voice` | Private container name |
| `AZURE_OPENAI_ENDPOINT` | empty | Required when `LLM_PROVIDER=azure` |
| `AZURE_OPENAI_API_KEY` | empty | Required when `LLM_PROVIDER=azure` |
| `AZURE_OPENAI_API_VERSION` | `2025-03-01-preview` | Chat and transcription API version |
| `AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT` | `gpt-4o-transcribe` | Speech deployment name |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | `gpt-4.1-mini` | Structured-output deployment name |
| `AZURE_CONTENT_SAFETY_ENDPOINT` | empty | Required when `SAFETY_PROVIDER=azure` |
| `AZURE_CONTENT_SAFETY_KEY` | empty | Required when `SAFETY_PROVIDER=azure` |
| `CONTENT_SAFETY_BLOCK_SEVERITY` | 4 | Block at this Azure severity or higher |
| `SERVICE_BUS_CONNECTION_STRING` | empty | Required when `BROKER=servicebus` |
| `CELERY_BROKER_URL` | Redis db 0 | Local broker |
| `CELERY_RESULT_BACKEND` | Redis db 1 | Local result backend (results are ignored) |
| `REDIS_URL` | Redis db 2 in compose | Rate-limit counter. Falls back to `CELERY_BROKER_URL` when empty |
| `RATE_LIMIT_ENABLED` | true | Per-user limit on authenticated routes |
| `RATE_LIMIT_REQUESTS` | 120 | Allowed requests per window |
| `RATE_LIMIT_WINDOW_SECONDS` | 60 | Window length. `429` responses include `Retry-After` |
| `RATE_LIMIT_BACKEND` | `redis` in compose and Azure, `memory` in `.env.example` | `memory` is one process only |
| `ROLLUP_SCHEDULE_SECONDS` | 900 | Celery beat interval; the Azure job uses a 15 minute cron |
| `MOCK_STAGE_DELAY_SEC` | `1.5` in compose, `0` otherwise | Pause before each analysis stage so the assistant flowchart can move |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | empty | Both required or Langfuse stays off |
| `LANGFUSE_HOST` | cloud host | Langfuse base URL |
| `OTEL_ENABLED` | false | Turn on OpenTelemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | OTLP HTTP endpoint |
| `OTEL_SERVICE_NAME` | `voice-analytics-api` | Service name on spans |
| `CHUNK_CHARS` | 4000 | Map-reduce threshold |
| `MAX_CHUNKS` | 20 | Cap on mapped chunks |
| `MAX_UPLOAD_BYTES` | 20971520 | Upload limit |
| `HOME_REGION` | `local` | Stored on the user at sign-up |
| `API_BASE_URL` | `http://api:8000` inside compose | Streamlit target |
| `POSTGRES_TEST_URL` | unset | Enables the live partition test |

## API

Base path `/api/v1`. Authenticated routes expect `Authorization: Bearer <token>`. Full request and response notes: [docs/api.md](docs/api.md).

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/auth/signup` | no | Create account, return a token |
| POST | `/auth/login` | no | Return a token |
| GET | `/auth/me` | yes | Current user |
| POST | `/files` | yes | Multipart field `files`, up to 10 |
| GET | `/files` | yes | List with filters |
| GET | `/files/{id}` | yes | Detail plus transcript |
| GET | `/files/{id}/audio` | yes | Audio bytes |
| GET | `/events` | yes | Pipeline log for the caller, newest first |
| POST | `/files/{id}/analyze` | yes | Re-queue analysis |
| DELETE | `/files/{id}` | yes | Delete blobs and the row |
| GET | `/prompts/options` | no | Layer 2 catalog |
| GET | `/prompts/config` | yes | Saved selections |
| PUT | `/prompts/config` | yes | Replace selections |
| POST | `/summaries` | yes | On-demand rollup |
| GET | `/summaries` | yes | Recent rollups |
| POST | `/chat` | yes | Follow-up question for the chat agent |
| GET | `/health` | no | Liveness |
| GET | `/health/ready` | no | Database check |
| GET | `/meta` | no | Active providers |

List filters: `date_from`, `date_to`, `min_duration`, `max_duration`, `taxonomy`, `custom` (`name:value`), `limit`, `offset`.

## Guardrails

Written design: [docs/guardrails.md](docs/guardrails.md).

1. Users pick catalog options and declared parameters only. There is no free-form system prompt.
2. Saved selections and custom filter strings pass content safety (Azure Prompt Shields plus category analysis, or the mock equivalent) before they are stored.
3. System prompts are constants. The transcript is placed only in the user message, inside `<transcript>` markers, and is treated as data.
4. The transcript is shielded before any summary call. A block stores the transcript and skips the model.
5. Azure chat calls use a strict JSON schema. Pydantic checks the payload again before it is stored. Topics found on any map chunk are unioned back after reduce.
6. Assistant chat input, including history, goes through the same content-safety check. The agent may call only `search_files`, `get_analysis`, and `run_summary`. Arguments are validated, queries filter on the JWT `user_id`, and the reply is a structured string. Tool results are data. The raw transcript is not sent to the agent.

## Cost, trade-offs, future work

Transcription minutes dominate cost at the planning case (about 900,000 minutes per region per month if every registered user uploads one 3 minute file a day). `gpt-4.1-mini`, Content Safety, Postgres, and Container Apps are smaller lines. Multiply by N. Figures and the assumptions behind them are in [docs/scaling.md](docs/scaling.md). Recheck the Azure price sheet before using them as a budget.

Trade-offs in this POC:

- Hosted Azure OpenAI, not a self-hosted transcriber. The local demo needs no GPU, and 0.56 files/s does not justify a transcription cluster.
- Hash modulus is fixed at 16. Raising it later means a new table and a copy.
- `users` is not hash-partitioned, so email stays globally unique inside a region.
- Four Service Bus queues exist. Any analysis message currently runs the full idempotent pipeline. Splitting stages is the next scale step, not a new message contract.
- On-demand rollup runs in the API process. The schedule is Celery beat locally and a Container Apps Job in Azure.
- Celery beat is embedded in the single local worker. Do not run more than one beat process. Production uses the cron job instead.
- API Management is Basic, not Consumption. `rate-limit-by-key` is not available on Consumption, and that is the policy that keys a limit on the JWT `sub` claim. Basic is about $150 per region per month and covers the planning rate. Premium would add zones and a virtual network, and is not used. See [docs/scaling.md](docs/scaling.md).
- Postgres is zone-redundant with geo-redundant backups. Blob storage is GZRS. The Container Apps environment is zone redundant. Service Bus, Redis, and API Management stay off Premium. A user's rows still live only in the home region.

Later: stage-split workers, ffmpeg so compressed audio gets real duration and RMS, a global email directory in front of regional sign-up, private endpoints, a live cross-region copy of each user's data, Premium for Service Bus, Redis, and API Management if zone redundancy on those services is required, a quality evaluation set, and a retention policy for blocked transcripts.

## Deviations from the agreed stack

- The UI is Streamlit with a phone-width layout (max 440 px), talking to FastAPI over HTTP. That is the agreed interface. A native mobile client is not in this repository.
- The `users` table is not hash-partitioned. PostgreSQL unique constraints on a partitioned table must include the partition key, and email must stay unique without being part of `user_id`. All tenant tables (`audio_files`, `transcripts`, `analyses`, `prompt_configs`, `rollup_summaries`, `file_events`) are `PARTITION BY HASH (user_id)`.
- Duration and RMS are computed for 16-bit PCM WAV only. Other accepted types are stored and transcribed. Their Layer 2 energy step returns `skipped` with reason `wav_pcm16_required`. There is no ffmpeg in the image.
- On-demand rollup is synchronous in the API, using the same `run_rollup` function as the scheduler, so the screen can show the result immediately.
- Analysis queue messages all invoke the full pipeline. `llm-layer1` and `llm-layer2` are provisioned and routed by the dispatcher, and documented as the split point.
- Mock taxonomy is keyword and sentence heuristics. It is deterministic so the demo and tests do not need API keys. It is not a substitute for `gpt-4.1-mini` quality.
- The sample is a 4 second tone, not speech. In mock mode the transcript is a fixed script, so words per minute on that file is high (about 79 words in 4 seconds). Duration (4.0) and RMS are measured from the samples. Say this in the demo.
- `home_region` is stored. A global email directory across regions is described in the scaling doc and is not implemented.
- Schema changes are an idempotent SQL script applied at startup. There is no migration tool.
- Assistant chat history is kept in the Streamlit session and the last eight turns are sent with the question. It is not stored in Postgres.
- The mock chat agent chooses one tool with keyword rules. Azure mode asks `gpt-4.1-mini` for a tool call, then for a JSON reply. Both paths use the same LangGraph and the same three tools.
- The Container App is not on a private link. API Management Basic cannot join a virtual network, and Front Door Standard cannot private-link to it. The ingress allowlist plus the `X-Azure-FDID` check is the lock that is feasible on this tier.

## Demo video

Follow [DEMO_SCRIPT.md](DEMO_SCRIPT.md). Keep the mock-mode caption in frame, upload the bundled sample, show filters and a rollup, and mention that Azure mode is the same code path with `LLM_PROVIDER=azure`.
