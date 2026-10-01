# Architecture

## Names

| Short form | Meaning |
| --- | --- |
| API | application programming interface |
| HTTP | Hypertext Transfer Protocol |
| JSON | JavaScript Object Notation |
| JWT | JSON Web Token |
| IP | Internet Protocol address |
| URL | Uniform Resource Locator |
| UI | user interface |
| SQL | Structured Query Language |
| NoSQL | a store that is not queried with Structured Query Language |
| DB | database |
| MCP | Model Context Protocol |

## System

This is the multi-region Azure design, kept in the repo with its Terraform. It is not a running deployment; the prototype runs on one machine with `docker compose up` (see [This machine](#this-machine)). In the design, a signed-in request enters through Front Door. The API answers a question, queues an upload, or writes the grouped summaries. Only the API sits behind Front Door. The Streamlit screen runs on the local machine and is not part of the Terraform. The API, the workers, and the summary job get the Application Insights connection string. The API sends its traces there. The worker and job entry points do not start the exporter yet. Front Door does not route by `home_region`, so a call can land in a region that has no row for the account, and that region answers 401.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "monospace", "fontSize": "14px", "primaryColor": "#ffffff", "primaryTextColor": "#0f172a", "primaryBorderColor": "#0f172a", "lineColor": "#0f172a", "background": "#ffffff"}, "flowchart": {"curve": "linear", "padding": 16, "nodeSpacing": 20, "rankSpacing": 40, "htmlLabels": false, "wrappingWidth": 210, "useMaxWidth": false}}}%%
flowchart LR
  subgraph interfaceLayer ["Interface"]
    direction TB
    UI["Streamlit screen
Runs locally. Not hosted in this design"]
  end
  subgraph implementationLayer ["Implementation"]
    direction TB
    FD["Azure Front Door
Sends each call to any healthy region"]
    APIM["API Management
Checks the token and the account limit"]
    API["FastAPI
Checks the token and the request count"]
    SB["Service Bus queues
Holds the analysis job"]
    WK["Container Apps workers
Runs the queued file"]
    JOB["Scheduled summary job
Every 15 minutes"]
    PG["PostgreSQL
Rows, plus a standby in another zone"]
    BLOB["Blob storage
Audio copied across zones and to the paired region"]
    CACHE["Redis
The account request count"]
    OBS["Application Insights
Receives the API traces"]
    FD --> APIM --> API
    API --> SB --> WK
    API --> CACHE
    API --> PG
    API --> BLOB
    API --> OBS
    WK --> PG
    WK --> BLOB
    WK -.->|"connection string, no exporter yet"| OBS
    JOB --> PG
    JOB -.->|"connection string, no exporter yet"| OBS
  end
  subgraph intelligenceLayer ["Intelligence"]
    direction TB
    CHAT["Microsoft Agent Framework chat
Answers one question"]
    LG["Microsoft Agent Framework analysis
Words, then the summary, then the Analytics prompts"]
    STT["gpt-4o-transcribe
Turns the audio into words"]
    LLM["gpt-5-mini
Writes the summary and the reply"]
    CS["Content Safety
Blocks unsafe text"]
    FEAT["Analytics
Predefined AI prompts. Loudness and pace numbers from measuring tools"]
    CHAT --> LLM
    CHAT --> CS
    LG --> STT
    LG --> LLM
    LG --> CS
    LG --> FEAT
  end
  UI -->|"API calls"| FD
  API --> CHAT
  WK --> LG
  JOB --> LLM
```

## This machine

What `docker compose up` starts.

```mermaid
%%{init: {"theme": "base", "htmlLabels": false, "themeVariables": {"fontFamily": "monospace", "fontSize": "15px", "primaryColor": "#ffffff", "primaryTextColor": "#0f172a", "primaryBorderColor": "#0f172a", "lineColor": "#0f172a", "background": "#ffffff"}, "flowchart": {"curve": "linear", "padding": 18, "nodeSpacing": 28, "rankSpacing": 72, "wrappingWidth": 480, "useMaxWidth": false}}}%%
flowchart LR
  Start --> Compose[docker compose up]
  Compose --> database@{ shape: cyl, label: "Database
(A)
[sql:postgres]
Holds the Voice database
and the Langfuse database." }
  database --> voiceDb@{ shape: cyl, label: "Voice
(A.1)
[main db]
The main database for the app." }
  voiceDb --> tblUsers@{ shape: bow-rect, label: "Users
(A.1.1)
[table:users]
Stores the account email,
hashed credentials, and home region." }
  voiceDb --> tblAudio@{ shape: bow-rect, label: "Audio Files
(A.1.2)
[table:audio_files]
Stores each uploaded recording,
its length, and processing status." }
  voiceDb --> tblTranscripts@{ shape: bow-rect, label: "Transcripts
(A.1.3)
[table:transcripts]
Stores the AI-processed transcriptions." }
  voiceDb --> tblAnalyses@{ shape: bow-rect, label: "Analyses
(A.1.4)
[table:analyses]
Stores the AI-extracted insights
and analytics for one recording." }
  voiceDb --> tblPrompts@{ shape: bow-rect, label: "Prompt Configs
(A.1.5)
[table:prompt_configs]
Stores which analytics
this account turned on." }
  voiceDb --> tblSummaries@{ shape: bow-rect, label: "Rollup Summaries
(A.1.6)
[table:rollup_summaries]
Stores the analysis aggregation
of those AI summaries, for all of
the account's completed recordings,
or grouped by topic, day, week,
month, or sentiment." }
  voiceDb --> tblReports@{ shape: bow-rect, label: "Group Reports
(A.1.7)
[table:group_reports]
Stores the All groupings report:
every grouping, the numbers per group,
and an AI summary per group." }
  voiceDb --> tblEvents@{ shape: bow-rect, label: "File Events
(A.1.8)
[table:file_events]
Stores the file processing logs." }
  voiceDb --> tblChatSessions@{ shape: bow-rect, label: "Chat Sessions
(A.1.9)
[table:chat_sessions]
Stores one chat for this account." }
  voiceDb --> tblChatMessages@{ shape: bow-rect, label: "Chat Messages
(A.1.10)
[table:chat_messages]
Stores the questions and answers
in that chat." }
  database --> lfDb@{ shape: cyl, label: "langfuse
(A.2)
[db:langfuse]
Stores Langfuse's projects,
users, and settings." }
  Compose --> queue["Redis
(B)
[nosql:redis]
Job queue, rate limit,
and Langfuse."]
  Compose --> storage["Storage
(C)
[blob:azurite]"]
  Compose --> mcp["MCP Server
(D)
[mcp.server.mcpserver]"]
  Compose --> api["Audio Analytics
(E)
[api:fastapi]"]
  Compose --> processor["Processor
(F)
[worker:celery]"]
  Compose --> ui["UI
(G)
[web:streamlit]"]
  Compose --> langfuse["Langfuse
(H)
[tracing:langfuse]"]
  langfuse --> lfWeb["Web
(H.1)
[langfuse:web]"]
  langfuse --> lfWorker["Worker
(H.2)
[langfuse:worker]"]
  langfuse --> lfClick["ClickHouse
(H.3)
[clickhouse]"]
  langfuse --> lfMinio["MinIO
(H.4)
[minio]"]
  langfuse --> lfInit["DB Init
(H.5)
[langfuse:db-init]"]
```

## One recording (MAF analysis workflow)

One saved file is measured, transcribed, checked, summarized, then run through the ticked Analytics prompts on gpt-5-mini. Loudness and pace numbers come from measuring tools.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "monospace", "fontSize": "14px", "primaryColor": "#ffffff", "primaryTextColor": "#0f172a", "primaryBorderColor": "#0f172a", "lineColor": "#0f172a", "background": "#ffffff"}, "flowchart": {"curve": "linear", "padding": 16, "nodeSpacing": 28, "rankSpacing": 44, "htmlLabels": false, "wrappingWidth": 260, "useMaxWidth": false}}}%%
flowchart TD
  START["Upload saved"] --> prepare["prepare
measure_duration()"]
  prepare --> transcribe["transcribe
fetch_audio_via_mcp() when that address is set"]
  transcribe --> speech["transcribe()
gpt-4o-transcribe turns the audio into words"]
  speech --> shield["shield
Content Safety"]
  shield -->|blocked| stopped["Stopped
The words are kept"]
  shield -->|allowed| insights["Summary
One recording"]
  insights -->|short| shorty["summarize_and_classify()
gpt-5-mini"]
  insights -->|long| longy["summarize_chunk(), then reduce_summaries()
gpt-5-mini"]
  shorty --> analytics["Analytics
run_predefined_prompts(), gpt-5-mini
Tools measure_rms() and measure_speaking_pace() give the loudness and pace numbers"]
  longy --> analytics
  analytics --> validate["validate
Schema check of Insights and Analytics"]
  validate --> saved["Saved"]
```

## One question (MAF chat workflow)

| | Azure | Mock |
| --- | --- | --- |
| Plan | Fixed rules first for memory, greetings, capabilities, voice questions, a named file id, and "what it says". Otherwise `gpt-5-mini` picks the tool. Rules again if that call fails or leaves a clear summary request without a tool | Fixed rules |
| Reply | `gpt-5-mini` writes every reply, including greetings, help, and tool errors. Fixed text only when that call fails | Fixed rules |
| Voice questions | Gender, age, accent, emotion, or who is speaking call `profile_speaker` on the recording named in the question, otherwise the newest. The audio comes through the MCP `fetch_audio` tool (a direct blob read when `MCP_AUDIO_URL` is unset or the call fails) | Same |
| Voice traits | Need an audio-input deployment. `gpt-5-mini` takes text and images, and `gpt-4o-transcribe` only transcribes, so with the current deployments the acoustic fallback answers: duration and loudness only. Known limit: for an `.mp3` upload, `azure.py` labels the audio `mp3` from the filename even after ffmpeg has turned it into WAV | Acoustic fallback |
| Temperature | Empty unless `AZURE_OPENAI_CHAT_TEMPERATURE` is set | |

The steps that answer one question.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "monospace", "fontSize": "14px", "primaryColor": "#ffffff", "primaryTextColor": "#0f172a", "primaryBorderColor": "#0f172a", "lineColor": "#0f172a", "background": "#ffffff"}, "flowchart": {"curve": "linear", "padding": 16, "nodeSpacing": 32, "rankSpacing": 48, "htmlLabels": false, "wrappingWidth": 240, "useMaxWidth": false}}}%%
flowchart TD
  START["Question"] --> plan["plan
Chooses one tool, or none"]
  plan -->|search_files| search["search_files
No model"]
  plan -->|get_analysis| opened["get_analysis
No model"]
  plan -->|"run_summary(group_by)"| summary["summarize_group(summaries)
One summary for the group"]
  plan -->|profile_speaker| profile["profile_speaker
Acoustic fallback with the current deployments"]
  plan -->|none| compose["compose
Writes every reply"]
  search --> compose
  opened --> compose
  summary --> compose
  profile --> compose
  compose -->|"gpt-5-mini. Fixed text if that call fails"| END["Reply"]
```

## Call path

Which file calls the next.

```mermaid
%%{init: {"theme": "base", "htmlLabels": true, "securityLevel": "antiscript", "themeVariables": {"fontFamily": "monospace", "fontSize": "15px", "primaryColor": "#ffffff", "primaryTextColor": "#0f172a", "primaryBorderColor": "#0f172a", "lineColor": "#0f172a", "background": "#ffffff"}, "flowchart": {"htmlLabels": true, "curve": "linear", "padding": 18, "nodeSpacing": 28, "rankSpacing": 72, "wrappingWidth": 480, "useMaxWidth": false}}}%%
flowchart LR
  streamlit["<span style='font-weight:normal'>streamlit_app.py</span><br><span style='font-size:11px;color:rgb(148,163,184)'>frontend/streamlit_app.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Signs in, then opens the assistant.</span></i>"] -->|"render_assistant()"| assistant["<span style='font-weight:normal'>assistant_ui.py</span> | <b>render_assistant()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>frontend/assistant_ui.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Sends what you picked.</span></i>"]
  assistant --> client["<span style='font-weight:normal'>api_client.py</span> | <b>upload()</b>, <b>chat()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>frontend/api_client.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Sends the request.</span></i>"]
  client -->|"upload()"| files["<span style='font-weight:normal'>files.py</span> | <b>upload_files()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/api/routes/files.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Saves the recording.</span></i>"]
  client -->|"chat()"| chat
  files -->|"enqueue_analysis()"| service["<span style='font-weight:normal'>analysis/service.py</span> | <b>enqueue_analysis()</b>, <b>run_file_analysis()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/service.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Queues the job, then runs the analysis.</span></i>"]
  service -->|"analyze_file_task.apply_async()"| tasks
  service --> graphPy["<span style='font-weight:normal'>analysis/graph.py</span> | <b>run_graph()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/graph.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Microsoft Agent Framework works through one recording.<br>First the words, then the summary, then the Analytics prompts.</span></i>"]
  tasks["<span style='font-weight:normal'>jobs/tasks.py</span> | <b>analyze_file_task()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/jobs/tasks.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Runs the queued job.</span></i>"] --> service
  chat["<span style='font-weight:normal'>chat.py</span> | <b>chat()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/api/routes/chat.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Answers the question.</span></i>"] --> agent["<span style='font-weight:normal'>chat/agent.py</span> | <b>run_chat_agent()</b>, <b>execute_tool()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/chat/agent.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Microsoft Agent Framework answers one question<br>about this account's recordings.</span></i>"]
  agent --> tools["<span style='font-weight:normal'>chat/tools.py</span> | <b>run_summary(group_by)</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/chat/tools.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Asks for a summary.</span></i>"]
  tools --> summary["<span style='font-weight:normal'>summarize_group.py</span> | <b>summarize_group(summaries)</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/summarize_group.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Writes one summary for the group.</span></i>"]
  graphPy --> mcp["<span style='font-weight:normal'>mcp_audio.py</span> | <b>fetch_audio_via_mcp()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/mcp_audio.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Reads the audio.</span></i>"]
  graphPy --> intelligence["<span style='font-weight:normal'>intelligence.py</span> | <b>get_intelligence()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/providers/intelligence.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Azure OpenAI, or the mock provider.<br>Transcribes the audio and writes the insights.</span></i>"]
  graphPy --> layer2["<span style='display:inline-block;width:19ch;text-align:right;font-weight:400'>llm_options.py</span> | <b>run_predefined_prompts()</b><br><span style='display:inline-block;width:19ch;text-align:right;font-weight:400'></span> | gpt-5-mini<br><span style='display:inline-block;width:19ch;text-align:right;font-weight:400'>SignalTools</span> | <b>measure_rms()</b><br><span style='display:inline-block;width:19ch;text-align:right;font-weight:400'></span> | <b>measure_speaking_pace()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/llm_options.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Analytics.<br>Predefined AI prompts; loudness and pace numbers<br>come from measuring tools.</span></i>"]
```

## Local and cloud

| | Local | Cloud |
| --- | --- | --- |
| Interface | Streamlit (`web`, port 8501) | Not in the Terraform design. No frontend host |
| API | FastAPI on Uvicorn (`api`, port 8000) | Container App `ca-api`, 2 copies growing to 8 |
| Queue | Redis and Celery | Service Bus |
| Schedule | Celery beat | Every 15 minutes |
| Files | Azurite, then `fetch_audio` when `MCP_AUDIO_URL` is set | Blob. The bytes are already loaded |
| Database | PostgreSQL 16 | One server per region, plus a standby in another zone |
| Models | Azure OpenAI and Content Safety when `LLM_PROVIDER` and `SAFETY_PROVIDER` are `azure`. Mock by default (Compose and `.env.example`), with no keys | Azure OpenAI and Content Safety |
| Front door | none locally | Front Door, then API Management. Any healthy region. No routing by `home_region`; the other region answers 401 |
| Work | Inline in tests. Celery in Compose | 1 to 20 worker copies. A Service Bus rule adds one copy per waiting `transcription` message |
| Account limit | 120 a minute when the limiter is on | 120 a minute |
| Trace | Langfuse when both keys are set. A failed send leaves the file result in place | Not configured. Terraform sets no Langfuse keys |
| Telemetry | Application Insights only when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set (the worker in Compose) | API, workers, and job get the connection string. Only the API starts the exporter |

[Scaling](scaling.md)
