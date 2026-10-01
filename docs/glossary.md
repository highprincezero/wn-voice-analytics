# Glossary

| Short form | Meaning |
| --- | --- |
| API | application programming interface |
| HTTP | Hypertext Transfer Protocol |
| IP | Internet Protocol address |
| JSON | JavaScript Object Notation |
| JWT | JSON Web Token |
| URL | Uniform Resource Locator |
| UI | user interface |
| SQL | Structured Query Language |
| NoSQL | a store that is not queried with Structured Query Language |
| DB | database |
| MCP | Model Context Protocol |
| ID | identifier |
| PK | primary key |
| UK | unique key |
| UUID | universally unique identifier |
| MB | megabyte |
| GB | gigabyte |
| GiB | gibibyte |
| vCPU | virtual central processing unit |
| HA | high availability. PostgreSQL standby in another zone |
| GZRS | geo-zone-redundant storage. Blob copied across zones and to the paired region |
| RMS | root mean square. Loudness on 16-bit wav |

## Langfuse trace names

Langfuse is the trace store at port 3000. Each row below is a name on a trace. A model call is one row. A workflow step is another row. The workflow runner is Microsoft Agent Framework. The recording workflow is named analysis. The chat workflow is named chat.

Model calls have one name with `LLM_PROVIDER=azure` and another in mock mode, where the model column shows `mock`. Only processes that have both Langfuse keys send traces. Compose gives them to the worker, not the API, so steps that run in the API do not appear there.

| Name (azure) | Name (mock) | Meaning |
| --- | --- | --- |
| AudioLayer1 | layer1 | Insights for one short recording: summary and topics. The model column shows gpt-5-mini. |
| AudioChunk | layer1-map | One piece of a long transcript. |
| AudioReduce | layer1-reduce | Joins those pieces into one Insights result. |
| AudioRollup | rollup | One summary for a group of recordings. The scheduled summaries and the All groupings report run in the worker. On-demand summaries run in the API. |
| AudioLayer2Prompts | layer2-prompts | Analytics for one recording: the ticked predefined prompts, with the measure_rms and measure_speaking_pace tool round trip. |
| speaker-profile | speaker-profile | Speaker profile for one recording, written after that model call succeeds. It runs in the API, from the chat `profile_speaker` tool. |

| Name | Meaning |
| --- | --- |
| workflow.build | The workflow is assembled before it runs. |
| workflow.run | One run of the workflow. |
| executor.process prepare | Measure duration, and whether the file is a wav. |
| executor.process transcribe | Speech to text. |
| executor.process shield | Content safety check. A block skips Insights and Analytics. |
| executor.process layer1 | Insights. A short transcript writes AudioLayer1. A long one writes AudioChunk, then AudioReduce. |
| executor.process layer2 | Analytics: one AudioLayer2Prompts call with the ticked predefined prompts. Loudness and pace come from the measuring tools. |
| executor.process validate | Check the Insights and Analytics result before it is saved. |
| executor.process plan | Chat chooses a tool, or none. This step runs in the API. |
| executor.process tools | Chat runs that tool. This step runs in the API. |
| executor.process compose | Chat writes the reply. With Azure the model writes every reply. This step runs in the API. |
| edge_group.process SingleEdgeGroup | Hand the result from one step to the next step. |
| edge_group.process InternalEdgeGroup | Hand a framework message into a step. |
| message.send | A step sends its state onward. |

## What runs where

Yes means that side uses it. A blank cell means it does not.

| Column | Meaning |
| --- | --- |
| local | `docker compose up` |
| cloud (Microsoft Azure) | `infra/`: Service Bus, Container Apps, Blob, Azure OpenAI, Content Safety |

A folder row means the whole folder.

| name | local | cloud (Microsoft Azure) |
| --- | --- | --- |
| `.env.example` | yes | |
| `.github/` | | |
| `.gitignore` | yes | |
| `README.md` | yes | yes |
| `backend/` | yes | yes |
| `docker-compose.override.example.yml` | yes | |
| `docker-compose.yml` | yes | |
| `docs/` | yes | yes |
| `frontend/` | yes | |
| `infra/` | | yes |
| `mcp/` | yes | |
| `ruff.toml` | | |
| `samples/` | yes | |
| `scripts/` | | |
| `skills/` | | |

| name | Note |
| --- | --- |
| `mcp/` | Local Azurite server, port 8090. Not in the cloud design |
| `docker-compose.override.example.yml` | Optional local dev tools. Copy it to `docker-compose.override.yml`, which is git-ignored |
| `skills/` | The speaker-profile skill write-up. The prompt it describes is `SKILL_PROMPT` in `backend/app/analysis/speaker_skill.py` |
