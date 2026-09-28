# Demo script

Screen recording, about 9 minutes. Record the Streamlit app at http://localhost:8501 after `docker compose up --build`. Use a window around 440 pixels wide for the Classic pages so the phone layout is obvious. Widen the window before Assistant mode so the live log can sit in the right-hand column. Open the sidebar when the script reaches Assistant mode. Compose sets `MOCK_STAGE_DELAY_SEC=1.5`, so each pipeline step holds long enough to see the flowchart move.

Before you start:

```bash
docker compose up --build
```

Wait until the API health check is healthy and the worker log shows it is ready. Open http://localhost:8501. The caption under the title must read that mock mode is on. If it does not, stop and check `LLM_PROVIDER` on the API container. This demo uses no Azure keys.

Have a second browser profile or a private window unused. You will sign up as a new user.

Say, in your own words, what is on screen. The beats below are the points to hit, not a word-for-word script.

## 0:00 – 0:40  What this is

Show the title and the mock-mode caption.

Say: this is a voice analytics proof of concept. The browser talks to a Streamlit client. That client calls a separate FastAPI service. Audio goes to blob storage under the user id. A worker runs a LangGraph pipeline: duration, transcript, content safety, summary, taxonomy, and the Layer 2 options the user picked. Today the models are deterministic stand-ins so the stack runs without API keys. The same graph calls Azure OpenAI `gpt-4o-transcribe` and `gpt-4.1-mini` when `LLM_PROVIDER=azure`.

## 0:40 – 1:20  Sign up

Open the Sign up tab. Create an account with an email you have not used and a password of at least 8 characters. Submit.

You land on Prompt options. Say: sign-up returns a JWT. The next screen is the only place a user configures analysis, and it is a catalog, not a text box for a system prompt.

## 1:20 – 2:10  Prompt configuration

Check all four options: RMS energy, nouns and adjectives, speaking pace, lexicon sentiment.

Set RMS window to 250 and the spaCy top-n slider to 5. Save.

Say: the server accepts only these option ids and these parameter ranges. A free-form instruction is rejected. The saved JSON is also passed through content safety before it is stored. Prompt Shields and a category check run again on the transcript, and a blocked transcript never reaches the chat model.

## 2:10 – 3:40  Upload and Layer 1

Open Upload. Point at **Upload bundled sample** and click it.

Say: the file is `sample_call.wav`, four seconds, 16 kHz, 16-bit mono. It is a tone, not speech. Mock mode returns a fixed transcript for this file so the demo has topics to show. Duration and RMS are measured from the samples, not invented by the model. In Azure mode this button would send the same bytes to `gpt-4o-transcribe`.

The library should show the file move from uploaded or processing to completed. If it is still processing, wait. The page polls for a short time.

On the completed card, read:

- Duration 4.0 seconds.
- A summary that mentions professional topics and personal context.
- Professional topics such as project, payments, budget, and client.
- Personal topics such as dentist and family.
- Upcoming lines that mention Friday, Tuesday, and the weekend.

Open Transcript and show the weekly project sync text, including the dentist appointment and the school event.

Say: Layer 1 is duration, summary, and taxonomy with professional topics, personal topics, and upcoming events. The transcript and the analysis JSON are also stored as blobs at `users/{user id}/audio/{file id}.wav` and the matching transcript and analysis keys.

## 3:40 – 4:40  Layer 2 and filters

On the same card, point at the caption with sentiment, words per minute, and RMS.

Say: those numbers are Layer 2. Sentiment is a fixed lexicon. Pace is word count divided by the measured duration, so it is high here because a short tone was paired with a full script. RMS comes from the waveform: the second half of the file is louder, so the mean is above the quiet half. Noun and adjective counts come from spaCy.

Open Filters.

- Taxonomy contains: type `budget` and show the file remains.
- Change it to a word that is not in the taxonomy, such as `zzznone`, and show an empty list. Clear the box.
- Custom filter: sentiment, value `positive`. The file stays.
- Change the value to `negative`. The file disappears. Set the custom filter back to none.

Say: date and duration filters run in SQL on this user's partition. Taxonomy and the custom Layer 2 filters run on the analysis payload, and the custom names are whitelisted the same way as prompt options.

## 4:40 – 5:30  Rollup job

Open Rollup. Group by **Taxonomy label**. Click **Run summary job**.

Show the overall summary and a few groups (budget, dentist, and an upcoming line). Point at the caption that the latest trigger is `on_demand`.

Say: the same function runs every 15 minutes for each user who has completed analyses. Locally that is Celery beat. In Azure it is a Container Apps Job. Grouping can also be all of my files, week, or sentiment. Long sets of summaries are map-reduced so the rollup stays inside the chunk budget.

## 5:30 – 7:40  Assistant mode

Widen the window. Open the sidebar and choose **Assistant**. Leave **Live log** on. Classic stays available when you switch back.

Point at the flowchart across the top: Upload, Queued, Transcribe, Safety check, Layer 1, Layer 2, Saved. Pending steps are grey. Say: this row stays put while the chat underneath scrolls. It reads `stage` from the files API. The worker updates that column as each node starts.

The first message asks for one recording or a set of up to ten. Your lines sit on the right. The assistant lines sit on the left with a small AI circle. Click **Use bundled sample**.

Say: this is the same upload limit as the Upload page. The file is attached inside the chat, then the bot lists it back.

Check **Lexicon sentiment** and **Speaking pace**. Click **Save options and start processing**.

Say: those choices are saved with the same prompt-config API as the Prompts page. The upload uses the same files endpoint. The next line says processing has started.

Watch the flowchart. The current step pulses, the connector into it marches, and finished steps turn green. In the log, newest line first, read a few entries: file saved to blob storage, row inserted in Postgres, job queued with a task id, worker picked up, then each stage starting and finishing with a duration, then results saved. Say: that log is `GET /api/v1/events` for this user, not a guess in the browser. Toggle **Live log** off and on. On a narrow window the same log is in the sidebar and the steps wrap.

When the file is saved, open the panels: Output · Transcript (with the audio player), Output · Summary, Output · Topics & events, Output · Layer 2. Read the summary, one professional topic, one personal topic, and an upcoming line.

In the chat box, ask: what upcoming events did I mention this week?

Read the reply. It should name an upcoming line from the panel, such as Friday or Tuesday. Point at the chip named `search_files` and the output panel under the reply.

Ask: summarize my files by topic.

Point at the `run_summary` chip. Say: that question calls `POST /api/v1/chat`. A small LangGraph agent can search this user's files, open one analysis, or run the same rollup job. In mock mode the choice is a fixed rule, and it still runs the tool. The question is checked for prompt injection before the graph runs. The agent never sees another user's rows. The transcript is not pasted into the tool result. Chat history stays in this browser session.

Click **Export conversation**. Open the downloaded HTML and show the same bubbles, chips, and panels, with the audio still playable inside the file.

Switch the sidebar back to **Classic** and open Rollup if you want to show that the topic summary was stored with trigger `on_demand`. Narrow the window again if you still want the phone layout in frame.

## 7:40 – 8:20  Account, isolation, and how it scales

Open Account. Show the email, the user id, and home region `local`.

Say: every audio row, transcript, analysis, prompt config, rollup, and pipeline event is keyed by this user id. Postgres hash-partitions those tables into 16 buckets. Another account gets a 404 for this file id and an empty event log. Blob keys that do not start with `users/{this user id}/` are rejected.

Close on the architecture in one sentence: N regions, each sized for 10,000 registered users and 2,000 concurrent users, Front Door in front of API Management, then the API, and no cross-region read of audio on the request path. The planning case is about half a file per second per region at peak, which fits the worker replica range in the Terraform module. API Management Basic rate-limits each token's `sub` claim, and the API repeats that limit in Redis.

Optional last line, if you have ten seconds: the tests cover auth, upload, the mock pipeline, the stage log, partition SQL, the rollup, and the guardrails, and GitHub Actions also validates the Terraform.
