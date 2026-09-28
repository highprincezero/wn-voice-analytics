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


def _card(item: dict) -> dict:
    return {
        "original_filename": item.get("original_filename"),
        "status": item.get("status"),
        "duration_sec": item.get("duration_sec"),
        "summary": item.get("summary"),
        "taxonomy": item.get("taxonomy"),
        "layer2": item.get("layer2"),
        "block_reason": item.get("block_reason"),
        "error_message": item.get("error_message"),
    }


def _render_card(card: dict) -> None:
    duration = card["duration_sec"] if card.get("duration_sec") is not None else "—"
    st.markdown(f"**{card.get('original_filename') or 'recording'}**")
    st.caption(f"{card.get('status')} · {duration}s")
    if card.get("block_reason"):
        st.warning(card["block_reason"])
    if card.get("error_message"):
        st.error(card["error_message"])
    if card.get("summary"):
        st.write(card["summary"])
    taxonomy = card.get("taxonomy") or {}
    if taxonomy:
        professional = ", ".join(taxonomy.get("professional_topics") or []) or "—"
        personal = ", ".join(taxonomy.get("personal_topics") or []) or "—"
        st.write("Professional: " + professional)
        st.write("Personal: " + personal)
        events = taxonomy.get("upcoming_events") or []
        st.write("Upcoming: " + ("; ".join(events) if events else "—"))
    layer2 = card.get("layer2") or {}
    bits = []
    if "sentiment_lexicon" in layer2:
        bits.append(str(layer2["sentiment_lexicon"].get("label") or ""))
    if "speaking_pace" in layer2:
        bits.append(f"{layer2['speaking_pace'].get('words_per_minute')} wpm")
    if "rms_energy" in layer2 and "rms_mean" in layer2["rms_energy"]:
        bits.append(f"rms {layer2['rms_energy']['rms_mean']}")
    if "pos_counts" in layer2:
        counts = layer2["pos_counts"]
        bits.append(f"{counts.get('noun_count')} nouns")
        bits.append(f"{counts.get('adjective_count')} adjectives")
    if bits:
        st.caption(" · ".join(bit for bit in bits if bit))


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


def _say(role: str, content: str, card: dict | None = None) -> None:
    message = {"role": role, "content": content}
    if card is not None:
        message["card"] = card
    st.session_state["assistant_messages"].append(message)


def render_assistant(api: ApiClient, sample_path: Path) -> None:
    _ensure_state()
    st.caption(
        "Assistant uses the same upload, prompt, and file APIs. Questions go to the chat agent."
    )
    for message in st.session_state["assistant_messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("card"):
                _render_card(message["card"])

    phase = st.session_state["assistant_phase"]
    if phase == "upload":
        _upload_phase(api, sample_path)
    elif phase == "options":
        _options_phase(api)
    elif phase == "processing":
        _processing_phase(api)
    else:
        _ready_phase(api)


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


def _upload_phase(api: ApiClient, sample_path: Path) -> None:
    del api
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
            params = {}
            for name, spec in option["params"].items():
                params[name] = spec["default"]
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
    names = ", ".join(item["original_filename"] for item in items)
    _say("assistant", f"Processing has started for {len(items)} file(s): {names}.")
    st.session_state["assistant_phase"] = "processing"
    st.session_state["assistant_polls"] = 0
    st.rerun()


def _processing_phase(api: ApiClient) -> None:
    watch = list(st.session_state.get("assistant_watch") or [])
    reported = set(st.session_state.get("assistant_reported") or [])
    try:
        listing = api.list_files({"limit": 50})
    except ApiError as exc:
        st.error(exc.detail)
        return
    by_id = {item["id"]: item for item in listing.get("items") or []}
    pending = False
    added = False
    for file_id in watch:
        item = by_id.get(file_id)
        if item is None or item["status"] not in _TERMINAL:
            pending = True
            continue
        if file_id in reported:
            continue
        try:
            detail = api.get_file(file_id)
        except ApiError as exc:
            st.error(exc.detail)
            return
        _say(
            "assistant",
            f"{detail['original_filename']} is {detail['status']}.",
            _card(detail),
        )
        reported.add(file_id)
        added = True
    st.session_state["assistant_reported"] = list(reported)
    if not pending and watch:
        _say("assistant", _FOLLOWUP)
        st.session_state["assistant_phase"] = "ready"
        st.rerun()
    if added:
        st.rerun()
    st.info("Processing is still running.")
    polls = st.session_state.get("assistant_polls", 0) + 1
    st.session_state["assistant_polls"] = polls
    if polls < 40:
        time.sleep(1.2)
        st.rerun()
    _say("assistant", "Still working. Ask a question anyway, or upload again in a moment.")
    st.session_state["assistant_phase"] = "ready"
    st.rerun()


def _ready_phase(api: ApiClient) -> None:
    if st.button("Upload more"):
        st.session_state["assistant_phase"] = "upload"
        st.rerun()
    prompt = st.chat_input("Ask about your recordings")
    if not prompt:
        return
    _say("user", prompt.strip())
    try:
        body = api.chat(prompt.strip(), _history_for_api())
    except ApiError as exc:
        _say("assistant", exc.detail)
    else:
        _say("assistant", body.get("reply") or "No answer.")
    st.rerun()
