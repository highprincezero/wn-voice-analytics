import base64
import html
import re
from collections import Counter
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import altair as alt
import pandas as pd
import streamlit as st
from api_client import ApiClient, ApiError

# Streamlit primer for this file:
# - The whole script reruns top to bottom on every click; st.session_state is the dict
#   that survives reruns (messages, phase, uploads), so state lives there, not in globals.
# - st.rerun() stops the current run and starts a fresh one at once, e.g. after a reply.
# - st.columns([2.1, 1]) splits the page into side-by-side blocks sized by ratio; use
#   "with col:" to place elements inside one. Columns stack on narrow screens.
# - st.container(key=..., height=600) is a fixed-height box that scrolls on its own;
#   the key also adds a CSS class "st-key-<key>" that the stylesheet below targets.
# - st.chat_input(...) returns the submitted text (or a value with .text/.files when
#   accept_file is set) only on the run right after the user presses Enter, else None.
# - st.expander(title) and st.status(label) are collapsible blocks; st.status also
#   shows a running/complete state, which suits pipeline progress.
# - st.dataframe(df, hide_index=True, column_config={...}) renders an interactive table;
#   st.column_config.*Column sets labels, widths, and formats (progress bars, dates).
# - st.altair_chart(chart) draws a Vega-Lite chart built with altair: alt.Chart(df)
#   .mark_bar().encode(x=..., y=...), layered with "+" and sized with .properties().
# - @st.fragment(run_every=1.0) reruns just that function every second, so polling
#   refreshes the progress card without rerunning (and flickering) the whole page.

_TERMINAL = {"completed", "blocked", "failed"}
_AUDIO_TYPES = ["wav", "mp3", "m4a", "ogg", "flac"]
_MAX_FILES = 10
_FOLLOWUP = (
    "All set. Ask a follow-up, for example: what upcoming events did I mention this week, "
    "or summarize my files by topic."
)
_SUGGESTIONS = (
    ("Summarize my calls", ":material/summarize:", "Summarize my calls"),
    ("Upload a recording", ":material/upload_file:", None),
    ("Which calls mention refunds?", ":material/search:", "Which calls mention refunds?"),
    (
        "Upcoming events this week",
        ":material/event:",
        "What upcoming events did I mention this week?",
    ),
)
_TOOL_LABEL = {
    "search_files": "Searched recordings",
    "get_analysis": "Opened a recording",
    "run_summary": "Ran a summary",
}
_STEPS = (
    ("upload", "Upload"),
    ("queued", "Queued"),
    ("transcribe", "Transcribe"),
    ("safety", "Safety check"),
    ("layer1", "Layer 1"),
    ("layer2", "Layer 2"),
    ("saved", "Saved"),
)
_STEP_INDEX = {key: index for index, (key, _label) in enumerate(_STEPS)}
_STEP_LABEL = dict(_STEPS)

_INDIGO = "#3a5be8"
_GROUP_LABEL = {
    "user": "Scope",
    "taxonomy_label": "Topic",
    "week": "Week",
    "sentiment": "Sentiment",
}
_TOPIC_KEYS = (
    ("professional_topics", "Professional"),
    ("personal_topics", "Personal"),
    ("upcoming_events", "Upcoming events"),
)
_LAYER2_LABEL = {
    "sentiment_lexicon": "Sentiment",
    "speaking_pace": "Speaking pace",
    "rms_energy": "Loudness (RMS)",
    "pos_counts": "Nouns & adjectives",
}
_SKIP_TEXT = {"wav_pcm16_required": "Needs a 16-bit PCM WAV file"}
_SENTIMENT_COLOR = {
    "positive": "#1a9d54",
    "negative": "#e5484d",
    "neutral": "#8a92a6",
    "unknown": "#c2c7d0",
}
_STATUS_TEXT = {
    "completed": "Completed",
    "blocked": "Blocked",
    "failed": "Failed",
    "uploaded": "Uploaded",
    "processing": "Processing",
    "queued": "Queued",
}
_LOG_KINDS = {"timeline"}
_PANEL_HEIGHT = 600
_PANEL_ICON = {
    "rollup": ":material/bar_chart:",
    "files": ":material/table_rows:",
    "events": ":material/event:",
    "analysis": ":material/graphic_eq:",
    "topics": ":material/insights:",
    "checks": ":material/analytics:",
    "timeline": ":material/timeline:",
}
# Display titles. Older turns stored "Output · …" on the card itself.
_PANEL_TITLE = {
    "analysis": "Transcription",
    "topics": "Insights",
    "checks": "Analytics",
}
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LIST_ITEM = re.compile(r"^(?:[-*\u2022]|\d+[.)])\s+")

_MARK_SVG = (
    '<svg viewBox="0 0 24 24" width="{size}" height="{size}" aria-hidden="true">'
    '<g fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round">'
    '<line x1="4" y1="10" x2="4" y2="14"/><line x1="8" y1="7" x2="8" y2="17"/>'
    '<line x1="12" y1="4" x2="12" y2="20"/><line x1="16" y1="8" x2="16" y2="16"/>'
    '<line x1="20" y1="11" x2="20" y2="13"/></g></svg>'
)

# Shared by the live view and the exported conversation file.
_BUBBLE_CSS = """
.va-row { display: flex; gap: 9px; margin: 12px 0 6px; align-items: flex-end;
  animation: va-rise .28s ease; }
.va-row.user { justify-content: flex-end; }
.va-row.assistant { justify-content: flex-start; }
.va-av {
  width: 30px; height: 30px; border-radius: 50%; flex: 0 0 30px; display: flex;
  align-items: center; justify-content: center;
  background: linear-gradient(135deg, #1b2130, #3a4254); color: #fff;
}
.va-bubble {
  max-width: 76%; padding: 11px 15px; border-radius: 20px; font-size: 0.95rem;
  line-height: 1.5; word-wrap: break-word; box-shadow: 0 1px 2px rgba(20, 28, 45, .06);
}
.va-row.user .va-bubble {
  background: linear-gradient(135deg, #3a5be8, #5670ff); color: #fff;
  border-bottom-right-radius: 6px;
}
.va-row.assistant .va-bubble {
  background: #fff; color: #1f2330; border: 1px solid #e8ebf2;
  border-bottom-left-radius: 6px;
}
.va-chips { display: flex; flex-wrap: wrap; gap: 5px; margin: 2px 0 6px 39px; }
.va-chip {
  background: #eef1fd; color: #3a5be8; border: 1px solid #d9e0fb; border-radius: 12px;
  padding: 2px 10px; font-size: 0.68rem; font-weight: 700; letter-spacing: .02em;
}
.va-bubble p { margin: 0 0 6px; }
.va-bubble p:last-child { margin-bottom: 0; }
.va-bubble ul, .va-bubble ol { margin: 2px 0 6px; padding-left: 20px; }
.va-bubble li { margin: 1px 0; }
.va-file {
  display: inline-flex; align-items: center; gap: 6px; background: rgba(255,255,255,.18);
  border-radius: 10px; padding: 2px 8px; margin: 2px 4px 2px 0; font-size: 0.85rem;
}
@keyframes va-rise {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}
"""

_APP_CSS = (
    """
<style>
[data-testid="stMainBlockContainer"] {
  max-width: 1320px; padding-top: 1.2rem; padding-bottom: 1rem;
}
[data-testid="stBottomBlockContainer"] { max-width: 1320px; padding-bottom: 1.4rem; }
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] { overflow-x: hidden; }
[data-testid="stHeader"] { background: transparent; }
textarea, input, select { font-size: 16px !important; }

/* agent header */
.va-head { display: flex; align-items: center; gap: 12px; }
.va-mark {
  width: 44px; height: 44px; border-radius: 50%; flex: 0 0 44px; display: flex;
  align-items: center; justify-content: center;
  background: linear-gradient(135deg, #3a5be8, #7a5cff);
  box-shadow: 0 6px 16px rgba(58, 91, 232, .32);
}
.va-title { font-weight: 700; font-size: 1.08rem; color: #1f2330; line-height: 1.2; }
.va-status {
  font-size: 0.78rem; color: #1a9d54; display: flex; align-items: center; gap: 6px;
  margin-top: 2px;
}
.va-live {
  width: 8px; height: 8px; border-radius: 50%; background: #1fbf5f;
  animation: va-pulse 1.6s infinite;
}
.st-key-va-header {
  border-bottom: 1px solid #e8ebf2; padding-bottom: 12px; margin-bottom: 4px;
}
.st-key-va-header button { min-height: 36px; }
.st-key-va-header [data-testid="stPopoverButton"] { min-height: 36px; }

/* empty state */
.va-hero { text-align: center; padding: 11vh 0 18px; }
.va-hero .va-mark { width: 64px; height: 64px; margin: 0 auto 18px; }
.va-hero h1 {
  font-size: 1.9rem; font-weight: 750; color: #1f2330; margin: 0 0 6px; padding: 0;
  letter-spacing: -0.01em;
}
.va-hero p { color: #8a92a6; font-size: 1rem; margin: 0; }
.st-key-va-chips { margin-top: 6px; }
.st-key-va-chips button {
  border-radius: 999px; background: #fff; border: 1px solid #e3e7f1; color: #3d4556;
  font-size: 0.86rem; min-height: 36px; padding: 4px 14px;
  box-shadow: 0 1px 2px rgba(20, 28, 45, .05);
}
.st-key-va-chips button:hover { border-color: #3a5be8; color: #3a5be8; background: #f7f8ff; }

/* composer */
[data-testid="stChatInput"] {
  border-radius: 22px !important; background: #fff !important;
  box-shadow: 0 4px 18px rgba(20, 28, 45, .08); border: 1px solid #e3e7f1;
}
[data-testid="stChatInput"] > div { background: #fff !important; border-radius: 22px !important; }
[data-testid="stChatInput"] textarea { background: transparent !important; }
[data-testid="stBottom"] > div { background: #f5f7fc; }
.st-key-va-hero-composer { margin-top: 8px; }

/* thread + cards under assistant turns. st.container(height=...) sets a fixed pixel
   height on the block and on its wrapper, and both are flex items, so flex is reset
   before the height can follow the viewport. */
div:has(> [class*="st-key-va-thread"]), [class*="st-key-va-thread"] {
  flex: 0 0 auto !important; height: calc(100vh - 205px) !important;
  min-height: 360px !important;
}
[class*="st-key-va-thread"] { border: none !important; padding: 0 2px !important; }
.st-key-va-composer { margin-top: 6px; }
div:has(> .st-key-va-logs), .st-key-va-logs {
  flex: 0 0 auto !important; height: calc(100vh - 131px) !important;
  min-height: 434px !important;
}

/* activity column: low-key gray logs */
.st-key-va-logs, .st-key-va-logs-mobile details {
  background: #f6f7f9 !important; border: 1px solid #e6e8ec !important;
  border-radius: 14px !important;
}
.st-key-va-logs { padding: 10px 12px 12px !important; gap: 0.5rem !important; }
.va-lg-title {
  font-size: 0.66rem; font-weight: 800; letter-spacing: .09em; text-transform: uppercase;
  color: #9aa0ab; display: flex; align-items: center; gap: 6px;
}
.va-lg-title .pip { width: 6px; height: 6px; border-radius: 50%; background: #b8bec8; }
.va-lg-empty {
  text-align: center; color: #a3a9b4; font-size: 0.8rem; padding: 22vh 12px 0;
  line-height: 1.5;
}
.va-lg-empty .ico { font-size: 1.4rem; color: #c3c8d0; margin-bottom: 6px; }
.va-lg-empty b { display: block; color: #8b919c; font-weight: 600; font-size: 0.84rem; }
.va-lg-head {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.7rem; color: #7b818c; border-top: 1px dashed #dde0e5; padding-top: 8px;
  display: flex; gap: 6px; align-items: baseline;
}
.va-lg-head b { color: #4b5160; font-weight: 700; }
.va-lg-head .when { color: #a3a9b4; margin-left: auto; }
.va-lg-prompt {
  font-size: 0.74rem; color: #8b919c; margin: 2px 0 4px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.va-lg-now { font-size: 0.74rem; color: #6b7280; display: flex; gap: 6px; align-items: center; }
.va-lg-now i {
  width: 6px; height: 6px; border-radius: 50%; background: #9aa6e8;
  animation: va-blink 1.2s infinite;
}
[class*="st-key-va-logs"] .va-chips { margin: 0 0 4px; }
[class*="st-key-va-logs"] .va-chip {
  background: #eceef2; color: #6b7280; border-color: #e1e4e9; font-weight: 600;
  font-size: 0.64rem;
}
[class*="st-key-va-logs"] .va-steps { margin: 0 0 4px; }
[class*="st-key-va-logs"] .va-step { padding: 3px 0; gap: 8px; }
[class*="st-key-va-logs"] .va-step:not(:last-child)::after {
  left: 3px; top: 14px; background: #e3e5ea;
}
[class*="st-key-va-logs"] .va-step .dot {
  width: 8px; height: 8px; flex: 0 0 8px; background: #b8bec8; box-shadow: 0 0 0 2px #eceef2;
}
[class*="st-key-va-logs"] .va-step.warn .dot { background: #d97757; }
[class*="st-key-va-logs"] .va-step b { font-size: 0.74rem; font-weight: 600; color: #4b5160; }
[class*="st-key-va-logs"] .va-step small { font-size: 0.68rem; color: #9aa0ab; }
[class*="st-key-va-logs"] .va-step code {
  font-size: 0.66rem; background: #eceef2; color: #6b7280;
}
[class*="st-key-va-logs"] [data-testid="stExpander"] details {
  background: #f1f2f5; border-color: #e3e5ea; box-shadow: none; border-radius: 10px;
}
[class*="st-key-va-logs"] [data-testid="stExpander"] summary {
  background: transparent; font-size: 0.72rem; color: #7b818c; padding-top: 4px;
  padding-bottom: 4px; min-height: 0;
}
[class*="st-key-va-logs"] [data-testid="stExpander"] summary:hover { background: #eceef2; }
[class*="st-key-va-logs"] [data-testid="stJson"] { font-size: 11px; }
[class*="st-key-va-logs"] [data-testid="stCaptionContainer"] { font-size: 0.68rem; }
.va-tl {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10.5px; line-height: 1.5; color: #7b818c; margin: 2px 0 4px;
}
.va-tl .row { display: grid; grid-template-columns: 58px 72px 1fr auto; gap: 6px; }
.va-tl .row span:nth-child(2) { color: #5b6270; }
.va-tl .row span:nth-child(4) { color: #a3a9b4; }
.va-tl .row.err { color: #b45309; }
.va-tl-name { font-size: 0.7rem; color: #6b7280; font-weight: 600; margin-top: 2px; }
[class*="st-key-va-logs"] [data-testid="stExpander"] .va-flow-name { color: #4b5160; }
[class*="st-key-va-logs"] .va-flow .step {
  background: #eef0f3; border-color: #e1e4e9; color: #a3a9b4; font-size: 0.66rem;
  padding: 2px 8px;
}
[class*="st-key-va-logs"] .va-flow .step.done {
  background: #e8ece9; border-color: #d3dcd5; color: #4f6f5b;
}
[class*="st-key-va-logs"] .va-flow .step.current {
  background: #f3f5fd; border-color: #b9c4f5; color: #3a5be8;
}
[class*="st-key-va-logs"] .va-flow .conn { width: 8px; background: #e1e4e9; }
[class*="st-key-va-logs"] .va-flow .conn.done { background: #b9c9bf; }
[class*="st-key-va-turn-"] [data-testid="stExpander"] {
  margin-left: 39px; width: calc(100% - 39px) !important;
}
[data-testid="stExpander"] details {
  border-radius: 14px; border-color: #e8ebf2; background: #fff;
  box-shadow: 0 1px 2px rgba(20, 28, 45, .05);
}
[data-testid="stExpander"] summary { font-size: 0.86rem; background: #fbfcfe; }
[data-testid="stExpander"] summary:hover { background: #f5f7fc; }
/* Processing status only: one horizontal line, gray type. Card titles stay as they are.
   Open, the line grows so the stage row under the chevron is readable. */
[class*="st-key-va-proc-status"] [data-testid="stExpander"] {
  width: fit-content !important;
  max-width: 100%;
  margin-left: 39px;
}
[class*="st-key-va-proc-status"] [data-testid="stExpander"]:has(details[open]) {
  width: calc(100% - 39px) !important;
}
[class*="st-key-va-proc-status"] [data-testid="stExpander"] details {
  width: fit-content;
  border: none;
  box-shadow: none;
  background: transparent;
}
[class*="st-key-va-proc-status"] [data-testid="stExpander"] details[open] {
  width: 100%;
}
[class*="st-key-va-proc-status"] [data-testid="stExpanderDetails"] {
  width: 100%;
  padding-top: 4px;
}
[class*="st-key-va-proc-status"] [data-testid="stExpander"] summary {
  width: auto;
  background: transparent;
  display: inline-flex;
  flex-direction: row;
  align-items: center;
}
[class*="st-key-va-proc-status"] [data-testid="stExpander"] summary,
[class*="st-key-va-proc-status"] [data-testid="stExpander"] summary * {
  color: #8a92a6 !important;
  font-weight: 500;
}
.st-key-va-proc-status [data-testid="stExpanderIcon"] {
  display: inline-flex;
  animation: va-spin 0.9s linear infinite;
}
/* The compact panel animates its own height and can lock at 0. The stage row
   is a sibling under the line, so the click still reveals it. */
.st-key-va-proc-detail {
  margin: 2px 0 8px 39px;
  width: calc(100% - 39px) !important;
}
[class*="st-key-va-card-"] {
  margin-left: 39px; width: calc(100% - 39px) !important; background: #fff;
  border: 1px solid #e8ebf2; border-radius: 14px;
  padding: 12px 14px 6px; box-shadow: 0 1px 2px rgba(20, 28, 45, .05);
}
.va-card-title {
  font-size: 0.72rem; font-weight: 800; letter-spacing: .06em; text-transform: uppercase;
  color: #8a92a6; margin-bottom: 2px;
}
.va-out { font-size: 0.92rem; line-height: 1.55; color: #1f2330; }
.va-out p { margin: 0 0 8px; }
.va-out ul, .va-out ol { margin: 0 0 8px; padding-left: 20px; }

/* structured output panels */
.va-sec {
  font-size: 0.7rem; font-weight: 800; letter-spacing: .07em; text-transform: uppercase;
  color: #8a92a6; margin: 10px 0 4px;
}
[class*="st-key-va-metrics-"] { flex-wrap: wrap; }
[class*="st-key-va-metrics-"] [data-testid="stMetric"],
[data-testid="stExpander"] [data-testid="stMetric"] {
  background: #f7f8ff; border-color: #e3e7fb; border-radius: 12px; padding: 8px 12px;
  min-width: 110px;
}
[data-testid="stExpander"] [data-testid="stMetricLabel"] p {
  font-size: 0.72rem; color: #6b7386; font-weight: 600;
}
[data-testid="stExpander"] [data-testid="stMetricValue"] {
  font-size: 1.2rem; font-weight: 700; color: #1f2330;
}
[data-testid="stExpander"] [data-testid="stDataFrame"],
[data-testid="stExpander"] [data-testid="stTable"] {
  border-radius: 12px; overflow: hidden;
}
[data-testid="stExpander"] [data-testid="stTable"] td:first-child {
  color: #6b7386; width: 34%; font-size: 0.84rem;
}
[data-testid="stExpander"] [data-testid="stTable"] td { font-size: 0.86rem; }
.va-empty {
  border: 1px dashed #d9e0fb; background: #fafbff; border-radius: 14px;
  padding: 18px 16px; text-align: center; margin: 6px 0 10px;
}
.va-empty .ico { font-size: 1.3rem; color: #9aa6e8; line-height: 1; margin-bottom: 6px; }
.va-empty b { display: block; font-size: 0.92rem; color: #3d4556; font-weight: 650; }
.va-empty small { display: block; font-size: 0.8rem; color: #8a92a6; margin-top: 3px; }
.va-transcript {
  max-height: 240px; overflow: auto; background: #f7f8fc; border: 1px solid #e8ebf2;
  border-radius: 12px; padding: 10px 12px; font-size: 0.88rem; line-height: 1.6;
  color: #2b3142; margin-bottom: 8px;
}
.va-tax { display: flex; flex-direction: column; gap: 8px; margin: 4px 0 10px; }
.va-taxrow { display: flex; gap: 10px; align-items: flex-start; }
.va-taxrow .cat {
  flex: 0 0 132px; font-size: 0.8rem; font-weight: 650; color: #3d4556; padding-top: 3px;
}
.va-taxrow .cat small {
  margin-left: 6px; background: #eef1fd; color: #3a5be8; border-radius: 8px;
  padding: 0 6px; font-size: 0.7rem; font-weight: 700;
}
.va-taxrow .tags { display: flex; flex-wrap: wrap; gap: 6px; }
.va-tag {
  background: #eef1fd; color: #2f4bd0; border: 1px solid #d9e0fb; border-radius: 999px;
  padding: 3px 11px; font-size: 0.8rem;
}
.va-none { color: #a3a9b6; font-size: 0.8rem; padding-top: 3px; }
.va-facts {
  display: flex; flex-wrap: wrap; gap: 8px; margin: 2px 0 12px;
}
.va-fact {
  display: inline-flex; align-items: baseline; gap: 6px;
  background: #fff; border: 1px solid #e6e9f2; border-radius: 999px;
  padding: 5px 12px; box-shadow: 0 1px 2px rgba(20, 28, 45, .04);
  max-width: 100%;
}
.va-fact small {
  font-size: 0.66rem; letter-spacing: .05em; text-transform: uppercase;
  color: #8a92a6; font-weight: 700;
}
.va-fact b { font-size: 0.84rem; font-weight: 650; color: #1f2330; }
.va-fact.ok b { color: #16804a; }
.va-fact.bad b { color: #b42318; }
.va-fact.warn b { color: #9a6b12; }
.va-read {
  display: flex; flex-wrap: wrap; align-items: flex-start;
  gap: 22px 36px; margin: 6px 2px 8px;
}
.va-read .row {
  display: flex; flex-direction: column; gap: 2px;
  min-width: 120px; padding: 0; border: 0;
}
.va-read .k { color: #8a92a6; font-size: 0.78rem; }
.va-read .v { color: #3d4556; font-size: 1rem; font-weight: 640; }
.va-read .v.ok { color: #16804a; }
.va-read .v.bad { color: #b42318; }
.va-read small { color: #8a92a6; font-size: 0.78rem; line-height: 1.4; }
.va-spark { display: block; width: 120px; height: 22px; margin-top: 4px; }
.va-quiet { color: #8a92a6; font-size: 0.86rem; line-height: 1.45; margin: 2px 0 8px; }

/* agent step timeline */
.va-steps { position: relative; margin: 2px 0 10px; }
.va-step { display: flex; gap: 10px; padding: 5px 0; position: relative; }
.va-step:not(:last-child)::after {
  content: ""; position: absolute; left: 5px; top: 20px; bottom: -6px; width: 2px;
  background: #e3e7f1;
}
.va-step .dot {
  width: 12px; height: 12px; border-radius: 50%; margin-top: 4px; flex: 0 0 12px;
  background: #3a5be8; box-shadow: 0 0 0 3px #eef1fd;
}
.va-step.warn .dot { background: #e5484d; box-shadow: 0 0 0 3px #fde8e8; }
.va-step b { font-size: 0.86rem; font-weight: 650; color: #1f2330; display: block; }
.va-step small { font-size: 0.78rem; color: #8a92a6; display: block; word-break: break-word; }
.va-step code { font-size: 0.75rem; background: #f3f5fa; padding: 1px 5px; border-radius: 6px; }

/* typing indicator */
.va-typing {
  background: #fff; border: 1px solid #e8ebf2; border-radius: 20px; border-bottom-left-radius: 6px;
  padding: 11px 16px; display: inline-flex; gap: 5px; align-items: center;
}
.va-typing b { color: #5b6270; font-weight: 500; font-size: 0.9rem; margin-right: 4px; }
.va-typing i {
  width: 7px; height: 7px; border-radius: 50%; background: #c2c7d0; display: inline-block;
  animation: va-blink 1.2s infinite;
}
.va-typing i:nth-of-type(2) { animation-delay: .2s; }
.va-typing i:nth-of-type(3) { animation-delay: .4s; }

/* pipeline flow inside the processing card */
.va-flow-name { font-size: 0.82rem; font-weight: 650; color: #1f2330; margin: 6px 0 4px; }
.va-flow { display: flex; flex-wrap: wrap; align-items: center; row-gap: 6px; margin-bottom: 6px; }
.va-flow .step {
  border: 1px solid #e3e7f1; background: #f5f7fc; color: #8a92a6;
  border-radius: 999px; padding: 3px 10px; font-size: 0.74rem;
}
.va-flow .step.done { background: #e9f7ef; border-color: #b9e5c9; color: #16804a; }
.va-flow .step.current {
  background: #eef1fd; border-color: #3a5be8; color: #3a5be8; font-weight: 650;
  animation: va-ring 1.4s ease-in-out infinite;
}
.va-flow .step.skipped { background: #fafafa; color: #a3a9b6; border-style: dashed; }
.va-flow .step.failed { background: #fde8e8; border-color: #e5484d; color: #b42318; }
.va-flow .conn { width: 14px; height: 2px; margin: 0 3px; background: #e3e7f1; }
.va-flow .conn.done { background: #7fd0a0; }
.va-events { font-size: 0.74rem; color: #8a92a6; line-height: 1.5; margin-bottom: 4px; }
.va-log {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10.5px; line-height: 1.45; max-height: 300px; overflow: auto;
  background: #eef0f3; color: #6b7280; padding: 8px 10px; border-radius: 10px;
  border: 1px solid #e3e5ea;
}
.va-log .line { margin: 0 0 5px; white-space: pre-wrap; word-break: break-word; }
.va-log .line b { color: #9aa0ab; font-weight: 500; }
.va-log .err { color: #b45309; }
"""
    + _BUBBLE_CSS
    + """
@keyframes va-pulse {
  0% { box-shadow: 0 0 0 0 rgba(31, 191, 95, .5); }
  70% { box-shadow: 0 0 0 7px rgba(31, 191, 95, 0); }
  100% { box-shadow: 0 0 0 0 rgba(31, 191, 95, 0); }
}
@keyframes va-blink {
  0%, 60%, 100% { opacity: .3; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}
@keyframes va-ring {
  0%, 100% { box-shadow: 0 0 0 0 rgba(58, 91, 232, .35); }
  50% { box-shadow: 0 0 0 5px rgba(58, 91, 232, 0); }
}
@keyframes va-spin { to { transform: rotate(360deg); } }
/* header items that move into the menu on small screens */
.st-key-va-menu-new, .st-key-va-menu-classic, .st-key-va-menu-activity { display: none; }
.st-key-va-activity label { white-space: nowrap; }
@media (max-width: 768px) {
  [data-testid="stMainBlockContainer"] {
    padding-left: 0.8rem; padding-right: 0.8rem; padding-bottom: 90px;
  }
  .st-key-va-new-chat, .st-key-va-classic, .st-key-va-activity { display: none; }
  .st-key-va-menu-new, .st-key-va-menu-classic, .st-key-va-menu-activity { display: block; }
  .va-title { font-size: 1rem; }
  .va-mark { width: 38px; height: 38px; flex-basis: 38px; }
  /* stack the chat and activity columns */
  [data-testid="stHorizontalBlock"]:has(.st-key-va-logs, .st-key-va-logs-mobile) {
    flex-direction: column; gap: 0.6rem;
  }
  [data-testid="stHorizontalBlock"]:has(.st-key-va-logs, .st-key-va-logs-mobile)
    > [data-testid="stColumn"] { width: 100% !important; flex: 1 1 100% !important; }
  div:has(> [class*="st-key-va-thread"]), [class*="st-key-va-thread"] {
    height: auto !important; min-height: 0 !important; max-height: none !important;
    overflow: visible !important;
  }
  div:has(> .st-key-va-logs), .st-key-va-logs {
    height: auto !important; min-height: 0 !important; max-height: 60vh !important;
  }
  .va-lg-empty { padding-top: 12px; }
  /* composer pinned to the bottom of the screen */
  .st-key-va-composer {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 50; margin: 0;
    padding: 8px 10px calc(10px + env(safe-area-inset-bottom)); background: #f5f7fc;
    border-top: 1px solid #e8ebf2;
  }
  [data-testid="stMetric"] { min-width: calc(50% - 6px) !important; }
  [data-testid="stTable"] { overflow-x: auto; }
  .va-transcript { max-height: 200px; }
  .va-tl .row { grid-template-columns: 58px 64px 1fr auto; }
}
@media (max-width: 768px) {
  .va-bubble { max-width: 88%; }
  .va-hero { padding-top: 6vh; }
  .va-hero h1 { font-size: 1.5rem; }
  [class*="st-key-va-turn-"] [data-testid="stExpander"],
  [class*="st-key-va-card-"] { margin-left: 0; width: 100% !important; }
  .va-hero { padding-top: 4vh; }
  .va-chips { margin-left: 0; }
  .va-taxrow { flex-direction: column; gap: 4px; }
  .va-taxrow .cat { flex-basis: auto; }
  .va-read { gap: 16px 22px; }
  .va-status .va-provider { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .va-row, .va-live, .va-typing i, .va-flow .step.current,
  .st-key-va-proc-status [data-testid="stExpanderIcon"] { animation: none; }
}
</style>
"""
)


# ---------------------------------------------------------------- formatting


def _text_html(value: str) -> str:
    return "<br>".join(html.escape(value).splitlines())


def _inline_html(value: str) -> str:
    return _BOLD.sub(r"<b>\1</b>", html.escape(value))


def _prose_html(value: str) -> str:
    """Light markdown for replies and summaries: paragraphs, bullet or numbered lists, bold."""
    blocks: list[str] = []
    items: list[str] = []
    list_tag = "ul"
    for raw in str(value or "").splitlines():
        line = raw.strip()
        match = _LIST_ITEM.match(line)
        if match:
            if not items:
                list_tag = "ol" if line[:1].isdigit() else "ul"
            items.append(f"<li>{_inline_html(line[match.end() :])}</li>")
            continue
        if items:
            blocks.append(f"<{list_tag}>{''.join(items)}</{list_tag}>")
            items = []
        if line:
            blocks.append(f"<p>{_inline_html(line)}</p>")
    if items:
        blocks.append(f"<{list_tag}>{''.join(items)}</{list_tag}>")
    return "".join(blocks)


def _mark(size: int = 22) -> str:
    return _MARK_SVG.format(size=size)


def _viewer_zone() -> tzinfo:
    try:
        name = st.context.timezone
    except Exception:
        name = None
    if name:
        try:
            return ZoneInfo(str(name))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return timezone.utc


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        # The API stores naive UTC timestamps.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _local_time(value) -> datetime | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return parsed.astimezone(_viewer_zone()).replace(tzinfo=None)


def _zone_note() -> str:
    zone = _viewer_zone()
    return f"Shown in {getattr(zone, 'key', None) or 'UTC'} time"


def _duration_text(value) -> str:
    try:
        total = int(round(float(value)))
    except (TypeError, ValueError):
        return "—"
    hours, rest = divmod(max(total, 0), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _wav_seconds(data: bytes) -> float | None:
    # A WAV file is a list of RIFF chunks: "fmt " holds the bytes per second and
    # "data" holds the samples, so length = data size / bytes per second. Reading the
    # header works for any WAV sample format; other formats (mp3, m4a...) return None.
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    offset, byte_rate = 12, 0
    while offset + 8 <= len(data):
        name = data[offset : offset + 4]
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        if name == b"fmt " and size >= 12:
            byte_rate = int.from_bytes(data[offset + 16 : offset + 20], "little")
        elif name == b"data" and byte_rate:
            size = min(size, len(data) - offset - 8)
            return size / byte_rate
        offset += 8 + size + (size & 1)
    return None


# MPEG audio (mp3) frame header tables, indexed by the 4-bit bitrate and 2-bit rate
# fields. Key 1 = MPEG-1; key 2 = MPEG-2/2.5 (lower bitrates, used for speech).
_MP3_KBPS = {
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _mp3_seconds(data: bytes) -> float | None:
    # Skip an ID3v2 tag (10-byte header; the size is four 7-bit bytes).
    start = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        start = 10 + sum((data[6 + i] & 0x7F) << (21 - 7 * i) for i in range(4))
    for pos in range(start, min(len(data) - 4, start + 4096)):
        head = data[pos : pos + 4]
        if head[0] != 0xFF or (head[1] & 0xE0) != 0xE0:
            continue
        version, layer = (head[1] >> 3) & 3, (head[1] >> 1) & 3
        bitrate, rate_index = head[2] >> 4, (head[2] >> 2) & 3
        if version == 1 or layer != 1 or bitrate in (0, 15) or rate_index == 3:
            continue  # not a Layer III frame header, keep scanning
        rate = _MP3_RATES[version][rate_index]
        samples = 1152 if version == 3 else 576
        mono = head[3] >> 6 == 3
        # Encoders often put a "Xing"/"Info" frame first with the exact frame count.
        side = (17 if mono else 32) if version == 3 else (9 if mono else 17)
        tag = pos + 4 + side
        if data[tag : tag + 4] in (b"Xing", b"Info") and data[tag + 7] & 1:
            return int.from_bytes(data[tag + 8 : tag + 12], "big") * samples / rate
        if data[pos + 36 : pos + 40] == b"VBRI":
            return int.from_bytes(data[pos + 50 : pos + 54], "big") * samples / rate
        # Otherwise assume a constant bitrate: audio bytes * 8 / bits per second.
        end = len(data) - (128 if data[-128:-125] == b"TAG" else 0)
        return (end - pos) * 8 / (_MP3_KBPS[1 if version == 3 else 2][bitrate] * 1000)
    return None


def _clip_seconds(clip: tuple[bytes, str] | None) -> float | None:
    """Length of the downloaded audio when its header tells us (WAV or MP3)."""
    if not clip:
        return None
    try:
        return _wav_seconds(clip[0]) or _mp3_seconds(clip[0])
    except (IndexError, KeyError, ZeroDivisionError):
        return None


def _size_text(value) -> str:
    size = float(value or 0)
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / 1024:.0f} KB"


def _nice(value) -> str:
    text = str(value or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "—"


def _names_cell(names: list[str], limit: int = 3) -> str:
    if not names:
        return "—"
    counts = Counter(names)
    shown = [f"{name} ×{count}" if count > 1 else name for name, count in counts.most_common(limit)]
    extra = len(counts) - len(shown)
    return ", ".join(shown) + (f" +{extra} more" if extra > 0 else "")


def _topic_labels(taxonomy: dict | None) -> list[str]:
    taxonomy = taxonomy or {}
    labels: list[str] = []
    for key, _label in _TOPIC_KEYS:
        labels.extend(str(item) for item in taxonomy.get(key) or [])
    return labels


def _subject_labels(taxonomy: dict | None) -> list[str]:
    taxonomy = taxonomy or {}
    labels = list(taxonomy.get("professional_topics") or [])
    labels.extend(taxonomy.get("personal_topics") or [])
    return [str(item) for item in labels]


def _has_speech(detail: dict) -> bool:
    return bool(str(detail.get("transcript") or "").strip())


# ---------------------------------------------------------------- output data

# Panels are stored in session state as plain data (kind + dict) and turned
# into tables, charts, or prose at render time, so reruns never call the API.


def _panel(kind: str, title: str, data: dict, audio: str | None = None) -> dict:
    return {"kind": kind, "title": title, "data": data, "audio": audio}


def _audio(api: ApiClient, file_id: str) -> tuple[bytes, str] | None:
    cache = st.session_state.setdefault("assistant_audio", {})
    if file_id in cache:
        return cache[file_id]
    try:
        data, media = api.download_audio(file_id)
    except ApiError:
        return None
    cache[file_id] = (data, media)
    return cache[file_id]


def _safe_detail(api: ApiClient, file_id: str | None) -> dict | None:
    if not file_id:
        return None
    try:
        return api.get_file(str(file_id))
    except ApiError:
        return None


def _safe_files(api: ApiClient) -> list[dict]:
    try:
        return api.list_files({"limit": 200}).get("items") or []
    except ApiError:
        return []


def _file_events(api: ApiClient, file_id: str) -> list[dict]:
    try:
        events = api.list_events().get("items") or []
    except ApiError:
        return []
    return [event for event in events if str(event.get("file_id")) == str(file_id)]


def _group_keys(item: dict, group_by: str) -> list[str]:
    if group_by == "taxonomy_label":
        return _topic_labels(item.get("taxonomy")) or ["unlabeled"]
    if group_by == "sentiment":
        sentiment = ((item.get("layer2") or {}).get("sentiment_lexicon") or {}).get("label")
        return [str(sentiment or "unknown")]
    if group_by == "week":
        created = _parse_time(item.get("created_at"))
        if created is None:
            return []
        iso = created.isocalendar()
        return [f"{iso.year}-W{iso.week:02d}"]
    return ["all"]


def _group_members(api: ApiClient, result: dict) -> dict[str, list[str]]:
    """Recording names per rollup group, rebuilt from the file list with the same rules."""
    group_by = str(result.get("group_by") or "user")
    start = _parse_time(result.get("time_from"))
    end = _parse_time(result.get("time_to"))
    members: dict[str, list[str]] = {}
    for item in _safe_files(api):
        if item.get("status") != "completed":
            continue
        created = _parse_time(item.get("created_at"))
        if created is not None and start is not None and created < start:
            continue
        if created is not None and end is not None and created > end:
            continue
        name = str(item.get("original_filename") or item.get("filename") or "recording")
        for key in _group_keys(item, group_by):
            members.setdefault(key, []).append(name)
    return members


def _rollup_panel(api: ApiClient, result: dict) -> dict:
    data = dict(result)
    data["members"] = _group_members(api, result) if result.get("file_count") else {}
    group_by = str(result.get("group_by") or "user")
    title = "Output · Summary"
    if group_by != "user":
        title = f"Output · Summary by {_GROUP_LABEL.get(group_by, group_by).lower()}"
    return _panel("rollup", title, data)


def _file_row(item: dict, stages: dict[str, dict]) -> dict:
    listed = stages.get(str(item.get("id"))) or {}
    layer2 = item.get("layer2") or listed.get("layer2") or {}
    return {
        "id": item.get("id"),
        "filename": item.get("filename") or item.get("original_filename") or "recording",
        "status": item.get("status") or listed.get("status"),
        "stage": item.get("stage") or listed.get("stage"),
        "duration_sec": item.get("duration_sec"),
        "created_at": item.get("created_at") or listed.get("created_at"),
        "topics": _subject_labels(item.get("taxonomy")),
        "events": list((item.get("taxonomy") or {}).get("upcoming_events") or []),
        "sentiment": (layer2.get("sentiment_lexicon") or {}).get("label"),
        "summary": item.get("summary"),
        "block_reason": item.get("block_reason"),
    }


def _search_panels(api: ApiClient, result: dict, prompt: str) -> list[dict]:
    found = [item for item in result.get("items") or [] if isinstance(item, dict)]
    wants_events = any(word in prompt.lower() for word in ("upcoming", "event"))
    if len(found) == 1 and found[0].get("id") and not wants_events:
        detail = _safe_detail(api, found[0]["id"])
        return _panels_for_detail(api, detail or found[0])
    stages = {str(item.get("id")): item for item in _safe_files(api)} if found else {}
    rows = [_file_row(item, stages) for item in found]
    total = result.get("total")
    panels = []
    if wants_events:
        panels.append(_panel("events", "Output · Upcoming events", {"rows": rows}))
    panels.append(
        _panel(
            "files",
            "Output · Recordings",
            {"rows": rows, "total": len(rows) if total is None else total},
        )
    )
    return panels


def _panels_for_detail(api: ApiClient, detail: dict) -> list[dict]:
    file_id = detail.get("id")
    filename = detail.get("original_filename") or detail.get("filename") or "Recording"
    record = dict(detail)
    record["filename"] = filename
    panels = [_panel("analysis", "Transcription", record, audio=file_id)]
    if detail.get("status") != "blocked":
        panels.append(_panel("topics", "Insights", record))
    panels.append(_panel("checks", "Analytics", record))
    events = _file_events(api, str(file_id)) if file_id else []
    if events:
        panels.append(_panel("timeline", "Output · Processing timeline", {"events": events}))
    return panels


def _panels_for_tools(api: ApiClient, tool_calls: list[dict], prompt: str = "") -> list[dict]:
    panels: list[dict] = []
    for call in tool_calls:
        name = str(call.get("name") or "")
        result = call.get("result") or {}
        if result.get("error"):
            continue
        if name == "run_summary":
            panels.append(_rollup_panel(api, result))
        elif name == "get_analysis":
            file_id = (call.get("arguments") or {}).get("file_id") or result.get("id")
            detail = _safe_detail(api, file_id)
            panels.extend(_panels_for_detail(api, detail or result))
        elif name == "search_files":
            panels.extend(_search_panels(api, result, prompt))
    return panels


# ---------------------------------------------------------------- output rows
# Shared by the live view (dataframes) and the exported conversation (HTML tables).


def _rollup_rows(data: dict) -> list[dict]:
    group_by = str(data.get("group_by") or "user")
    label = _GROUP_LABEL.get(group_by, "Group")
    total = int(data.get("file_count") or 0)
    members = data.get("members") or {}
    rows = []
    for group in data.get("groups") or []:
        if not isinstance(group, dict):
            continue
        key = str(group.get("key") or "—")
        count = int(group.get("file_count") or 0)
        if key == "all":
            shown = "All recordings"
        elif group_by == "sentiment":
            shown = _nice(key)
        else:
            shown = key
        rows.append(
            {
                label: shown,
                "Recordings": count,
                "Share": round(100.0 * count / total, 1) if total else 0.0,
                "Files": _names_cell(members.get(key) or []),
                "Summary": str(group.get("summary") or "—"),
            }
        )
    if group_by == "week":
        rows.sort(key=lambda row: row[label])
    else:
        rows.sort(key=lambda row: (-row["Recordings"], row[label]))
    return rows


def _file_rows(data: dict) -> list[dict]:
    rows = []
    for row in data.get("rows") or []:
        status = str(row.get("status") or "")
        stage = str(row.get("stage") or "")
        state = _STATUS_TEXT.get(status, _nice(status))
        if status not in _TERMINAL and stage:
            state = f"{state} · {_STEP_LABEL.get(stage, _nice(stage))}"
        rows.append(
            {
                "Recording": row.get("filename") or "recording",
                "Status": state,
                "Duration": _duration_text(row.get("duration_sec")),
                "Uploaded": _local_time(row.get("created_at")),
                "Topics": ", ".join(row.get("topics") or []) or "—",
                "Summary": str(row.get("summary") or row.get("block_reason") or "—"),
            }
        )
    return rows


def _event_rows(data: dict) -> list[dict]:
    rows = []
    for row in data.get("rows") or []:
        for event in row.get("events") or []:
            rows.append(
                {
                    "Mentioned": _local_time(row.get("created_at")),
                    "Event": str(event),
                    "Recording": row.get("filename") or "recording",
                }
            )
    rows.sort(key=lambda item: item["Mentioned"] or datetime.min, reverse=True)
    return rows


def _timeline_rows(data: dict) -> list[dict]:
    # The API lists newest first; reverse so ties keep pipeline order after sorting.
    events = sorted(
        reversed(data.get("events") or []),
        key=lambda event: (
            _parse_time(event.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
        ),
    )
    rows = []
    for event in events:
        took = event.get("duration_ms")
        stage = str(event.get("stage") or "")
        rows.append(
            {
                "Time": _local_time(event.get("created_at")),
                "Stage": _STEP_LABEL.get(stage, _nice(stage)) if stage else "—",
                "Step": str(event.get("message") or ""),
                "Took": f"{float(took) / 1000:.1f} s" if took is not None else "—",
                "Level": "⚠️ Error" if event.get("level") == "error" else "Info",
            }
        )
    return rows


def _topic_groups(taxonomy: dict | None) -> list[tuple[str, list[str]]]:
    taxonomy = taxonomy or {}
    return [(label, [str(item) for item in taxonomy.get(key) or []]) for key, label in _TOPIC_KEYS]


def _layer2_result(option: str, value: dict) -> tuple[str, str]:
    if value.get("skipped"):
        reason = str(value["skipped"])
        return _SKIP_TEXT.get(reason, _nice(reason)), "⏭️ Skipped"
    if option == "sentiment_lexicon":
        text = (
            f"{_nice(value.get('label'))} · {value.get('positive', 0)} positive, "
            f"{value.get('negative', 0)} negative words"
        )
    elif option == "speaking_pace":
        text = f"{value.get('words_per_minute', 0)} words/min · {value.get('word_count', 0)} words"
    elif option == "rms_energy":
        text = f"Mean {value.get('rms_mean', 0)} · peak {value.get('rms_peak', 0)}"
    elif option == "pos_counts":
        text = f"{value.get('noun_count', 0)} nouns · {value.get('adjective_count', 0)} adjectives"
    else:
        text = ", ".join(f"{key}: {item}" for key, item in value.items() if key != "windows")
    return text or "—", "✅ Pass"


def _check_rows(record: dict) -> list[dict]:
    status = str(record.get("status") or "")
    blocked = status == "blocked"
    speech = _has_speech(record)
    rows = [
        {"Check": "Upload saved", "Result": "Stored and queued", "Status": "✅ Pass"},
    ]
    if "transcript" in record:
        rows.append(
            {
                "Check": "Transcription",
                "Result": "Transcript ready" if speech else "No speech detected",
                "Status": "✅ Pass" if speech else "⚠️ Empty",
            }
        )
    rows.append(
        {
            "Check": "Content safety",
            "Result": str(record.get("block_reason") or "No issues found"),
            "Status": "⛔ Blocked" if blocked else "✅ Pass",
        }
    )
    if blocked:
        rows.append({"Check": "Layer 1 summary", "Result": "Not run", "Status": "⏭️ Skipped"})
    elif status == "failed":
        rows.append(
            {
                "Check": "Layer 1 summary",
                "Result": str(record.get("error_message") or "Processing failed"),
                "Status": "❌ Failed",
            }
        )
    elif record.get("summary"):
        rows.append(
            {
                "Check": "Layer 1 summary & topics",
                "Result": "Schema valid",
                "Status": "✅ Pass",
            }
        )
    for option, value in (record.get("layer2") or {}).items():
        if not isinstance(value, dict):
            continue
        result, state = _layer2_result(option, value)
        rows.append(
            {
                "Check": f"Layer 2 · {_LAYER2_LABEL.get(option, _nice(option))}",
                "Result": result,
                "Status": state,
            }
        )
    return rows


# ---------------------------------------------------------------- output widgets


def _section(title: str) -> None:
    st.markdown(f'<div class="va-sec">{html.escape(title)}</div>', unsafe_allow_html=True)


def _empty(title: str, hint: str = "", icon: str = "&#9675;") -> None:
    st.markdown(
        f'<div class="va-empty"><div class="ico">{icon}</div><b>{html.escape(title)}</b>'
        + (f"<small>{html.escape(hint)}</small>" if hint else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def _metrics(key: str, values: list[tuple[str, str]]) -> None:
    with st.container(key=f"va-metrics-{key}", horizontal=True, gap="small"):
        for label, value in values:
            st.metric(label, value, border=True)


def _table(rows: list[dict], column_config: dict | None = None, height="auto") -> None:
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        height=height,
        column_config=column_config or {},
    )


def _bar_chart(
    rows: list[dict],
    category: str,
    value: str,
    title: str,
    colors: dict[str, str] | None = None,
    keep_order: bool = False,
) -> None:
    pairs = [(str(row[category]), int(row[value] or 0)) for row in rows]
    if not keep_order:
        pairs.sort(key=lambda pair: (-pair[1], pair[0].lower()))
    frame = pd.DataFrame(
        {"label": [name for name, _count in pairs], "count": [n for _l, n in pairs]}
    )
    order = list(frame["label"])
    peak = max([count for _name, count in pairs] + [1])
    y = alt.Y(
        "label:N",
        sort=order,
        title=None,
        axis=alt.Axis(labelLimit=220, labelFontSize=12, ticks=False, domain=False, labelPadding=8),
    )
    # Values are printed at the end of each bar, so the x axis is left off.
    x = alt.X("count:Q", axis=None, scale=alt.Scale(domain=[0, peak * 1.15]))
    if colors:
        color = alt.Color(
            "label:N",
            scale=alt.Scale(domain=order, range=[colors.get(item, _INDIGO) for item in order]),
            legend=None,
        )
    else:
        color = alt.value(_INDIGO)
    base = alt.Chart(frame)
    bars = base.mark_bar(cornerRadiusEnd=5, size=16).encode(
        x=x,
        y=y,
        color=color,
        tooltip=[alt.Tooltip("label:N", title=category), alt.Tooltip("count:Q", title=title)],
    )
    labels = base.mark_text(align="left", dx=6, color="#5b6270", fontSize=12).encode(
        x=x, y=y, text="count:Q"
    )
    height = 28 * len(frame) + 12
    chart = (bars + labels).properties(height=height).configure_view(stroke=None)
    st.altair_chart(chart, width="stretch", height=height)


def _trend_chart(rows: list[dict], category: str, value: str, title: str) -> None:
    frame = pd.DataFrame(
        {"label": [row[category] for row in rows], "count": [row[value] for row in rows]}
    )
    x = alt.X("label:O", title=None, sort=None, axis=alt.Axis(labelAngle=0))
    y = alt.Y("count:Q", title=title, axis=alt.Axis(tickMinStep=1, format="d"))
    base = alt.Chart(frame).encode(x=x, y=y, tooltip=["label:O", "count:Q"])
    chart = (
        (
            base.mark_area(color=_INDIGO, opacity=0.12, line={"color": _INDIGO})
            + base.mark_point(color=_INDIGO, filled=True, size=60)
        )
        .properties(height=190)
        .configure_view(stroke=None)
    )
    st.altair_chart(chart, width="stretch", height=240)


# ---------------------------------------------------------------- output renderers


def _render_rollup(data: dict, key: str) -> None:
    rows = _rollup_rows(data)
    total = int(data.get("file_count") or 0)
    if not total or not rows:
        _empty(
            "No completed recordings in this range",
            "Upload a recording or widen the date range, then ask again.",
        )
        return
    group_by = str(data.get("group_by") or "user")
    label = _GROUP_LABEL.get(group_by, "Group")
    stats = [("Recordings", str(total))]
    if group_by != "user":
        stats.append((f"{label} groups", str(len(rows))))
        top = max(rows, key=lambda row: row["Recordings"])
        stats.append((f"Top {label.lower()}", str(top[label])))
    _metrics(key, stats)
    if data.get("overall_summary"):
        _section("Overview")
        st.markdown(
            f'<div class="va-out">{_prose_html(str(data["overall_summary"]))}</div>',
            unsafe_allow_html=True,
        )
    if group_by == "user":
        return
    if len(rows) > 1:
        _section(f"Recordings per {label.lower()}")
        if group_by == "week":
            _trend_chart(rows, label, "Recordings", "Recordings")
        else:
            colors = _SENTIMENT_COLOR if group_by == "sentiment" else None
            if colors:
                colors = {_nice(name): color for name, color in colors.items()}
            _bar_chart(rows, label, "Recordings", "Recordings", colors=colors)
    _section("Breakdown")
    share_help = "Share of the recordings in range."
    if group_by == "taxonomy_label":
        share_help += " A recording can carry several topics, so shares can add past 100%."
    _table(
        rows,
        {
            label: st.column_config.TextColumn(label),
            "Recordings": st.column_config.NumberColumn("Recordings", format="%d"),
            "Share": st.column_config.ProgressColumn(
                "Share",
                format="%.0f%%",
                min_value=0,
                max_value=100,
                help=share_help,
            ),
            "Files": st.column_config.TextColumn("Files"),
            "Summary": st.column_config.TextColumn("Summary", width="large"),
        },
    )


def _render_files(data: dict, key: str) -> None:
    rows = data.get("rows") or []
    if not rows:
        _empty("No recordings matched", "Try a wider date range or a different topic.")
        return
    completed = sum(1 for row in rows if row.get("status") == "completed")
    attention = sum(1 for row in rows if row.get("status") in {"blocked", "failed"})
    seconds = sum(float(row.get("duration_sec") or 0) for row in rows)
    stats = [
        ("Recordings", str(data.get("total") or len(rows))),
        ("Completed", str(completed)),
        ("Total audio", _duration_text(seconds)),
    ]
    if attention:
        stats.append(("Needs attention", str(attention)))
    _metrics(key, stats)
    topics = Counter(topic for row in rows for topic in row.get("topics") or [])
    if len(topics) > 1:
        _section("Top topics")
        top = [{"Topic": name, "Recordings": count} for name, count in topics.most_common(8)]
        _bar_chart(top, "Topic", "Recordings", "Recordings")
    _section("Recordings")
    _table(
        _file_rows(data),
        {
            "Recording": st.column_config.TextColumn("Recording"),
            "Status": st.column_config.TextColumn("Status"),
            "Duration": st.column_config.TextColumn("Duration"),
            "Uploaded": st.column_config.DatetimeColumn(
                "Uploaded", format="MMM D, YYYY h:mm a", help=_zone_note()
            ),
            "Topics": st.column_config.TextColumn("Topics"),
            "Summary": st.column_config.TextColumn("Summary", width="large"),
        },
    )
    shown = len(rows)
    total = int(data.get("total") or shown)
    if total > shown:
        st.caption(f"Showing the {shown} most recent of {total} recordings.")


def _render_events(data: dict, key: str) -> None:
    rows = _event_rows(data)
    if not rows:
        _empty(
            "No upcoming events mentioned",
            "None of the matching recordings mention a future plan or date.",
        )
        return
    _metrics(
        key,
        [
            ("Events", str(len(rows))),
            ("Recordings", str(len({row["Recording"] for row in rows}))),
        ],
    )
    _table(
        rows,
        {
            "Mentioned": st.column_config.DatetimeColumn(
                "Mentioned", format="MMM D, YYYY h:mm a", help=_zone_note()
            ),
            "Event": st.column_config.TextColumn("Event"),
            "Recording": st.column_config.TextColumn("Recording"),
        },
    )


def _analysis_facts(record: dict, clip: tuple[bytes, str] | None) -> list[tuple[str, str, str]]:
    """Labeled facts for the horizontal strip. Tone is ok, bad, warn, or empty."""
    layer2 = record.get("layer2") or {}
    status = str(record.get("status") or "")
    facts: list[tuple[str, str, str]] = []
    if status == "failed" and record.get("stage"):
        stage = str(record["stage"])
        facts.append(("Status", f"Failed at {_STEP_LABEL.get(stage, _nice(stage)).lower()}", "bad"))
    elif status:
        tone = "ok" if status == "completed" else "bad" if status in {"failed", "blocked"} else ""
        facts.append(("Status", _STATUS_TEXT.get(status, _nice(status)), tone))
    # The server stores 0 when it could not measure the audio (it only reads WAV);
    # fall back to the file header, and leave the duration out rather than show "0:00".
    seconds = float(record.get("duration_sec") or 0)
    if seconds <= 0:
        seconds = _clip_seconds(clip) or 0
    if seconds > 0:
        facts.append(("Duration", _duration_text(seconds), ""))
    words = (layer2.get("speaking_pace") or {}).get("word_count")
    if words is None and "transcript" in record:
        words = len(str(record.get("transcript") or "").split())
    if words is not None:
        facts.append(("Words", str(words), ""))
    sentiment = (layer2.get("sentiment_lexicon") or {}).get("label")
    topics = len(_subject_labels(record.get("taxonomy")))
    if sentiment:
        tone = {"positive": "ok", "negative": "bad"}.get(str(sentiment).lower(), "")
        facts.append(("Sentiment", _nice(sentiment), tone))
    elif topics:
        facts.append(("Topics", str(topics), ""))
    if record.get("byte_size"):
        facts.append(("Size", _size_text(record["byte_size"]), ""))
    uploaded = _local_time(record.get("created_at"))
    if uploaded:
        facts.append(("Uploaded", uploaded.strftime("%b %d %H:%M"), ""))
    if record.get("summary_strategy"):
        facts.append(("Summary", _nice(record["summary_strategy"]), ""))
    return [(label, value, tone) for label, value, tone in facts if value]


def _facts_html(facts: list[tuple[str, str, str]]) -> str:
    chips = []
    for label, value, tone in facts:
        klass = f"va-fact {tone}".strip()
        chips.append(
            f'<span class="{klass}"><small>{html.escape(label)}</small>'
            f"<b>{html.escape(value)}</b></span>"
        )
    return '<div class="va-facts">' + "".join(chips) + "</div>"


def _render_analysis(api: ApiClient, panel: dict, key: str) -> None:
    record = panel.get("data") or {}
    speech = _has_speech(record)
    file_id = panel.get("audio")
    clip = _audio(api, str(file_id)) if file_id else None
    facts = _analysis_facts(record, clip)
    if facts:
        st.markdown(_facts_html(facts), unsafe_allow_html=True)
    if record.get("block_reason"):
        st.warning(
            f"Blocked by the safety check: {record['block_reason']}", icon=":material/block:"
        )
    if record.get("error_message"):
        st.error(str(record["error_message"]), icon=":material/error:")
    has_transcript_field = "transcript" in record
    if has_transcript_field and not speech and record.get("status") == "completed":
        _empty(
            "No speech detected",
            "The recording was processed, but the transcript is empty, so there is nothing "
            "to summarize. Play it below to double-check.",
            icon="&#128263;",
        )
    elif record.get("summary"):
        _section("Summary")
        st.markdown(
            f'<div class="va-out">{_prose_html(str(record["summary"]))}</div>',
            unsafe_allow_html=True,
        )
    if clip or speech:
        _section("Transcript" if speech else "Audio")
    if clip:
        st.audio(clip[0], format=clip[1])
    if speech:
        st.markdown(
            f'<div class="va-transcript">{_text_html(str(record["transcript"]))}</div>',
            unsafe_allow_html=True,
        )


def _render_topics(data: dict) -> None:
    groups = _topic_groups(data.get("taxonomy"))
    if not any(items for _label, items in groups):
        _empty(
            "No topics or upcoming events found",
            "Topics appear once a recording has enough speech to classify.",
        )
        return
    rows = []
    for label, items in groups:
        chips = "".join(f'<span class="va-tag">{html.escape(item)}</span>' for item in items)
        if not chips:
            chips = '<span class="va-none">None</span>'
        rows.append(
            f'<div class="va-taxrow"><div class="cat">{html.escape(label)}'
            f'<small>{len(items)}</small></div><div class="tags">{chips}</div></div>'
        )
    st.markdown('<div class="va-tax">' + "".join(rows) + "</div>", unsafe_allow_html=True)


def _short_num(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    text = f"{number:.2f}" if abs(number) < 10 else f"{number:.0f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _sparkline(values: list[float]) -> str:
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    width, height = 160, 26
    last = len(values) - 1
    points = []
    for index, value in enumerate(values):
        x = index / last * width
        y = height - 2 - ((value - low) / span) * (height - 4)
        points.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg class="va-spark" viewBox="0 0 {width} {height}" aria-hidden="true">'
        '<polyline fill="none" stroke="#b7c0ea" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{" ".join(points)}"/>'
        "</svg>"
    )


def _word_line(items: object) -> str:
    words = [
        str(item.get("lemma"))
        for item in items or []
        if isinstance(item, dict) and item.get("lemma")
    ]
    return ", ".join(words[:5])


def _analytics_view(data: dict) -> tuple[list[str], str]:
    """Problems worth a sentence, then one quiet line per measure."""
    notes = []
    for row in _check_rows(data):
        check = str(row["Check"])
        if check.startswith("Layer 2"):
            continue
        if "Pass" in str(row["Status"]):
            continue
        notes.append(f"{check}: {row['Result']}")
    layer2 = data.get("layer2") or {}
    rows: list[tuple[str, str, str, str, str]] = []
    sentiment = layer2.get("sentiment_lexicon") or {}
    if sentiment and not sentiment.get("skipped"):
        label = str(sentiment.get("label") or "").lower()
        tone = {"positive": "ok", "negative": "bad"}.get(label, "")
        positive = sentiment.get("positive", 0)
        negative = sentiment.get("negative", 0)
        detail = f"{positive} positive · {negative} negative"
        rows.append(("Sentiment", _nice(sentiment.get("label")), detail, tone, ""))
    pace = layer2.get("speaking_pace") or {}
    if pace and not pace.get("skipped"):
        rows.append(
            (
                "Pace",
                f"{_short_num(pace.get('words_per_minute'))} wpm",
                f"{pace.get('word_count', 0)} words",
                "",
                "",
            )
        )
    energy = layer2.get("rms_energy") or {}
    if energy and not energy.get("skipped"):
        windows = [float(item) for item in energy.get("windows") or []]
        rows.append(
            (
                "Loudness",
                f"{_short_num(energy.get('rms_mean'))} mean",
                f"peak {_short_num(energy.get('rms_peak'))}",
                "",
                _sparkline(windows),
            )
        )
    counts = layer2.get("pos_counts") or {}
    if counts and not counts.get("skipped"):
        nouns = _word_line(counts.get("top_nouns"))
        adjectives = _word_line(counts.get("top_adjectives"))
        detail = f"Adjectives {adjectives}" if adjectives else ""
        rows.append(("Words", nouns or "—", detail, "", ""))
    return notes, _read_html(rows)


def _read_html(rows: list[tuple[str, str, str, str, str]]) -> str:
    if not rows:
        return ""
    body = []
    for label, value, detail, tone, extra in rows:
        klass = f"v {tone}".strip()
        note = f"<small>{html.escape(detail)}</small>" if detail else ""
        body.append(
            f'<div class="row"><span class="k">{html.escape(label)}</span>'
            f'<span class="{klass}">{html.escape(value)}</span>{note}{extra}</div>'
        )
    return '<div class="va-read">' + "".join(body) + "</div>"


def _render_checks(data: dict, key: str) -> None:
    del key
    notes, body = _analytics_view(data)
    if notes:
        text = "<br>".join(html.escape(note) for note in notes)
        st.markdown(f'<div class="va-quiet">{text}</div>', unsafe_allow_html=True)
    if body:
        st.markdown(body, unsafe_allow_html=True)
        return
    st.markdown(
        '<div class="va-quiet">No extra measures for this recording.</div>',
        unsafe_allow_html=True,
    )


def _result_line(name: str, result: dict) -> str:
    if result.get("error"):
        return f"Error: {result['error']}"
    if name == "search_files":
        total = result.get("total")
        if total is None:
            total = len(result.get("items") or [])
        return f"{total} recording(s) matched"
    if name == "get_analysis":
        filename = result.get("filename") or "recording"
        return f"{filename} · {result.get('status') or 'loaded'}"
    if name == "run_summary":
        return f"{result.get('file_count', 0)} file(s) grouped by {result.get('group_by', 'user')}"
    return "done"


def _step_records(tool_calls: list[dict]) -> list[dict]:
    records = []
    for call in tool_calls:
        name = str(call.get("name") or "")
        if not name:
            continue
        result = call.get("result") or {}
        records.append(
            {
                "name": name,
                "arguments": call.get("arguments") or {},
                "summary": _result_line(name, result),
                "error": bool(result.get("error")),
                "raw": result,
            }
        )
    return records


def _steps_html(steps: list[dict]) -> str:
    names = ", ".join(step["name"] for step in steps)
    rows = [
        '<div class="va-step"><span class="dot"></span><div><b>Planned the answer</b>'
        f"<small>Chose {html.escape(names)}</small></div></div>"
    ]
    for step in steps:
        args = ", ".join(f"{key}={value}" for key, value in step["arguments"].items())
        klass = "va-step warn" if step["error"] else "va-step"
        label = _TOOL_LABEL.get(step["name"], step["name"])
        rows.append(
            f'<div class="{klass}"><span class="dot"></span><div><b>{html.escape(label)}</b>'
            f"<small><code>{html.escape(step['name'])}({html.escape(args)})</code></small>"
            f"<small>{html.escape(step['summary'])}</small></div></div>"
        )
    rows.append(
        '<div class="va-step"><span class="dot"></span><div><b>Wrote the reply</b>'
        "<small>Answer grounded in the tool results above</small></div></div>"
    )
    return '<div class="va-steps">' + "".join(rows) + "</div>"


# ---------------------------------------------------------------- state


def _fresh_state() -> None:
    st.session_state["assistant_messages"] = []
    st.session_state["assistant_phase"] = "ready"
    st.session_state["assistant_watch"] = []
    st.session_state["assistant_reported"] = []
    st.session_state["assistant_pending"] = []
    st.session_state["assistant_run"] = None
    st.session_state["assistant_show_uploader"] = False
    st.session_state["assistant_polls"] = 0
    st.session_state["assistant_upload_nonce"] = (
        st.session_state.get("assistant_upload_nonce", 0) + 1
    )


def _ensure_state() -> None:
    if "assistant_messages" not in st.session_state:
        _fresh_state()
        st.session_state["assistant_audio"] = {}
    # The live log has its own column now, so it starts switched on.
    st.session_state.setdefault("show_live_log", True)
    # "Show activity" preference. setdefault only writes the first time, so a value the
    # user picked is kept on every rerun (and when they visit Batch Process and come back).
    st.session_state.setdefault("show_activity", True)


def _new_chat() -> None:
    _fresh_state()


def _to_classic() -> None:
    # "Classic" is the internal mode name; the screen is labelled "Batch Process".
    st.session_state["ui_mode"] = "Classic"


def _log_out() -> None:
    st.session_state.clear()


def _say(
    role: str,
    content: str,
    tools: list[str] | None = None,
    artifacts: list[dict] | None = None,
    steps: list[dict] | None = None,
    files: list[str] | None = None,
) -> None:
    st.session_state["assistant_messages"].append(
        {
            "role": role,
            "content": content,
            "tools": tools or [],
            "artifacts": artifacts or [],
            "steps": steps or [],
            "files": files or [],
            "at": datetime.now(_viewer_zone()).strftime("%H:%M:%S"),
        }
    )


def _history_for_api() -> list[dict]:
    turns = []
    for message in st.session_state.get("assistant_messages") or []:
        content = (message.get("content") or "").strip()
        if message.get("files"):
            names = ", ".join(message["files"])
            content = f"{content} (attached: {names})".strip()
        if message.get("role") in {"user", "assistant"} and content:
            turns.append({"role": message["role"], "content": content[:2000]})
    if turns and turns[-1]["role"] == "user":
        turns = turns[:-1]
    return turns[-8:]


def _queue(text: str) -> None:
    text = text.strip()
    if not text:
        return
    _say("user", text)
    st.session_state["assistant_run"] = text


def _attach(files: list[tuple[str, bytes]], note: str = "") -> None:
    if not files:
        return
    names = [name for name, _data in files]
    _say("user", note.strip(), files=names)
    st.session_state["assistant_show_uploader"] = False
    st.session_state["assistant_upload_nonce"] = (
        st.session_state.get("assistant_upload_nonce", 0) + 1
    )
    if len(files) > _MAX_FILES:
        _say("assistant", f"Attach at most {_MAX_FILES} files at a time.")
        return
    st.session_state["assistant_pending"] = files
    _say(
        "assistant",
        f"Got {len(files)} file(s). Pick the Layer 2 options below and I'll start processing. "
        "Leave them all off to run Layer 1 only.",
    )
    st.session_state["assistant_phase"] = "options"


def _submit(value) -> None:
    if value is None:
        return
    if isinstance(value, str):
        _queue(value)
        return
    text = str(getattr(value, "text", "") or "")
    uploads = list(getattr(value, "files", None) or [])
    if uploads:
        _attach([(item.name, item.getvalue()) for item in uploads], text)
    else:
        _queue(text)


def _composer(key: str) -> None:
    value = st.chat_input(
        "Ask about your calls, or attach a recording…",
        key=key,
        accept_file="multiple",
        file_type=_AUDIO_TYPES,
    )
    if value is not None:
        _submit(value)
        st.rerun()


# ---------------------------------------------------------------- rendering


def _sync_activity(widget_key: str) -> None:
    # on_change callbacks run before the rerun, so the new choice is saved first.
    st.session_state["show_activity"] = st.session_state[widget_key]


def _activity_toggle(widget_key: str) -> None:
    # Two toggles (header on wide screens, menu on phones) share one setting. Widget
    # state is dropped when a widget is not drawn (e.g. on the Batch Process pages), so the
    # choice lives in the plain "show_activity" key and each toggle is seeded from it.
    st.session_state[widget_key] = st.session_state["show_activity"]
    st.toggle(
        "Show activity",
        key=widget_key,
        on_change=_sync_activity,
        args=(widget_key,),
        help="Show or hide the activity and log column next to the chat",
    )


def _header(meta: dict | None, messages: list[dict]) -> None:
    provider = ""
    if meta:
        provider = "mock providers" if meta.get("llm_provider") == "mock" else "live models"
    status = "Online · connected to your recordings"
    if provider:
        status += f'<span class="va-provider"> · {html.escape(provider)}</span>'
    with st.container(key="va-header", horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown(
            f'<div class="va-head"><div class="va-mark">{_mark(22)}</div><div>'
            '<div class="va-title">Voice Analytics Agent</div>'
            f'<div class="va-status"><span class="va-live"></span>{status}</div>'
            "</div></div>",
            unsafe_allow_html=True,
            width="stretch",
        )
        st.button(
            "New chat",
            key="va-new-chat",
            icon=":material/edit_square:",
            on_click=_new_chat,
            disabled=not messages,
        )
        st.button(
            "Batch Process",
            key="va-classic",
            type="tertiary",
            icon=":material/view_list:",
            help="Switch to Batch Process: library, upload, prompts, and rollup pages",
            on_click=_to_classic,
        )
        _activity_toggle("va-activity")
        with st.popover("", icon=":material/more_horiz:", key="va-menu"):
            # Shown only on small screens (CSS), where the header buttons are hidden.
            st.button(
                "New chat",
                key="va-menu-new",
                icon=":material/edit_square:",
                on_click=_new_chat,
                disabled=not messages,
                width="stretch",
            )
            st.button(
                "Batch Process",
                key="va-menu-classic",
                icon=":material/view_list:",
                on_click=_to_classic,
                width="stretch",
            )
            _activity_toggle("va-menu-activity")
            # The live log sits inside the activity column, so it has no effect when hidden.
            st.toggle(
                "Live log", key="show_live_log", disabled=not st.session_state["show_activity"]
            )
            st.download_button(
                "Export conversation",
                data=_export_document(messages),
                file_name="conversation.html",
                mime="text/html",
                icon=":material/download:",
                disabled=not messages,
                width="stretch",
            )
            st.button(
                "Log out",
                key="va-logout",
                icon=":material/logout:",
                on_click=_log_out,
                width="stretch",
            )


def _user_bubble(message: dict) -> str:
    files = "".join(
        f'<span class="va-file">&#128206; {html.escape(name)}</span>'
        for name in message.get("files") or []
    )
    content = _text_html(message.get("content") or "")
    if files and content:
        content = files + "<br>" + content
    elif files:
        content = files
    return f'<div class="va-row user"><div class="va-bubble">{content}</div></div>'


def _assistant_bubble(message: dict) -> str:
    return (
        f'<div class="va-row assistant"><div class="va-av">{_mark(15)}</div>'
        f'<div class="va-bubble">{_prose_html(message.get("content") or "")}</div></div>'
    )


def _chips_html(tools: list[str]) -> str:
    if not tools:
        return ""
    chips = "".join(f'<span class="va-chip">{html.escape(name)}</span>' for name in tools)
    return f'<div class="va-chips">{chips}</div>'


def _render_panel_body(api: ApiClient, panel: dict, key: str) -> None:
    kind = panel.get("kind")
    data = panel.get("data") or {}
    if kind == "rollup":
        _render_rollup(data, key)
    elif kind == "files":
        _render_files(data, key)
    elif kind == "events":
        _render_events(data, key)
    elif kind == "analysis":
        _render_analysis(api, panel, key)
    elif kind == "topics":
        _render_topics(data)
    elif kind == "checks":
        _render_checks(data, key)
    elif kind == "timeline":
        st.markdown(_timeline_log_html(data), unsafe_allow_html=True)
    elif panel.get("body"):
        st.markdown(f'<div class="va-out">{panel["body"]}</div>', unsafe_allow_html=True)


def _panel_title(panel: dict) -> str:
    kind = str(panel.get("kind") or "")
    return _PANEL_TITLE.get(kind) or str(panel.get("title") or "Output")


def _render_panel(api: ApiClient, panel: dict, key: str, expanded: bool) -> None:
    title = _panel_title(panel)
    icon = _PANEL_ICON.get(str(panel.get("kind")), ":material/article:")
    with st.expander(title, expanded=expanded, key=key, icon=icon):
        _render_panel_body(api, panel, key)


def _render_turn(api: ApiClient, index: int, message: dict) -> None:
    if message.get("role") == "user":
        st.markdown(_user_bubble(message), unsafe_allow_html=True)
        return
    # The chat column shows only the reply and its structured outputs; agent steps,
    # tool labels, and processing timelines go to the activity column.
    with st.container(key=f"va-turn-{index}"):
        st.markdown(_assistant_bubble(message), unsafe_allow_html=True)
        panels = [
            panel for panel in message.get("artifacts") or [] if panel.get("kind") not in _LOG_KINDS
        ]
        for number, panel in enumerate(panels):
            _render_panel(api, panel, f"va-out-{index}-{number}", expanded=number == 0)


def _empty_state(sample_path: Path) -> None:
    st.markdown(
        f'<div class="va-hero"><div class="va-mark">{_mark(30)}</div>'
        "<h1>What should we look into?</h1>"
        "<p>Ask about your recorded calls, or attach a recording to analyze it.</p></div>",
        unsafe_allow_html=True,
    )
    with st.container(key="va-hero-composer"):
        _composer("va-composer-hero")
    with st.container(key="va-chips", horizontal=True, horizontal_alignment="center", gap="small"):
        for label, icon, prompt in _SUGGESTIONS:
            if st.button(label, key=f"va-suggest-{label}", icon=icon):
                if prompt is None:
                    st.session_state["assistant_show_uploader"] = True
                else:
                    _queue(prompt)
                st.rerun()
    if st.session_state.get("assistant_show_uploader"):
        _uploader_card(sample_path)


def _uploader_card(sample_path: Path) -> None:
    nonce = st.session_state.get("assistant_upload_nonce", 0)
    with st.container(key=f"va-card-upload-{nonce}"):
        st.markdown('<div class="va-card-title">Upload recordings</div>', unsafe_allow_html=True)
        uploads = st.file_uploader(
            "Audio files (wav, mp3, m4a, ogg, flac · up to 10, 20 MB each)",
            type=_AUDIO_TYPES,
            accept_multiple_files=True,
            key=f"va-upload-{nonce}",
        )
        with st.container(horizontal=True, gap="small"):
            if st.button("Attach", key="va-attach", type="primary", disabled=not uploads):
                _attach([(item.name, item.getvalue()) for item in uploads or []])
                st.rerun()
            if sample_path.exists() and st.button("Use bundled sample", key="va-sample"):
                _attach([(sample_path.name, sample_path.read_bytes())])
                st.rerun()
            if st.button("Close", key="va-close-upload", type="tertiary"):
                st.session_state["assistant_show_uploader"] = False
                st.rerun()


def _options_card(api: ApiClient) -> None:
    try:
        options = api.prompt_options()
        saved = {
            row["option_id"]: row.get("params") or {} for row in api.get_config()["selections"]
        }
    except ApiError as exc:
        st.error(exc.detail)
        return
    with st.container(key="va-card-options"):
        st.markdown('<div class="va-card-title">Layer 2 options</div>', unsafe_allow_html=True)
        with st.form("va-options", border=False):
            selections = []
            for option in options:
                enabled = st.checkbox(
                    option["label"], value=option["id"] in saved, help=option["description"]
                )
                params = {name: spec["default"] for name, spec in option["params"].items()}
                params.update(saved.get(option["id"]) or {})
                if enabled:
                    selections.append({"option_id": option["id"], "params": params})
            with st.container(horizontal=True, gap="small"):
                submitted = st.form_submit_button("Start processing", type="primary")
                cancelled = st.form_submit_button("Cancel", type="tertiary")
    if cancelled:
        st.session_state["assistant_pending"] = []
        st.session_state["assistant_phase"] = "ready"
        _say("assistant", "Okay, I won't upload those.")
        st.rerun()
    if not submitted:
        return
    try:
        api.save_config(selections)
        created = api.upload(st.session_state.get("assistant_pending") or [])
    except ApiError as exc:
        st.error(exc.detail)
        return
    st.session_state["assistant_pending"] = []
    items = created.get("items") or []
    st.session_state["assistant_watch"] = [item["id"] for item in items]
    st.session_state["assistant_reported"] = []
    names = ", ".join(item["original_filename"] for item in items)
    _say(
        "assistant",
        f"Processing has started for {len(items)} file(s): {names}.",
        tools=["Uploaded", "Queued analysis"],
    )
    st.session_state["assistant_phase"] = "processing"
    st.session_state["assistant_polls"] = 0
    st.session_state["va_proc_open"] = False
    st.rerun()


def _step_class(key: str, item: dict | None) -> str:
    if item is None:
        return "pending"
    if key in set(item.get("skipped_stages") or []):
        return "skipped"
    stage = item.get("stage") or "upload"
    if stage not in _STEP_INDEX:
        stage = "upload"
    status = item.get("status") or ""
    if status == "failed" and key == stage:
        return "failed"
    if status in {"completed", "blocked"} or stage == "saved":
        return "done"
    if _STEP_INDEX[key] < _STEP_INDEX[stage]:
        return "done"
    if key == stage:
        return "current"
    return "pending"


def _flow_html(item: dict) -> str:
    classes = [_step_class(key, item) for key, _label in _STEPS]
    name = html.escape(str(item.get("original_filename") or "Recording"))
    chunks = [
        f'<div class="va-flow-name">{name}</div>',
        '<div class="va-flow">',
    ]
    for index, ((_key, label), klass) in enumerate(zip(_STEPS, classes, strict=True)):
        if index:
            done = klass == "done" and classes[index - 1] == "done"
            chunks.append(f'<div class="conn{" done" if done else ""}"></div>')
        chunks.append(f'<div class="step {klass}">{html.escape(label)}</div>')
    chunks.append("</div>")
    return "".join(chunks)


def _status_label(item: dict) -> str:
    stage = item.get("stage") or "upload"
    if item.get("status") == "failed":
        return f"{_STEP_LABEL.get(stage, stage)} failed"
    if item.get("status") in {"completed", "blocked"}:
        return "Saved"
    return _STEP_LABEL.get(stage, stage)


def _watched_items(items: list[dict]) -> list[dict]:
    by_id = {item["id"]: item for item in items}
    return [
        by_id[file_id]
        for file_id in st.session_state.get("assistant_watch") or []
        if file_id in by_id
    ]


def _processing_label(watched: list[dict]) -> str:
    if not watched:
        return "Processing · waiting for the upload to register"
    if len(watched) == 1:
        return f"Processing · {_status_label(watched[0])}"
    done = sum(1 for item in watched if item.get("status") in _TERMINAL)
    return f"Processing · {done} of {len(watched)} done"


def _processing_card(items: list[dict], events: list[dict]) -> None:
    """Activity column: the stage sequence, current step, and the latest log lines."""
    watched = _watched_items(items)
    with st.container(key="va-log-processing"):
        with st.status(_processing_label(watched), state="running", expanded=True):
            st.markdown("".join(_flow_html(item) for item in watched), unsafe_allow_html=True)
            names = {str(item.get("original_filename")) for item in watched}
            recent = [event for event in events if str(event.get("filename") or "") in names][:4]
            if recent:
                lines = "<br>".join(
                    html.escape(str(event.get("message") or "")) for event in recent
                )
                st.markdown(f'<div class="va-events">{lines}</div>', unsafe_allow_html=True)


def _processing_detail(target, watched: list[dict], events: list[dict]) -> None:
    """Stage row and the latest log lines, drawn into the compact status body."""
    if not watched:
        target.markdown(
            '<div class="va-events">Waiting for the upload to register.</div>',
            unsafe_allow_html=True,
        )
        return
    target.markdown("".join(_flow_html(item) for item in watched), unsafe_allow_html=True)
    names = {str(item.get("original_filename")) for item in watched}
    recent = [event for event in events if str(event.get("filename") or "") in names][:4]
    if not recent:
        return
    lines = "<br>".join(html.escape(str(event.get("message") or "")) for event in recent)
    target.markdown(f'<div class="va-events">{lines}</div>', unsafe_allow_html=True)


def _processing_status(items: list[dict], events: list[dict]) -> None:
    """Chat, activity hidden: one compact line. The chevron opens the stages.

    The compact panel's open body is clipped to nothing, so the stage row is
    drawn under the line once the chevron has been opened. The keyed expander
    remembers that across the one-second refresh.
    """
    watched = _watched_items(items)
    state = "running"
    if watched and all(item.get("status") in _TERMINAL for item in watched):
        state = "error" if any(item.get("status") == "failed" for item in watched) else "complete"
    elif any(item.get("status") == "failed" for item in watched):
        state = "error"
    icons = {
        "running": ":material/progress_activity:",
        "complete": ":material/check:",
        "error": ":material/error:",
    }
    # Running uses va-proc-status so the icon spins. The other states share the
    # same prefix and pick up the line styling without the spin.
    shell = "va-proc-status" if state == "running" else "va-proc-status-still"
    opened = bool(st.session_state.get("va_proc_open", False))
    with st.container(key=shell):
        st.expander(
            _processing_label(watched),
            expanded=False,
            key="va_proc_open",
            type="compact",
            on_change="rerun",
            icon=icons[state],
        )
        if opened:
            with st.container(key="va-proc-detail"):
                _processing_detail(st, watched, events)


def _typing(label: str = "Thinking") -> str:
    return (
        f'<div class="va-row assistant"><div class="va-av">{_mark(15)}</div>'
        f'<div class="va-typing"><b>{html.escape(label)}</b><i></i><i></i><i></i></div></div>'
    )


def _log_html(events: list[dict]) -> str:
    if not events:
        return '<div class="va-log"><div class="line">No events yet.</div></div>'
    rows = []
    for event in events[:300]:
        local = _local_time(event.get("created_at"))
        stamp = local.strftime("%m-%d %H:%M:%S") if local else ""
        name = str(event.get("filename") or "")
        message = str(event.get("message") or "")
        klass = "line err" if event.get("level") == "error" else "line"
        rows.append(
            f'<div class="{klass}"><b>{html.escape(stamp)}</b> {html.escape(name)}<br>'
            f"{html.escape(message)}</div>"
        )
    return '<div class="va-log">' + "".join(rows) + "</div>"


def _is_mobile() -> bool:
    # st.context.headers exposes the browser request headers of this session.
    try:
        agent = str(st.context.headers.get("User-Agent") or "")
    except Exception:
        return False
    return any(token in agent for token in ("Mobi", "Android", "iPhone", "iPad"))


def _timeline_log_html(data: dict, filename: str | None = None) -> str:
    rows = _timeline_rows(data)
    if not rows:
        return ""
    lines = []
    for row in rows:
        stamp = row["Time"].strftime("%H:%M:%S") if row["Time"] else "—"
        took = "" if row["Took"] == "—" else row["Took"]
        klass = "row err" if row.get("Level", "Info") != "Info" else "row"
        cells = (stamp, row["Stage"], row["Step"], took)
        lines.append(
            f'<div class="{klass}">'
            + "".join(f"<span>{html.escape(str(cell))}</span>" for cell in cells)
            + "</div>"
        )
    name = f'<div class="va-tl-name">{html.escape(filename)}</div>' if filename else ""
    return f'{name}<div class="va-tl">{"".join(lines)}</div>'


def _log_groups(messages: list[dict]) -> list[dict]:
    """One activity group per assistant turn that used tools or ran the pipeline."""
    groups: list[dict] = []
    asked = ""
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            files = ", ".join(message.get("files") or [])
            asked = (message.get("content") or "").strip() or (f"Attached {files}" if files else "")
            continue
        steps = message.get("steps") or []
        tools = message.get("tools") or []
        timelines = [
            panel for panel in message.get("artifacts") or [] if panel.get("kind") in _LOG_KINDS
        ]
        if not (steps or tools or timelines):
            continue
        label = asked
        if "Pipeline finished" in tools:
            label = str(message.get("content") or "")
        groups.append(
            {
                "index": index,
                "number": len(groups) + 1,
                "at": message.get("at") or "",
                "label": label,
                "steps": steps,
                "tools": tools,
                "timelines": timelines,
                "messages": message.get("artifacts") or [],
            }
        )
    return groups


def _render_log_group(group: dict) -> None:
    index = group["index"]
    with st.container(key=f"va-log-turn-{index}", gap="small"):
        head = (
            f'<div class="va-lg-head"><b>Turn {group["number"]}</b>'
            f'<span class="when">{html.escape(group["at"])}</span></div>'
        )
        if group["label"]:
            head += f'<div class="va-lg-prompt">{html.escape(group["label"])}</div>'
        head += _chips_html(group["tools"])
        if group["steps"]:
            head += _steps_html(group["steps"])
        st.markdown(head, unsafe_allow_html=True)
        if group["steps"]:
            with st.expander("Raw tool data", key=f"va-log-raw-{index}"):
                for step in group["steps"]:
                    st.caption(f"{step['name']}")
                    st.json(
                        {"arguments": step["arguments"], "result": step.get("raw") or {}},
                        expanded=False,
                    )
        for panel in group["timelines"]:
            name = next(
                (
                    str((item.get("data") or {}).get("filename") or "")
                    for item in group["messages"]
                    if item.get("kind") == "analysis"
                ),
                "",
            )
            st.markdown(
                _timeline_log_html(panel.get("data") or {}, name or None),
                unsafe_allow_html=True,
            )


def _activity_body(api: ApiClient, messages: list[dict], phase: str, pending: bool) -> None:
    st.markdown(
        '<div class="va-lg-title"><span class="pip"></span>Activity</div>',
        unsafe_allow_html=True,
    )
    if not messages and not pending:
        st.markdown(
            '<div class="va-lg-empty"><div class="ico">&#9776;</div>'
            "<b>Activity will appear here</b>Agent steps, tool calls, pipeline progress, "
            "and the live log show up once you start a chat.</div>",
            unsafe_allow_html=True,
        )
        return
    if pending:
        st.markdown(
            '<div class="va-lg-now"><i></i>Planning the answer and choosing a tool…</div>',
            unsafe_allow_html=True,
        )
    if phase == "processing":
        _processing_watch(api)
    groups = _log_groups(messages)
    # Latest turn first.
    for group in reversed(groups):
        _render_log_group(group)
    if not groups and not pending and phase != "processing":
        st.caption("No tool calls yet in this chat.")
    if st.session_state.get("show_live_log"):
        _items, events = _load(api, False, True)
        st.markdown(
            '<div class="va-lg-title" style="margin-top:6px"><span class="pip"></span>'
            f"Live log · {len(events)}</div>" + _log_html(events),
            unsafe_allow_html=True,
        )


def _activity_column(api: ApiClient, messages: list[dict], phase: str, pending: bool) -> None:
    if _is_mobile():
        # Phones: the columns stack, so the logs sit below the chat, collapsed.
        with st.expander("Activity & logs", key="va-logs-mobile", icon=":material/receipt_long:"):
            _activity_body(api, messages, phase, pending)
        return
    # A fixed-height container scrolls on its own, independent of the chat column.
    with st.container(key="va-logs", height=_PANEL_HEIGHT):
        _activity_body(api, messages, phase, pending)


def _html_table(rows: list[dict], bar: str | None = None) -> str:
    if not rows:
        return ""
    columns = list(rows[0].keys())
    head = "".join(f"<th>{html.escape(str(column))}</th>" for column in columns)
    body = []
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column)
            if isinstance(value, datetime):
                text = html.escape(value.strftime("%b %d, %Y %H:%M"))
            elif value is None:
                text = "—"
            else:
                text = html.escape(str(value))
            if column == bar and isinstance(value, int | float):
                text = (
                    f'<div class="bar"><span style="width:{min(float(value), 100):.0f}%"></span>'
                    f"</div>{float(value):.0f}%"
                )
            cells.append(f"<td>{text}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _panel_export_html(panel: dict) -> str:
    kind = panel.get("kind")
    data = panel.get("data") or {}
    empty = '<p class="empty">{}</p>'
    if kind == "rollup":
        rows = _rollup_rows(data)
        if not rows or not data.get("file_count"):
            return empty.format("No completed recordings in this range.")
        overview = _prose_html(str(data.get("overall_summary") or ""))
        return overview + _html_table(rows, bar="Share")
    if kind == "files":
        rows = _file_rows(data)
        return _html_table(rows) if rows else empty.format("No recordings matched.")
    if kind == "events":
        rows = _event_rows(data)
        return _html_table(rows) if rows else empty.format("No upcoming events mentioned.")
    if kind == "analysis":
        parts = [_facts_html(_analysis_facts(data, None))]
        if _has_speech(data):
            parts.append(_prose_html(str(data.get("summary") or "")))
            parts.append(f'<div class="transcript">{_text_html(str(data["transcript"]))}</div>')
        elif "transcript" in data and data.get("status") == "completed":
            parts.append(empty.format("No speech detected in this recording."))
        else:
            parts.append(_prose_html(str(data.get("summary") or "")))
        return "".join(parts)
    if kind == "topics":
        groups = _topic_groups(data.get("taxonomy"))
        if not any(items for _label, items in groups):
            return empty.format("No topics or upcoming events found.")
        rows = [{"Category": label, "Items": ", ".join(items) or "None"} for label, items in groups]
        return _html_table(rows)
    if kind == "checks":
        notes, body = _analytics_view(data)
        note_html = ""
        if notes:
            joined = "<br>".join(html.escape(note) for note in notes)
            note_html = f'<p class="meta">{joined}</p>'
        return note_html + (body or '<p class="meta">No extra measures for this recording.</p>')
    if kind == "timeline":
        return _html_table(_timeline_rows(data))
    return str(panel.get("body") or "")


def _export_document(messages: list[dict]) -> str:
    audio = st.session_state.get("assistant_audio") or {}
    parts = []
    for message in messages:
        if message.get("role") == "user":
            parts.append(_user_bubble(message))
            continue
        parts.append(_assistant_bubble(message) + _chips_html(message.get("tools") or []))
        for panel in message.get("artifacts") or []:
            player = ""
            clip = audio.get(panel.get("audio") or "")
            if clip:
                encoded = base64.b64encode(clip[0]).decode("ascii")
                player = f'<audio controls src="data:{clip[1]};base64,{encoded}"></audio>'
            title = _panel_title(panel)
            parts.append(
                '<details class="out" open><summary>'
                f"{html.escape(title)}</summary>"
                f'<div class="body">{player}{_panel_export_html(panel)}</div></details>'
            )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        "<title>Voice Analytics conversation</title><style>"
        "body{font-family:system-ui,sans-serif;background:#f5f7fc;color:#1f2330;"
        "margin:0;} .wrap{max-width:760px;margin:0 auto;padding:24px 16px 60px;}"
        "h1{font-size:1.2rem;} .va-av svg{display:block}"
        "details.out{background:#fff;border:1px solid #e8ebf2;border-radius:14px;"
        "margin:4px 0 12px 39px;overflow:hidden}"
        "details.out summary{font-size:.72rem;font-weight:800;letter-spacing:.06em;"
        "text-transform:uppercase;color:#8a92a6;padding:10px 14px;cursor:pointer}"
        "details.out .body{padding:4px 14px 12px;font-size:.92rem;line-height:1.55}"
        "details.out audio{width:100%;margin-bottom:8px}"
        "table{border-collapse:collapse;width:100%;font-size:.84rem;margin:6px 0}"
        "th{text-align:left;color:#6b7386;font-weight:650;border-bottom:1px solid #e3e7f1;"
        "padding:6px 8px;background:#f7f8fc} td{padding:6px 8px;border-bottom:1px solid #eef0f5;"
        "vertical-align:top} .bar{display:inline-block;width:60px;height:6px;border-radius:3px;"
        "background:#eef1fd;margin-right:6px;vertical-align:middle;overflow:hidden}"
        ".bar span{display:block;height:6px;background:#3a5be8}"
        ".empty{color:#8a92a6;border:1px dashed #d9e0fb;border-radius:10px;padding:10px;"
        "text-align:center} .meta{color:#8a92a6;font-size:.8rem}"
        ".va-facts{display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 12px}"
        ".va-fact{display:inline-flex;align-items:baseline;gap:6px;background:#fff;"
        "border:1px solid #e6e9f2;border-radius:999px;padding:5px 12px}"
        ".va-fact small{font-size:.66rem;letter-spacing:.05em;text-transform:uppercase;"
        "color:#8a92a6;font-weight:700} .va-fact b{font-size:.84rem;color:#1f2330}"
        ".va-fact.ok b{color:#16804a} .va-fact.bad b{color:#b42318} .va-fact.warn b{color:#9a6b12}"
        ".va-read{display:flex;flex-wrap:wrap;gap:22px 36px}"
        ".va-read .row{display:flex;flex-direction:column;gap:2px;min-width:120px}"
        ".va-read .k{color:#8a92a6;font-size:.78rem} .va-read .v{font-size:1rem;color:#3d4556}"
        ".va-read small{color:#8a92a6;font-size:.78rem;display:block}"
        ".va-read .v.ok{color:#16804a} .va-read .v.bad{color:#b42318}"
        ".transcript{max-height:260px;overflow:auto;background:#f7f8fc;border-radius:10px;"
        "padding:8px 10px}"
        + _BUBBLE_CSS
        + '</style></head><body><div class="wrap"><h1>Voice Analytics conversation</h1>'
        + "".join(parts)
        + "</div></body></html>"
    )


# ---------------------------------------------------------------- flow


def _load(api: ApiClient, need_files: bool, need_events: bool) -> tuple[list[dict], list[dict]]:
    items: list[dict] = []
    events: list[dict] = []
    if need_files:
        try:
            items = api.list_files({"limit": 50}).get("items") or []
        except ApiError as exc:
            st.error(exc.detail)
    if need_events:
        try:
            events = api.list_events().get("items") or []
        except ApiError as exc:
            st.error(exc.detail)
    return items, events


def _advance(api: ApiClient, items: list[dict]) -> bool:
    watch = list(st.session_state.get("assistant_watch") or [])
    reported = set(st.session_state.get("assistant_reported") or [])
    by_id = {item["id"]: item for item in items}
    pending = False
    added = False
    for file_id in watch:
        item = by_id.get(file_id)
        if item is None or item.get("status") not in _TERMINAL:
            pending = True
            continue
        if file_id in reported:
            continue
        detail = _safe_detail(api, file_id)
        if detail is None:
            pending = True
            continue
        note = f"{detail['original_filename']} is {detail['status']}."
        if detail.get("block_reason"):
            note = f"{note} {detail['block_reason']}"
        if detail.get("error_message"):
            note = f"{note} {detail['error_message']}"
        _say(
            "assistant",
            note,
            tools=["Pipeline finished"],
            artifacts=_panels_for_detail(api, detail),
        )
        reported.add(file_id)
        added = True
    st.session_state["assistant_reported"] = list(reported)
    if not pending and watch and not added:
        _say("assistant", _FOLLOWUP)
        st.session_state["assistant_phase"] = "ready"
        return True
    return added


@st.fragment(run_every=1.0)
def _processing_watch(api: ApiClient, compact: bool = False) -> None:
    # Polls on its own timer so the rest of the page stays still and the
    # composer stays usable; a full rerun happens only when a file finishes.
    # compact is the activity-off chat: one status line, replaced each second,
    # and dropped entirely once the phase leaves "processing".
    if st.session_state.get("assistant_phase") != "processing":
        return
    items, events = _load(api, True, True)
    if compact:
        _processing_status(items, events)
    else:
        _processing_card(items, events)
    if _advance(api, items):
        st.rerun()
    polls = st.session_state.get("assistant_polls", 0) + 1
    st.session_state["assistant_polls"] = polls
    if polls >= 120:
        _say("assistant", "Still working on it. Ask a question anyway, or check back in a moment.")
        st.session_state["assistant_phase"] = "ready"
        st.rerun()


def _run_turn(api: ApiClient, prompt: str, placeholder) -> None:
    placeholder.markdown(_typing(), unsafe_allow_html=True)
    try:
        body = api.chat(prompt, _history_for_api())
    except ApiError as exc:
        _say("assistant", exc.detail)
        return
    calls = body.get("tool_calls") or []
    steps = _step_records(calls)
    labels = list(dict.fromkeys(_TOOL_LABEL.get(step["name"], step["name"]) for step in steps))
    _say(
        "assistant",
        body.get("reply") or "No answer.",
        tools=labels,
        artifacts=_panels_for_tools(api, calls, prompt),
        steps=steps,
    )


def render_assistant(api: ApiClient, sample_path: Path, meta: dict | None = None) -> None:
    _ensure_state()
    st.markdown(_APP_CSS, unsafe_allow_html=True)
    messages = st.session_state["assistant_messages"]
    _header(meta, messages)

    phase = st.session_state["assistant_phase"]
    prompt = st.session_state.get("assistant_run")
    show_activity = st.session_state["show_activity"]
    if show_activity:
        # Two columns: the chat (about 68%) and a gray activity/log column (about 32%).
        chat_col, log_col = st.columns([2.1, 1], gap="medium")
        with log_col:
            _activity_column(api, messages, phase, bool(prompt))
    else:
        # Activity hidden: no columns at all, so the chat takes the full width and the
        # activity panel, live log, and phone "Activity & logs" expander are never drawn.
        # st.container() is a plain block, so "with chat_col:" below works either way.
        chat_col = st.container()
    with chat_col:
        if not messages and not prompt:
            _empty_state(sample_path)
            return
        with st.container(key="va-thread", height=_PANEL_HEIGHT, autoscroll=True):
            for index, message in enumerate(messages):
                _render_turn(api, index, message)
            if phase == "processing" and not show_activity:
                # In the thread, under the "processing has started" message.
                # The full stage sequence stays in the activity column.
                _processing_watch(api, compact=True)
            if phase == "options":
                _options_card(api)
            if st.session_state.get("assistant_show_uploader"):
                _uploader_card(sample_path)
            placeholder = st.empty()
        _composer("va-composer")

    if prompt:
        st.session_state["assistant_run"] = None
        _run_turn(api, prompt, placeholder)
        st.rerun()
