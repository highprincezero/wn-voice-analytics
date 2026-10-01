# Audio Analytics

```mermaid
flowchart LR
  signup[Sign up] --> upload[Upload]
  upload --> result[Words summary topics Analytics]
  result --> chat[Chat]
  result --> summaries[Summaries]
```

| | |
| --- | --- |
| `docker compose up` | No cloud keys. Providers are mock |
| Azure | `LLM_PROVIDER=azure` and `SAFETY_PROVIDER=azure` |

| | |
| --- | --- |
| Sign up | The password is stored only as a bcrypt hash. Log-in returns a signed JWT (HS256) whose subject is the account id; every later call sends it, and each route returns only that account's data |
| Upload | 10 files, 20 MB. wav, mp3, m4a, ogg, flac |
| Filter | Date, duration, topic, Analytics |
| Insights | Length, summary, professional topics, personal topics, upcoming events |
| Analytics | Predefined AI prompts: loudness and pace (via measuring tools), nouns and adjectives, sentiment, action items, tone, key entities |
| Summaries | All recordings, topic, day, week, month, sentiment |
| Export | Browse results: Download CSV or JSON of the rows shown, filters applied |
| Opening line | Fixed list. No model call |
| Chat reply | Azure: `gpt-5-mini` writes every reply. Fixed text only when that call fails, and in mock mode |

## Layout

```text
backend/            API, workflow, worker, schema, tests
frontend/           Streamlit
mcp/                Local MCP server: fetch_audio reads the Azurite blob for transcription
samples/            The demo WAV
scripts/            generate_sample_audio.py rebuilds the demo WAV
skills/             The speaker-profile skill (prompt mirrored in speaker_skill.py)
docs/               Short write-ups
infra/terraform/    Azure, one module per region
docker-compose.yml  API, Streamlit, Postgres, Redis, worker, Azurite, MCP, Langfuse
```

## How it fits together

```mermaid
flowchart LR
  screen[Streamlit] --> api[API]
  api --> store[Account file results]
  api --> worker[Worker]
```

The pictures are inside [Architecture](docs/architecture.md) and [Scaling](docs/scaling.md).

[Architecture](docs/architecture.md), [data model](docs/data-model.md), [API](docs/api.md), [guardrails](docs/guardrails.md), [scaling](docs/scaling.md).

| Step | Model |
| --- | --- |
| Words | `gpt-4o-transcribe` |
| Summary, topics, chat | `gpt-5-mini` |
| Analytics | `gpt-5-mini`, one call per file with the ticked predefined prompts. When loudness or pace is ticked, that call is two requests: the model asks for the server tools, then answers with their numbers |
| Blocked transcript | Stored. Later steps do not run |

Blob keys, container `voice`:

```text
users/{user_id}/audio/{file_id}.{ext}
users/{user_id}/transcripts/{file_id}.json
users/{user_id}/analysis/{file_id}.json
```

```mermaid
flowchart LR
  account[Account] --> usersTable[users one table]
  account --> parts[16 hash parts on user_id]
```

Schema: [backend/sql/001_schema.sql](backend/sql/001_schema.sql).

## Run locally

Needs Docker with Compose v2, and about 10 GB of disk: about 7.5 GB of images plus about 2 GB of build cache.

```bash
cp .env.example .env      # mock mode: no keys needed, leave the Azure lines empty
docker compose up --build
```

Check it is up: `curl http://localhost:8000/api/v1/health` returns `{"status":"ok"}` and `curl http://localhost:8000/api/v1/meta` shows `"llm_provider":"mock"`.

Open http://localhost:8501. The logged-out caption says mock mode is on. Create an account (password at least 8 characters). You land on Audio Analytics Agent.

Upload the bundled sample from the assistant. Processing starts on its own. Analytics sits in a small box under the result.

Pick which Analytics options run, and their settings (loudness window, top words shown, most items listed), in Analytics settings next to Summarize across files, or with `PUT /api/v1/prompts/config`. All seven run by default. Changes apply to the next analysis run: a new upload or Run again.

Show activity log starts off. Turn it on to see the live log, then the activity list below it, newest turn first. Off hides both. The choice stays through processing and reruns, and `?activity=1` or `?activity=0` in the address keeps it after a new log-in. The side panel stays put while the chat scrolls. The chat input stays pinned to the bottom. The opening screen offers two replies: Summarize my calls and Upload a recording.

| Service | Port | Role |
| --- | --- | --- |
| web | 8501 | Streamlit |
| api | 8000 | FastAPI. Health: http://localhost:8000/api/v1/health |
| postgres | 5432 | Database `voice`, and `langfuse` for traces. User `voice` |
| redis | 6379 | 0 is the job queue. 1 holds Celery results. 2 is the rate limit and Langfuse |
| worker | | Celery, including the schedule |
| azurite | 10000 | Local blob store, container `voice` |
| azurite-mcp | 8090 | `fetch_audio` for the transcription step |
| langfuse-web | 3000 | Traces, when both keys are set (see Langfuse below). Names are in [Glossary](docs/glossary.md) |
| langfuse-minio | 9090 | Langfuse event and media store |

Optional dev tools: Redis Insight (5540), Azurite UI (8080), Flower (5555), pgAdmin (5050), Dozzle (9999), and redis-watch (no port; `docker compose logs -f redis-watch` shows Celery queue activity). They live in an override file that Compose loads on its own once it has the expected name (that name is git-ignored):

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
docker compose up -d
```

pgAdmin opens without a login. Register a server with host `postgres`, port 5432, user `voice`, password `voice`.

```mermaid
flowchart LR
  upload[Upload] --> azurite[Azurite]
  azurite -->|MCP_AUDIO_URL set| mcp[fetch_audio]
  mcp --> transcribe[Transcribe]
```

Cloud leaves `MCP_AUDIO_URL` empty.

| Sample | |
| --- | --- |
| File | `samples/sample_call.wav` |
| That name | Fixed script |
| Other files | Stand-in from the file hash |
| Regenerate | `python3 scripts/generate_sample_audio.py` |

### Langfuse

Local Langfuse runs at http://localhost:3000. Log in with `admin@local.dev` / `admin-dev-password` (set `LANGFUSE_INIT_USER_EMAIL` and `LANGFUSE_INIT_USER_PASSWORD` in `.env` before the first start to change them). It creates the project `voice-analytics` with the keys `pk-lf-local-dev` / `sk-lf-local-dev`.

Tracing is off until both keys are set. To send traces to the local Langfuse, put these in `.env` and restart the worker (`docker compose up -d worker`):

```text
LANGFUSE_PUBLIC_KEY=pk-lf-local-dev
LANGFUSE_SECRET_KEY=sk-lf-local-dev
LANGFUSE_HOST=http://langfuse-web:3000
```

`LANGFUSE_HOST` defaults to `http://langfuse-web:3000` in Compose. Traces come from the worker: each model step of the analysis pipeline and the background reports is one generation. With `LLM_PROVIDER=azure` they show the real deployment and prompts. In mock mode they are labelled `mock` and hold the stand-in output. The API container gets no Langfuse keys, so chat turns are not traced.

### Tests

Local tests need Python 3.12, the same version CI and the api image use, and `ffmpeg` on the PATH for the compressed-audio tests (mp3, ogg, flac). Use a virtual environment (system Python on recent macOS and Linux refuses `pip install`):

```bash
python3.12 -m venv .venv && . .venv/bin/activate
python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
ruff check backend frontend
ruff format --check backend frontend
(cd backend && pytest)
```

Plain `pytest` gives 153 passed and 1 skipped. The skipped one is the Postgres partition check, which needs `POSTGRES_TEST_URL`. If your `ffmpeg` cannot encode ogg (some Homebrew builds), the compressed-audio duration and loudness test (mp3, ogg, flac in one test) is skipped as well: 152 passed and 2 skipped. To run it against the Compose Postgres (stack up), create a separate test database first (run from the repo root). That gives 154 passed:

```bash
docker compose exec postgres createdb -U voice voice_test
(cd backend && POSTGRES_TEST_URL=postgresql://voice:voice@localhost:5432/voice_test pytest)
```

**Never point `POSTGRES_TEST_URL` at the app database (`voice`). The test runs `DROP SCHEMA public CASCADE` and wipes every account, file and result in it.** CI uses `voice` only because its Postgres is a throwaway service container.

To run the same tests inside the api image (it has no pytest or ruff, so the dev requirements are installed into the throwaway container first; the cache plugin is off because the container user cannot write to the mounted repo):

```bash
docker compose run --rm --no-deps -T -v "$PWD:/repo" -w /repo/backend api \
  sh -c 'pip install -q --user -r requirements-dev.txt && python -m pytest -p no:cacheprovider'
```

## Scalability design

The multi-region setup is a design kept in the repo. It is not a running deployment, and the demo video does not cover it. The prototype runs on one machine with `docker compose up`.

| Topic | Where |
| --- | --- |
| Multi-region design: request path, home region per account, sizing for N regions × 10,000 users and 2,000 concurrent each | [docs/scaling.md](docs/scaling.md): [Request 001](docs/scaling.md#request-001), [Plan numbers, each region](docs/scaling.md#plan-numbers-each-region) |
| Block diagram of every service: gateway, auth, queues, workers, database, cache, storage, observability | [docs/architecture.md](docs/architecture.md#system) |
| Database schema and object storage keys | [docs/data-model.md](docs/data-model.md), [Blob keys](docs/data-model.md#blob-keys) |
| Infrastructure as code, one stack per region | [infra/terraform](infra/terraform) ([notes](infra/terraform/README.md)). Validated in CI (`fmt`, `init`, `validate`), not applied |
| Model choice and justification | [docs/scaling.md: Data plane per region](docs/scaling.md#data-plane-per-region), [Why Azure](docs/scaling.md#why-azure), [Trade-offs](docs/scaling.md#trade-offs) |

## Azure

This is a design plus Terraform. It is not a running multi-region deployment.

```bash
cd infra/terraform || exit 1
cp terraform.tfvars.example terraform.tfvars   # set db_admin_password and jwt_secret
terraform init
terraform plan
```

`regions` defaults to eastus and westeurope. Each region's Azure OpenAI account defaults to eastus2 and swedencentral (`openai_location`), because eastus and westeurope do not offer `gpt-4o-transcribe`. Each region is its own stack. Front Door is the shared front door. Check without credentials:

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

Notes: [infra/terraform/README.md](infra/terraform/README.md). The release picture and the planning numbers are in [Scaling](docs/scaling.md).

Production settings:

```text
LLM_PROVIDER=azure
SAFETY_PROVIDER=azure
BLOB_PROVIDER=azure
BROKER=servicebus
ANALYSIS_MODE=celery
```

| Process | Command |
| --- | --- |
| Worker | `python -m app.jobs.service_bus_worker` |
| Every 15 minutes | `python -m app.jobs.run_scheduled_rollup` |
| On demand | The API. Same `summarize_group` |

## Configuration

Copy [.env.example](.env.example) to `.env`. Mock mode needs no keys. The app reads `.env` from the folder it is started in. The comments in that file cover the rest. The ones people trip on:

| Variable | Meaning |
| --- | --- |
| `LLM_PROVIDER` / `SAFETY_PROVIDER` | `mock` or `azure` |
| `AZURE_OPENAI_CHAT_TEMPERATURE` | Leave empty. `gpt-5-mini` rejects 0 |
| `MCP_AUDIO_URL` | Set in Compose. Empty uses the bytes already loaded |
| `ANALYSIS_MODE` | `celery` in Compose, `inline` in tests |
| `BROKER` | `celery` or `servicebus` |
| `MOCK_STAGE_DELAY_SEC` | `1.5` in Compose so the stages are visible. `0` elsewhere |
| `CHUNK_CHARS` / `MAX_CHUNKS` | 4000 / 20. Longer transcripts are split, then merged |
| `RATE_LIMIT_REQUESTS` | 120 per minute per account |
| `JWT_ALGORITHM` | `HS256`. The API Management policy checks HS256 only, so keep it |
| `SUMMARY_WORKERS` | 6. Group summaries are written this many model calls at a time |
| `DISPLAY_TIMEZONE` | `Asia/Manila`. Upload times in chat replies, used to tell same-name recordings apart |
| `APPLICATIONINSIGHTS_CONNECTION_STRING`, `API_BASE_URL`, `POSTGRES_TEST_URL` | Read from the real environment only, never from `.env`. Outside Docker, export them |

## API

Base path `/api/v1`. Authenticated routes send `Authorization: Bearer <token>`. Request notes: [docs/api.md](docs/api.md).

Passwords are stored only as bcrypt hashes. Sign-up and log-in return a JWT signed with `JWT_SECRET` (HS256). Its subject is the account id and it expires after `JWT_EXPIRE_MINUTES` (1440). Every route reads the account from the token, so another account's file id answers 404.

Try it with curl against the local stack:

```bash
API=http://localhost:8000/api/v1

# Sign up (201). Password at least 8 characters, at most 72 bytes. A repeat email answers 409.
curl -s -X POST $API/auth/signup -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","password":"alicepass123"}'

# Log in (200) and keep the token.
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","password":"alicepass123"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

# Upload (201). Multipart field `files`, repeat it for up to 10 files.
FILE_ID=$(curl -s -X POST $API/files -H "Authorization: Bearer $TOKEN" \
  -F files=@samples/sample_call.wav \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["id"])')

# Poll until status is completed (a few seconds in mock mode).
curl -s "$API/files/$FILE_ID" -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["stage"])'

# Ask a question (200). Send the returned session_id with the next turn to keep one chat.
curl -s -X POST $API/chat -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"What action items came out of my recordings?"}'
```

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/auth/signup` | no | Create an account |
| POST | `/auth/login` | no | Return a token |
| GET | `/auth/me` | yes | This account |
| POST | `/files` | yes | Upload, up to 10 |
| GET | `/files` | yes | List, with filters |
| GET | `/files/{id}` | yes | One recording, plus the transcript |
| GET | `/files/{id}/audio` | yes | Audio bytes |
| GET | `/events` | yes | Processing log |
| POST | `/files/{id}/analyze` | yes | Run it again |
| DELETE | `/files/{id}` | yes | Delete it. In Azure, the old Blob version is purged after 7 days, counted from when it was written |
| GET | `/prompts/options` | no | Analytics catalog |
| GET | `/prompts/config` | yes | The saved options |
| PUT | `/prompts/config` | yes | Save the chosen options |
| POST | `/summaries` | yes | Group completed summaries |
| GET | `/summaries` | yes | Recent summaries |
| POST | `/reports` | yes | Start the All groupings report (background job) |
| GET | `/reports` | yes | Recent reports |
| GET | `/reports/{id}` | yes | One report and its status |
| POST | `/chat` | yes | One question |
| GET | `/chat/session` | yes | The latest chat and its last 40 saved messages |
| POST | `/chat/session` | yes | Start an empty chat |
| GET | `/health` | no | Liveness |
| GET | `/health/ready` | no | Database check |
| GET | `/meta` | no | Which providers are on |
| GET | `http://localhost:8000/` (outside `/api/v1`) | no | Service name |

## Offline Collective Analysis

Summarize across files offers one summary at a time (trend per day, week, or month, by topic, by sentiment) and the **All groupings report**, one background job that groups every completed file every way at once and stores the result. Files are grouped in code so groups are exact; the AI writes the summary for every group. The numbers per group (files, total and average length, average wpm, average loudness, sentiment and tone mix, action item count) are computed in code. Blocked files are never read and are counted as skipped. The single summaries also write an AI summary for every group, plus one overall.

| Brief asks for | Where it is |
| --- | --- |
| Time range: day, week, month | `day` `week` `month` groupings |
| Context summary | An AI summary for every group, from the fixed hardened rollup prompt (`gpt-5-mini`; mock in local mode and tests) over the files' Insights summaries |
| User: all files together | `user` grouping, one group named `all files` |
| Taxonomy label | `taxonomy_label`, one group per topic, personal topic, or upcoming event |
| Other Layer 2 groupings | `sentiment`, `tone`, `pace_band` (slow under 110 wpm, conversational 110 to 170, brisk over 170), `key_entity`, `action_items` |

## Guardrails

[docs/guardrails.md](docs/guardrails.md).

**Layer 2 design choice.** Every Analytics option is a predefined prompt: a fixed instruction block on the server, keyed by the option id, injected into one `gpt-5-mini` call per file (two requests when loudness or pace is ticked, for the tool round trip). The user only ticks options and sets typed parameters in the Analytics settings panel (the brief: "Users can select and configure the predefined prompt on the UI"); nobody types prompt text. For loudness and pace the model calls server tools (`measure_rms`, `measure_speaking_pace`) that compute exact numbers in code; the model adds a short interpretation. Nouns and adjectives, sentiment, action items, tone, and key entities come from the model, in a JSON schema built from only the ticked options. The code functions (RMS math, spaCy, a fixed word list) stay as the tools and as the mock-mode stand-in, so tests and the offline demo need no Azure.

**Audio guardrails.** Every transcript goes through a content-safety check (Azure AI Content Safety Prompt Shields; a fixed phrase list in local mock mode) that blocks before any summary, Analytics, or chat model call. In Azure mode only the first 10,000 characters of each text are sent to the check; the rest is not screened. Behind it, the system prompts name common injection patterns and say never to obey them, and transcripts, summaries, and tool results are fenced in fixed tags as untrusted data.

### Guardrails for Layer 2

- Filter parameters: the UI offers only fixed choices. The server does not trust the API and re-checks every request against the whitelist: unknown ids, out-of-range or wrongly typed params (for example `window_ms` 123), and extra fields are rejected with an error. Only fixed server-side prompt text reaches the LLM; user values are validated numbers.
- Audio content: the content-safety check screens every transcript before any summary, Analytics, or chat model call. In Azure mode it screens the first 10,000 characters only. Blocked transcripts never reach the LLM or chat. Hardened prompts treat the transcript as untrusted data inside escaped markers. Output is schema-validated, and loudness and pace numbers come from server tools only.

## Worth knowing

| | |
| --- | --- |
| Screen | Streamlit, including a phone. No separate mobile app |
| Loudness | WAV is read directly. mp3, m4a, ogg, and flac are decoded with ffmpeg first, so duration and RMS work for every upload type |
| Sample pace | The file is a short tone and the words are a fixed script |
| Chat history | Postgres, per account and chat. The agent sees the last eight turns |
| Schema change | A SQL script at startup |
| Account rows | One region. No automatic move |
| Cost | [docs/scaling.md](docs/scaling.md) |

## Demo

The demo video shows the running app end to end at http://localhost:8501: sign up, log in, upload, transcription, Insights, Analytics, browsing results, summaries across files, and chat. The scalability design is not in the video; see [Scalability design](#scalability-design).
