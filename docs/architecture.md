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

A signed-in request enters through Front Door. The API answers a question, queues an upload, or writes the grouped summaries.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontFamily": "monospace", "fontSize": "14px", "primaryColor": "#ffffff", "primaryTextColor": "#0f172a", "primaryBorderColor": "#0f172a", "lineColor": "#0f172a", "background": "#ffffff"}, "flowchart": {"curve": "linear", "padding": 16, "nodeSpacing": 20, "rankSpacing": 40, "htmlLabels": false, "wrappingWidth": 210, "useMaxWidth": false}}}%%
flowchart LR
  subgraph interfaceLayer ["Interface"]
    direction TB
    UI["Audio Analytics Agent
The signed-in screen"]
  end
  subgraph implementationLayer ["Implementation"]
    direction TB
    FD["Azure Front Door
Picks a healthy region"]
    APIM["API Management
Checks the token and the account limit"]
    API["FastAPI
Checks the token and the request count"]
    SB["Service Bus queues
Holds the upload"]
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
The trace of the request"]
    FD --> APIM --> API
    API --> SB --> WK
    API --> CACHE
    API --> PG
    API --> BLOB
    API --> OBS
    WK --> PG
    WK --> BLOB
    WK --> OBS
    JOB --> PG
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
  UI --> FD
  API --> CHAT
  WK --> LG
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
  voiceDb --> tblSummaries@{ shape: bow-rect, label: "Summaries
(A.1.6)
[summaries]
Stores the analysis aggregation
of those AI summaries, for all of
the account's completed recordings,
or grouped by topic, day, week,
month, or sentiment." }
  voiceDb --> tblEvents@{ shape: bow-rect, label: "File Events
(A.1.7)
[table:file_events]
Stores the file processing logs." }
  database --> lfDb@{ shape: cyl, label: "langfuse
(A.2)
[db:langfuse]
Stores Langfuse's projects,
users, and settings." }
  Compose --> queue["Job Queue
(B)
[nosql:redis]"]
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

## One recording

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
  analytics --> saved["Saved"]
```

## One question

| | Azure | Mock |
| --- | --- | --- |
| Plan | `gpt-5-mini` | Fixed rules |
| Reply | `gpt-5-mini` writes every reply, including greetings, help, and tool errors. Fixed text only when that call fails | Fixed rules |
| Voice questions | Gender, age, accent, emotion, or who is speaking call `profile_speaker` on the recording named in the question, otherwise the newest. The audio comes through the MCP `fetch_audio` tool (a direct blob read when `MCP_AUDIO_URL` is unset or the call fails) | Same |
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
Voice traits from the audio"]
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
  streamlit["<span style='font-weight:normal'>streamlit_app.py</span> | <b>render_assistant()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>frontend/streamlit_app.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Opens the assistant.</span></i>"] --> assistant["<span style='font-weight:normal'>assistant_ui.py</span> | <b>upload()</b>, <b>chat()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>frontend/assistant_ui.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Sends what you picked.</span></i>"]
  assistant --> client["<span style='font-weight:normal'>api_client.py</span><br><span style='font-size:11px;color:rgb(148,163,184)'>frontend/api_client.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Sends the request.</span></i>"]
  client -->|"upload()"| files["<span style='font-weight:normal'>files.py</span> | <b>upload_files()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/api/routes/files.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Saves the recording.</span></i>"]
  client -->|"chat()"| chat
  files -->|"enqueue_analysis()"| tasks
  files --> service["<span style='font-weight:normal'>analysis/service.py</span> | <b>run_file_analysis()</b>, <b>run_graph()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/service.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Runs the analysis.</span></i>"]
  service --> graphPy["<span style='font-weight:normal'>analysis/graph.py</span> | <b>run_graph()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/graph.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Microsoft Agent Framework works through one recording.<br>First the words, then the summary, then the Analytics prompts.</span></i>"]
  tasks["<span style='font-weight:normal'>jobs/tasks.py</span> | <b>analyze_file_task()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/jobs/tasks.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Runs the queued job.</span></i>"] --> service
  chat["<span style='font-weight:normal'>chat.py</span> | <b>chat()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/api/routes/chat.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Answers the question.</span></i>"] --> agent["<span style='font-weight:normal'>chat/agent.py</span> | <b>run_chat_agent()</b>, <b>execute_tool()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/chat/agent.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Microsoft Agent Framework answers one question<br>about this account's recordings.</span></i>"]
  agent --> tools["<span style='font-weight:normal'>chat/tools.py</span> | <b>run_summary(group_by)</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/chat/tools.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Asks for a summary.</span></i>"]
  tools --> summary["<span style='font-weight:normal'>summarize_group.py</span> | <b>summarize_group(summaries)</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/summarize_group.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Writes one summary for the group.</span></i>"]
  graphPy --> mcp["<span style='font-weight:normal'>mcp_audio.py</span> | <b>fetch_audio_via_mcp()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/mcp_audio.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Reads the audio.</span></i>"]
  graphPy --> intelligence["<span style='font-weight:normal'>intelligence.py</span> | <b>get_intelligence()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/providers/intelligence.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Azure OpenAI.<br>Transcribes the audio and writes the insights.</span></i>"]
  graphPy --> layer2["<span style='display:inline-block;width:19ch;text-align:right;font-weight:400'>llm_options.py</span> | <b>run_predefined_prompts()</b><br><span style='display:inline-block;width:19ch;text-align:right;font-weight:400'></span> | gpt-5-mini<br><span style='display:inline-block;width:19ch;text-align:right;font-weight:400'>SignalTools</span> | <b>measure_rms()</b><br><span style='display:inline-block;width:19ch;text-align:right;font-weight:400'></span> | <b>measure_speaking_pace()</b><br><span style='font-size:11px;color:rgb(148,163,184)'>backend/app/analysis/llm_options.py</span><br><i><span style='font-size:11px;color:rgb(148,163,184);font-family:Courier'>Analytics.<br>Predefined AI prompts; loudness and pace numbers<br>come from measuring tools.</span></i>"]
```

## Local and cloud

| | Local | Cloud |
| --- | --- | --- |
| Interface | Uvicorn | Container App, 2 copies growing to 8 |
| Queue | Redis and Celery | Service Bus |
| Schedule | Celery beat | Every 15 minutes |
| Files | Azurite, then `fetch_audio` when `MCP_AUDIO_URL` is set | Blob. The bytes are already loaded |
| Database | PostgreSQL 16 | One server per region, plus a standby in another zone |
| Models | Azure OpenAI and Content Safety when `LLM_PROVIDER` and `SAFETY_PROVIDER` are `azure` (as on this machine). Mock with no keys, the `.env.example` default | Azure OpenAI and Content Safety |
| Front door | none on this machine | Front Door, then API Management |
| Work | Inline in tests. Celery in Compose | One worker copy. The ceiling is 20 and no rule adds a copy |
| Account limit | 120 a minute when the limiter is on | 120 a minute |
| Trace | Langfuse when both keys are set | Same send. A failed send leaves the file result in place |

[Scaling](scaling.md)
