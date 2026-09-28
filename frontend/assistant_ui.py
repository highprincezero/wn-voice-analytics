import base64
import html
import time
from pathlib import Path

import streamlit as st
from api_client import ApiClient, ApiError

_TERMINAL = {"completed", "blocked", "failed"}
_GREETING = (
    "Hello. Upload one recording, or a set of up to 10 (wav, mp3, m4a, ogg, flac, 20 MB each)."
)
_FOLLOWUP = (
    "You can ask a follow-up. For example: what upcoming events did I mention this week, "
    "or summarize my files by topic."
)
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

_BUBBLE_CSS = """
.msg { display: flex; gap: 8px; margin: 10px 0; align-items: flex-end; }
.msg.user { justify-content: flex-end; }
.msg.assistant { justify-content: flex-start; }
.avatar {
  width: 32px; height: 32px; border-radius: 50%; background: #0F6E6E; color: #fff;
  font-size: 11px; font-weight: 700; display: flex; align-items: center;
  justify-content: center; flex: 0 0 32px;
}
.bubble {
  max-width: min(78%, 640px); padding: 10px 12px; border-radius: 14px;
  line-height: 1.45; font-size: 15px;
}
.msg.user .bubble { background: #0F6E6E; color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .bubble {
  background: #E7EEEE; color: #14222B; border-bottom-left-radius: 4px;
}
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip {
  font-size: 12px; border: 1px solid #0F6E6E; color: #0F6E6E; border-radius: 999px;
  padding: 2px 8px; background: #fff;
}
details.artifact {
  border: 1px solid #d5e0e0; border-radius: 8px; margin-top: 8px; background: #fff;
}
details.artifact summary { cursor: pointer; padding: 8px 10px; font-weight: 650; }
details.artifact .body { padding: 0 10px 10px; font-size: 14px; }
details.artifact audio { width: 100%; margin-bottom: 8px; }
"""

_APP_CSS = (
    """
<style>
section.main > div.block-container { max-width: 1080px; padding-top: 0.7rem; }
div.stButton > button, div.stDownloadButton > button { min-height: 48px; }
textarea, input, select { font-size: 16px !important; }
.flow-wrap {
  position: sticky; top: 0; z-index: 30; background: #F6F8F8;
  padding: 4px 0 10px; margin-bottom: 4px;
}
.flow-caption { font-size: 13px; color: #3d4c55; margin-bottom: 6px; }
.flow { display: flex; flex-wrap: wrap; align-items: center; row-gap: 8px; }
.step {
  border: 1px solid #c5d0d0; background: #eef2f2; color: #5c6b73;
  border-radius: 8px; padding: 8px 10px; font-size: 13px; min-height: 36px;
}
.step.done { background: #e5f6ea; border-color: #1f8a4c; color: #146c3a; }
.step.current {
  background: #fde8e8; border-color: #b42318; color: #9f1c14;
  animation: step-pulse 1.4s ease-in-out infinite;
}
.step.skipped { background: #f7f7f7; color: #8a9399; border-style: dashed; }
.step.failed { background: #fde8e8; border-color: #b42318; color: #9f1c14; }
.conn {
  width: 28px; height: 2px; margin: 0 4px; flex: 0 0 28px;
  background: repeating-linear-gradient(90deg, #b7c2c2 0 5px, transparent 5px 9px);
}
.conn.done { background: #1f8a4c; }
.conn.march {
  background: repeating-linear-gradient(90deg, #b42318 0 5px, transparent 5px 9px);
  background-size: 18px 2px;
  animation: dash-march 0.55s linear infinite;
}
.chat-scroll {
  max-height: 52vh; overflow-y: auto; padding-right: 4px; margin-bottom: 8px;
}
.log-title { font-size: 13px; font-weight: 650; margin-bottom: 6px; }
.log-panel {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px; line-height: 1.45; max-height: 70vh; overflow: auto;
  background: #14222B; color: #d7e4e4; padding: 10px; border-radius: 8px;
}
.log-panel .line { margin: 0 0 6px; white-space: pre-wrap; }
.log-panel .err { color: #ffb4a8; }
"""
    + _BUBBLE_CSS
    + """
@keyframes step-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(180, 35, 24, 0.45); }
  50% { box-shadow: 0 0 0 7px rgba(180, 35, 24, 0); }
}
@keyframes dash-march {
  from { background-position: 0 0; }
  to { background-position: 18px 0; }
}
@media (max-width: 959px) {
  section.main > div.block-container {
    max-width: 100% !important;
    padding-left: 0.75rem;
    padding-right: 0.75rem;
  }
  .chat-scroll { max-height: 46vh; }
  .main-log { display: none !important; }
}
@media (min-width: 960px) {
  [data-testid="stSidebar"] .sidebar-log { display: none !important; }
}
@media (prefers-reduced-motion: reduce) {
  .step.current { animation: none; box-shadow: 0 0 0 2px rgba(180, 35, 24, 0.45); }
  .conn.march { animation: none; }
}
</style>
"""
)


def _text_html(value: str) -> str:
    return "<br>".join(html.escape(value).splitlines())


def _topics_html(taxonomy: dict, filename: str | None = None) -> str:
    lines = []
    if filename:
        lines.append(filename)
    professional = ", ".join(taxonomy.get("professional_topics") or []) or "—"
    personal = ", ".join(taxonomy.get("personal_topics") or []) or "—"
    upcoming = taxonomy.get("upcoming_events") or []
    lines.append("Professional: " + professional)
    lines.append("Personal: " + personal)
    lines.append("Upcoming: " + ("; ".join(str(item) for item in upcoming) if upcoming else "—"))
    return "<br>".join(html.escape(line) for line in lines)


def _layer2_html(layer2: dict, filename: str | None = None) -> str:
    lines = [filename] if filename else []
    for key, value in layer2.items():
        if isinstance(value, dict):
            bits = ", ".join(f"{name}: {item}" for name, item in value.items())
            lines.append(f"{key}: {bits}")
        else:
            lines.append(f"{key}: {value}")
    return "<br>".join(html.escape(line) for line in lines if line)


def _rollup_html(result: dict) -> str:
    lines = []
    if result.get("overall_summary"):
        lines.append(str(result["overall_summary"]))
    for group in result.get("groups") or []:
        count = group.get("file_count")
        lines.append(f"{group.get('key')} ({count}): {group.get('summary')}")
    if not lines:
        lines.append("No recordings in this range.")
    return "<br>".join(html.escape(line) for line in lines)


def _audio_uri(api: ApiClient, file_id: str) -> str:
    cache = st.session_state.setdefault("assistant_audio", {})
    cached = cache.get(file_id)
    if cached:
        return cached
    try:
        data, media = api.download_audio(file_id)
    except ApiError:
        return ""
    encoded = base64.b64encode(data).decode("ascii")
    uri = f"data:{media};base64,{encoded}"
    cache[file_id] = uri
    return uri


def _panel(title: str, body: str) -> dict:
    return {"title": title, "body": body}


def _panels_for_detail(api: ApiClient, detail: dict) -> list[dict]:
    panels = []
    transcript = detail.get("transcript") or ""
    player = ""
    if detail.get("id"):
        uri = _audio_uri(api, detail["id"])
        if uri:
            player = f'<audio controls src="{uri}"></audio>'
    if transcript or player:
        panels.append(_panel("Output · Transcript", player + _text_html(transcript)))
    if detail.get("summary"):
        panels.append(_panel("Output · Summary", _text_html(str(detail["summary"]))))
    taxonomy = detail.get("taxonomy") or {}
    if taxonomy:
        panels.append(_panel("Output · Topics & events", _topics_html(taxonomy)))
    layer2 = detail.get("layer2") or {}
    if layer2:
        panels.append(_panel("Output · Layer 2", _layer2_html(layer2)))
    return panels


def _panels_from_payload(item: dict) -> list[dict]:
    filename = item.get("filename") or item.get("original_filename")
    panels = []
    if item.get("summary"):
        body = _text_html(str(item["summary"]))
        if filename:
            body = _text_html(str(filename)) + "<br>" + body
        panels.append(_panel("Output · Summary", body))
    taxonomy = item.get("taxonomy") or {}
    if taxonomy:
        panels.append(_panel("Output · Topics & events", _topics_html(taxonomy, filename)))
    layer2 = item.get("layer2") or {}
    if layer2:
        panels.append(_panel("Output · Layer 2", _layer2_html(layer2, filename)))
    return panels


def _panels_for_tools(api: ApiClient, tool_calls: list[dict]) -> tuple[list[str], list[dict]]:
    names: list[str] = []
    panels: list[dict] = []
    for call in tool_calls:
        name = str(call.get("name") or "")
        if name:
            names.append(name)
        result = call.get("result") or {}
        if result.get("error"):
            panels.append(_panel("Output · Summary", _text_html(str(result["error"]))))
            continue
        if name == "run_summary":
            panels.append(_panel("Output · Summary", _rollup_html(result)))
            continue
        if name == "get_analysis":
            file_id = (call.get("arguments") or {}).get("file_id") or result.get("id")
            detail = _safe_detail(api, file_id)
            panels.extend(
                _panels_for_detail(api, detail) if detail else _panels_from_payload(result)
            )
            continue
        if name == "search_files":
            found = result.get("items") or []
            if len(found) == 1 and found[0].get("id"):
                detail = _safe_detail(api, found[0]["id"])
                if detail:
                    panels.extend(_panels_for_detail(api, detail))
                    continue
            if not found:
                panels.append(_panel("Output · Summary", "No recordings matched."))
            for item in found:
                panels.extend(_panels_from_payload(item))
    return names, panels


def _safe_detail(api: ApiClient, file_id: str | None) -> dict | None:
    if not file_id:
        return None
    try:
        return api.get_file(str(file_id))
    except ApiError:
        return None


def _bubbles(messages: list[dict]) -> str:
    parts = []
    for message in messages:
        content = _text_html(message.get("content") or "")
        if message.get("role") == "user":
            parts.append(f'<div class="msg user"><div class="bubble">{content}</div></div>')
            continue
        chips = "".join(
            f'<span class="chip">{html.escape(name)}</span>' for name in message.get("tools") or []
        )
        chip_row = f'<div class="chips">{chips}</div>' if chips else ""
        panels = []
        for panel in message.get("artifacts") or []:
            title = html.escape(str(panel.get("title") or "Output"))
            body = panel.get("body") or ""
            panels.append(
                f'<details class="artifact" open><summary>{title}</summary>'
                f'<div class="body">{body}</div></details>'
            )
        parts.append(
            '<div class="msg assistant"><div class="avatar">AI</div>'
            f'<div class="bubble">{content}{chip_row}{"".join(panels)}</div></div>'
        )
    return "".join(parts)


def _export_document(messages: list[dict]) -> str:
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        "<title>Voice Analytics conversation</title><style>"
        "body{font-family:system-ui,sans-serif;background:#F6F8F8;color:#14222B;"
        "margin:24px;} h1{font-size:1.3rem;}"
        + _BUBBLE_CSS
        + "</style></head><body><h1>Voice Analytics conversation</h1>"
        + _bubbles(messages)
        + "</body></html>"
    )


def _step_class(key: str, item: dict | None) -> str:
    if item is None:
        return "pending"
    skipped = set(item.get("skipped_stages") or [])
    if key in skipped:
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


def _flow_html(item: dict | None) -> str:
    if item is None:
        caption = "Pipeline"
    else:
        caption = str(item.get("original_filename") or "Recording")
    classes = [_step_class(key, item) for key, _label in _STEPS]
    chunks = [
        '<div class="flow-wrap">',
        f'<div class="flow-caption">{html.escape(caption)}</div>',
        '<div class="flow">',
    ]
    for index, ((key, label), klass) in enumerate(zip(_STEPS, classes, strict=True)):
        del key
        if index:
            previous = classes[index - 1]
            conn = "conn"
            if klass == "current":
                conn = "conn march"
            elif klass == "done" and previous == "done":
                conn = "conn done"
            chunks.append(f'<div class="{conn}"></div>')
        chunks.append(f'<div class="step {klass}">{html.escape(label)}</div>')
    chunks.append("</div></div>")
    return "".join(chunks)


def _status_label(item: dict) -> str:
    stage = item.get("stage") or "upload"
    if item.get("status") == "failed":
        return f"{_STEP_LABEL.get(stage, stage)} failed"
    if item.get("status") in {"completed", "blocked"}:
        return "Saved" if item.get("status") != "blocked" else "Saved (blocked)"
    return _STEP_LABEL.get(stage, stage)


def _log_html(events: list[dict]) -> str:
    if not events:
        body = '<div class="line">No events yet.</div>'
    else:
        rows = []
        for event in events[:300]:
            stamp = str(event.get("created_at") or "").replace("T", " ")[:19]
            name = str(event.get("filename") or "")
            message = str(event.get("message") or "")
            klass = "line err" if event.get("level") == "error" else "line"
            text = html.escape(f"{stamp}  {name}  {message}".strip())
            rows.append(f'<div class="{klass}">{text}</div>')
        body = "".join(rows)
    panel = f'<div class="log-title">Live log</div><div class="log-panel">{body}</div>'
    return panel


def _history_for_api() -> list[dict]:
    turns = []
    for message in st.session_state.get("assistant_messages") or []:
        content = (message.get("content") or "").strip()
        if message.get("role") in {"user", "assistant"} and content:
            turns.append({"role": message["role"], "content": content[:2000]})
    if turns and turns[-1]["role"] == "user":
        turns = turns[:-1]
    return turns[-8:]


def _ensure_state() -> None:
    if "assistant_messages" not in st.session_state:
        st.session_state["assistant_messages"] = [{"role": "assistant", "content": _GREETING}]
        st.session_state["assistant_phase"] = "upload"
        st.session_state["assistant_watch"] = []
        st.session_state["assistant_reported"] = []
        st.session_state["assistant_upload_nonce"] = 0
        st.session_state["assistant_audio"] = {}
    if "show_live_log" not in st.session_state:
        st.session_state["show_live_log"] = True


def _say(
    role: str,
    content: str,
    tools: list[str] | None = None,
    artifacts: list[dict] | None = None,
) -> None:
    st.session_state["assistant_messages"].append(
        {
            "role": role,
            "content": content,
            "tools": tools or [],
            "artifacts": artifacts or [],
        }
    )


def _scoped(items: list[dict]) -> list[dict]:
    by_id = {item["id"]: item for item in items}
    watch = list(st.session_state.get("assistant_watch") or [])
    if watch:
        scoped = [by_id[file_id] for file_id in watch if file_id in by_id]
        if scoped:
            return scoped
    return items[:10]


def _choose(scoped: list[dict]) -> dict | None:
    if not scoped:
        return None
    by_id = {item["id"]: item for item in scoped}
    current = st.session_state.get("assistant_selected")
    if current in by_id:
        return by_id[current]
    active = [item for item in scoped if item.get("status") not in _TERMINAL]
    if active:
        return active[-1]
    if st.session_state.get("assistant_watch"):
        return scoped[-1]
    return scoped[0]


def _load(api: ApiClient) -> tuple[list[dict], list[dict]]:
    try:
        items = api.list_files({"limit": 50}).get("items") or []
    except ApiError as exc:
        st.error(exc.detail)
        items = []
    try:
        events = api.list_events().get("items") or []
    except ApiError as exc:
        st.error(exc.detail)
        events = []
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
        _say("assistant", note, artifacts=_panels_for_detail(api, detail))
        reported.add(file_id)
        added = True
    st.session_state["assistant_reported"] = list(reported)
    if not pending and watch and not added:
        _say("assistant", _FOLLOWUP)
        st.session_state["assistant_phase"] = "ready"
        return True
    return added


def render_assistant(api: ApiClient, sample_path: Path) -> None:
    _ensure_state()
    st.markdown(_APP_CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.toggle("Live log", key="show_live_log")
    items, events = _load(api)
    scoped = _scoped(items)
    chosen = _choose(scoped)
    if chosen is not None:
        st.session_state["assistant_selected"] = chosen["id"]
    show_log = bool(st.session_state.get("show_live_log"))
    log_markup = _log_html(events)
    if show_log:
        with st.sidebar:
            st.markdown(
                f'<div class="sidebar-log">{log_markup}</div>',
                unsafe_allow_html=True,
            )
        main_col, log_col = st.columns([1.7, 1], gap="medium")
    else:
        main_col = st.container()
        log_col = None
    with main_col:
        st.markdown(_flow_html(chosen), unsafe_allow_html=True)
        if len(scoped) > 1:
            for item in scoped:
                label = f"{item['original_filename']} · {_status_label(item)}"
                kind = "primary" if chosen and item["id"] == chosen["id"] else "secondary"
                if st.button(label, key=f"pick-{item['id']}", type=kind):
                    st.session_state["assistant_selected"] = item["id"]
                    st.rerun()
        st.markdown(
            f'<div class="chat-scroll">{_bubbles(st.session_state["assistant_messages"])}</div>',
            unsafe_allow_html=True,
        )
        phase = st.session_state["assistant_phase"]
        if phase == "upload":
            _upload_phase(sample_path)
        elif phase == "options":
            _options_phase(api)
        elif phase == "processing":
            st.caption("Pipeline is running. The steps above follow the file stage.")
        else:
            _ready_phase(api)
        st.download_button(
            "Export conversation",
            data=_export_document(st.session_state["assistant_messages"]),
            file_name="conversation.html",
            mime="text/html",
        )
    if log_col is not None:
        with log_col:
            st.markdown(f'<div class="main-log">{log_markup}</div>', unsafe_allow_html=True)
    if st.session_state["assistant_phase"] == "processing":
        _poll(api, items)


def _poll(api: ApiClient, items: list[dict]) -> None:
    if _advance(api, items):
        st.rerun()
    polls = st.session_state.get("assistant_polls", 0) + 1
    st.session_state["assistant_polls"] = polls
    if polls < 90:
        time.sleep(0.8)
        st.rerun()
    _say("assistant", "Still working. Ask a question anyway, or upload again in a moment.")
    st.session_state["assistant_phase"] = "ready"
    st.rerun()


def _remember_files(files: list[tuple[str, bytes]]) -> None:
    if not files:
        return
    if len(files) > 10:
        _say("assistant", "Attach at most 10 files.")
        st.rerun()
    names = ", ".join(name for name, _data in files)
    st.session_state["assistant_pending"] = files
    _say("user", f"Attached {len(files)} file(s): {names}.")
    _say(
        "assistant",
        "Got them. Choose the Layer 2 options to save, then I will upload and start processing. "
        "Leave them all off to run Layer 1 only.",
    )
    st.session_state["assistant_phase"] = "options"
    st.session_state["assistant_upload_nonce"] = (
        st.session_state.get("assistant_upload_nonce", 0) + 1
    )
    st.rerun()


def _upload_phase(sample_path: Path) -> None:
    nonce = st.session_state.get("assistant_upload_nonce", 0)
    uploads = st.file_uploader(
        "Audio files",
        type=["wav", "mp3", "m4a", "ogg", "flac"],
        accept_multiple_files=True,
        key=f"assistant-upload-{nonce}",
    )
    if st.button("Use these files") and uploads:
        _remember_files([(item.name, item.getvalue()) for item in uploads])
    if sample_path.exists() and st.button("Use bundled sample"):
        _remember_files([(sample_path.name, sample_path.read_bytes())])


def _options_phase(api: ApiClient) -> None:
    try:
        options = api.prompt_options()
    except ApiError as exc:
        st.error(exc.detail)
        return
    with st.form("assistant-options"):
        selections = []
        for option in options:
            enabled = st.checkbox(option["label"], help=option["description"])
            params = {name: spec["default"] for name, spec in option["params"].items()}
            if enabled:
                selections.append({"option_id": option["id"], "params": params})
        submitted = st.form_submit_button("Save options and start processing")
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
    if items:
        st.session_state["assistant_selected"] = items[-1]["id"]
    names = ", ".join(item["original_filename"] for item in items)
    _say("assistant", f"Processing has started for {len(items)} file(s): {names}.")
    st.session_state["assistant_phase"] = "processing"
    st.session_state["assistant_polls"] = 0
    st.rerun()


def _ready_phase(api: ApiClient) -> None:
    if st.button("Upload more"):
        st.session_state["assistant_phase"] = "upload"
        st.rerun()
    prompt = st.chat_input("Ask about your recordings")
    if not prompt:
        return
    text = prompt.strip()
    _say("user", text)
    try:
        body = api.chat(text, _history_for_api())
    except ApiError as exc:
        _say("assistant", exc.detail)
    else:
        names, panels = _panels_for_tools(api, body.get("tool_calls") or [])
        _say("assistant", body.get("reply") or "No answer.", tools=names, artifacts=panels)
    st.rerun()
