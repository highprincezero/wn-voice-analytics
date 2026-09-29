> Applicable only for cloud provisioning. This file is used only when deployed to the cloud with a multi-region deployment.

# Scaling

Target: **N regions**, each with **10,000 registered users** and **2,000 concurrent users**. Regions do not share audio or analysis rows. A user has one home region.

![Scaling topology](diagrams/scaling-topology.png)

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

Terraform takes `regions` as a list. The length of that list is N. Each element is a `region_stack` module: network, Postgres, blob storage, Redis, Service Bus, Container Apps, a cron job, API Management, Key Vault, Azure OpenAI, and Content Safety. Front Door sits in a global resource group. Each origin is that region's API Management gateway. API Management forwards to the regional Container App.

## Planning case

These are assumptions, not measurements. Change them and the formulas still apply.

| Input | Symbol | Value |
| --- | --- | --- |
| Registered users per region | R | 10,000 |
| Concurrent users per region | C | 2,000 |
| Regions | N | variable |
| Audio files per registered user per day | F | 1 |
| Share of a day's uploads in a 4 hour peak | P | 0.80 |
| Mean audio duration | D | 3 minutes |
| Mean object size | S | 2 MB |
| Transcription wall clock | T_stt | 15 s |
| LLM wall clock, about two calls | T_llm | 4 s |
| Layer 2 CPU | T_l2 | 1 s |

Peak file rate:

```text
files_per_day        = R * F = 10,000
peak_files_per_sec   = files_per_day * P / (4 * 3600) = 0.56
```

In-flight work if one worker holds the whole pipeline:

```text
in_flight = 0.56 * (15 + 4 + 1) ≈ 11 tasks
```

That fits in a handful of worker replicas. The expensive wait is the transcription HTTP call, so production should not pin a CPU core to it.

## Request rate

Assume the 2,000 concurrent users split like this during the busy hour:

| Activity | Share | Interval | Requests/s |
| --- | --- | --- | --- |
| Library and polling | 70% | 10 s | 140 |
| Idle session | 20% | 60 s | 6.7 |
| Upload or waiting on analysis | 10% | 5 s | 40 |
| Total |  |  | about 187 |

FastAPI is async. List queries filter on `user_id` first, so Postgres prunes to one of 16 hash partitions. Two API replicas cover this rate and a deploy. The module sets min 2 and max 8.

Upload bandwidth at peak, if the 0.56 files/s are 2 MB each:

```text
0.56 * 2 MB ≈ 1.1 MB/s ≈ 9 Mbps
```

Blob ingress is not the bottleneck at this planning case. Playback is smaller still: 200 concurrent listeners at 128 kbps is about 26 Mbps.

## Workers and context limits

`CHUNK_CHARS` defaults to 4000 characters. A transcript under that size is one structured call to `gpt-5-mini`. A longer transcript is split, each chunk is summarized with its own schema, and a reduce call merges them. At most `MAX_CHUNKS` (20) chunks are sent. Topics found on any chunk are unioned back in, so the reduce step cannot drop them. Duration is computed locally and is not part of the model context.

Queues in each region:

| Queue | Job |
| --- | --- |
| `transcription` | Speech-to-text, then the rest of the pipeline |
| `llm-layer1` | Reserved for a later split of the summary stage |
| `llm-layer2` | Reserved for a later split of RMS and spaCy |
| `rollup` | Aggregate summaries |

The worker in this POC runs the full pipeline for any analysis message. That is idempotent. At 0.56 files/s it is enough. If transcription latency or file rate grows, move the LLM call onto `llm-layer1` so I/O wait does not scale the spaCy replicas. Suggested caps once split:

| Stage | In-flight at 0.56/s | Replica cap in Terraform |
| --- | --- | --- |
| Transcription, 15 s, async HTTP | 0.56 * 15 ≈ 9 | worker max 20 |
| Layer 1, 4 s | 0.56 * 4 ≈ 3 | same worker pool today |
| Layer 2, 1 s CPU | under 1 core | same worker pool today |
| API | 187 rps | min 2, max 8 |

The rollup job is a Container Apps Job on `*/15 * * * *`. It also runs when a user asks for it in the UI. The UI path calls the same function in the API process so the screen can show the result. The cron path calls `python -m app.jobs.run_scheduled_rollup`. A user is skipped when a scheduled rollup for `group_by=user` already exists inside the interval.

## Data plane per region

| Service | Choice | Why |
| --- | --- | --- |
| Postgres Flexible Server | GP D4ds v5, 128 GB, zone-redundant HA, geo-redundant backups, 16 hash partitions, public access off | Zone failure stays inside the region. Geo-redundant backup is a copy of backups in the paired region, not a second live database. |
| Blob | GZRS, private container, versioning on | Zone and geo copies of objects. Reads stay in the home region until a storage failover is started. 10,000 * 2 MB = 20 GB/day. |
| Service Bus | Standard, four queues | 0.56 messages/s does not need Premium. Standard is not zone redundant. Premium is the zone and private-network step, and it is a large fixed cost at this rate. |
| Redis | Standard C1 | Rate-limit counter and a short cache. It is not the production queue. Standard is not zone redundant. Premium Redis is not used for the same cost reason. |
| Azure OpenAI | `gpt-4o-transcribe` and `gpt-5-mini`, Global Standard | One resource. `gpt-4o-transcribe` is speech-to-text. `gpt-5-mini` is analysis and chat. Hosted API. Self-hosting a transcriber would remove the per-minute fee and add GPU capacity we do not need at 0.56 files/s. |
| Content Safety | S0 | Prompt Shields and category analysis in front of the model and on chat input |
| Front Door | One global Standard profile | Entry in front of API Management. The app stores `home_region` and should stick a user to that region. |
| Container Apps | Zone-redundant environment, consumption, apps subnet `/23` | Replicas can land in more than one zone. `/23` is the minimum for a consumption-only environment. |
| API Management | Basic, one unit | `rate-limit-by-key` is supported on Basic and not on Consumption. See the rate-limit section. |

Global identity is a small lookup, not a copy of the audio. Sign-up writes `email -> user_id -> home_region` in the region the user was routed to, and the email unique index lives in that region's `users` table. A second region must not create the same email. The practical approach is a tiny global directory (email, user id, home region) in the primary region, replicated read-only, or an external identity provider. The POC keeps that column and does not build the global directory.

Multiply every regional number by N. There is no cross-region join and no cross-region blob read on the request path.

## Cost notes

These are order-of-magnitude illustrations for **one region** at the planning case. Recheck the Azure price sheet before budgeting. Prices move, and transcription minutes dominate.

| Item | Rough monthly shape |
| --- | --- |
| Transcription | 10,000 files/day * 3 min * 30 days = 900,000 minutes. At a few tenths of a cent to about a cent per minute, this is the largest line, on the order of several thousand dollars. |
| `gpt-5-mini` | About 2 calls * 2k tokens * 10k files * 30 days. Mini pricing makes this hundreds of dollars, not thousands, unless map-reduce expands long files. |
| Content Safety | 300k text records/month. Often on the order of a few hundred dollars. |
| Postgres D4ds with zone-redundant HA | About twice the single-server compute, because the standby is a second server. A few hundred dollars becomes closer to the high hundreds. Geo-redundant backup adds paired-region backup storage on top. |
| Container Apps | Low hundreds at this replica count. Zone redundancy on the environment does not by itself add a second SKU. |
| Blob, 600 GB GZRS | Higher than ZRS, still tens of dollars plus operations at this size. Recheck GZRS rates. |
| Service Bus Standard | Low tens of dollars. Premium is hundreds and is not required for this rate. Premium is also the SKU that is zone redundant. |
| API Management Basic | On the order of $150 per region per month for one unit. Consumption has no fixed fee but cannot key a rate limit on `sub`. Two regions are still small next to transcription. |
| Front Door Standard + Redis C1 + App Insights | Low hundreds combined. Redis Premium, which would add zones, is a separate jump and is not in this module. |
| Langfuse | Optional, separate from Azure. |

For N regions, multiply the regional lines. Front Door stays one profile. The global directory, if added, is tiny next to transcription.

The main cost lever is minutes of audio sent to `gpt-4o-transcribe`. Skipping silence, rejecting very long files, and keeping the planning case at one file per user per day matter more than the API replica count.

## Trade-offs

- Hosted Azure OpenAI instead of a self-hosted model. The POC has to run with no GPU, and 0.56 files/s does not justify a transcription cluster. The cost is per minute and the quota is regional.
- Hash partitions are fixed at 16. That is enough for 10k users in one database. Raising the modulus later means a new partitioned table and a copy, so 16 is chosen up front.
- The users table is not partitioned, so email stays unique. See [data-model.md](data-model.md).
- One worker function instead of three stage consumers. The queues exist so the split does not need a new contract. Doing it now would add failure states between stages for a rate that fits in one pool.
- On-demand rollup runs in the API. The scheduled run is a job. Both call `run_rollup`. A very large account could move the on-demand path onto the `rollup` queue.
- Active-active regions with a home-region pin, not a single write region. A region failure loses that region's availability until someone restores it. Geo-redundant Postgres backups and GZRS blobs make a restore possible in the paired region. They do not keep a live copy of each user's rows, and the app does not fail over by itself.
- API Management Basic instead of Consumption. Microsoft's policy reference lists `rate-limit-by-key` as supported on the classic and v2 gateways and not on Consumption. `rate-limit` on Consumption is per subscription key, and this API does not use subscription keys. Developer supports the per-key policy and has no SLA. Basic is the smallest tier with an SLA that can key the counter on `sub`. One Basic unit is sized well above 187 requests/s. Standard is several times the Basic price without a feature this rate needs. Premium is the tier with zone redundancy and virtual-network injection. That cost is not justified for a gateway in front of 187 requests/s, so API Management is not zone redundant.
- The FastAPI limiter is the same 120 requests per 60 seconds per user, stored in Redis. API Management is the edge. Redis is the check that still runs in Compose and that still runs if a request reaches the container. Health, sign-up, login, meta, and the prompt catalog are not counted. Public API Management routes are limited per source IP instead, at 60 per 60 seconds.
- The Container App ingress allows only API Management's public IPs. API Management requires `X-Azure-FDID` to match the Front Door profile before it validates the JWT. Basic cannot join a virtual network, and Front Door Standard cannot private-link to API Management. A private path would be Front Door Premium plus API Management Premium. This module does not take that step.
- Zone redundancy that is turned on: Postgres Flexible Server (zones 1 and 2, with geo-redundant backups, which this pair of regions supports together with zone-redundant HA), the Container Apps environment, and GZRS blob. Zone redundancy that is documented and not turned on: Service Bus Standard, Redis Standard, and API Management Basic. Each of those needs Premium for zones. Cross-region replication of a user's rows is also not turned on. Each user still has one home region, and a request does not read another region's database or blobs.

## Future work

- Split the pipeline across the four queues and scale them independently.
- ffmpeg normalization so mp3 and m4a get real duration and RMS.
- A global email directory in front of regional sign-up.
- Private endpoints for blob, Service Bus, and OpenAI, which for API Management means leaving Basic.
- A live cross-region copy of each user's rows, and a failover runbook on top of geo-restore and GZRS. The backups exist. The failover automation does not.
- Premium Service Bus, Premium Redis, and Premium API Management if those three services need availability zones.
- Evaluation set for summary and taxonomy quality.
- Retention and deletion for blocked transcripts.
