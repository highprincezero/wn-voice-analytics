import os
import time
from datetime import date, datetime
from datetime import time as clock
from pathlib import Path

import streamlit as st
from api_client import ApiClient, ApiError
from assistant_ui import render_assistant

st.set_page_config(
    page_title="Voice Analytics",
    page_icon="🎙",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.markdown(
    """
    <style>
    section.main > div.block-container {
        max-width: 440px;
        padding-top: 1.1rem;
        padding-left: 1rem;
        padding-right: 1rem;
    }
    div.stButton > button { width: 100%; min-height: 44px; }
    h1 { font-size: 1.55rem; margin-bottom: 0.2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
SAMPLE_PATH = Path(__file__).resolve().parent / "samples" / "sample_call.wav"


def client() -> ApiClient:
    return ApiClient(API_BASE_URL, st.session_state.get("token"))


def show_error(exc: ApiError) -> None:
    st.error(exc.detail)


if "nav" not in st.session_state:
    st.session_state["nav"] = "Library"
if "polls" not in st.session_state:
    st.session_state["polls"] = 0

destination = st.session_state.pop("goto", None)
if destination:
    st.session_state["nav"] = destination

st.title("Voice Analytics")
try:
    meta = ApiClient(API_BASE_URL).meta()
except ApiError as exc:
    st.error(f"API unavailable at {API_BASE_URL}: {exc.detail}")
    st.stop()
except Exception as exc:
    st.error(f"API unavailable at {API_BASE_URL}: {exc}")
    st.stop()

if meta["llm_provider"] == "mock":
    st.caption("Mock mode is on. Speech-to-text and summaries use deterministic stand-ins.")
else:
    st.caption(f"Providers: {meta['llm_provider']} / {meta['safety_provider']}.")

with st.sidebar:
    st.radio("Mode", ["Classic", "Assistant"], key="ui_mode")
    st.caption("Classic is the library, upload, prompts, rollup, and account pages.")

if not st.session_state.get("token"):
    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])
    with login_tab:
        with st.form("login"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            if st.form_submit_button("Log in"):
                try:
                    body = client().login(email, password)
                except ApiError as exc:
                    show_error(exc)
                else:
                    st.session_state["token"] = body["access_token"]
                    st.session_state["goto"] = "Library"
                    st.rerun()
    with signup_tab:
        with st.form("signup"):
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")
            if st.form_submit_button("Create account"):
                try:
                    body = client().signup(email, password)
                except ApiError as exc:
                    show_error(exc)
                else:
                    st.session_state["token"] = body["access_token"]
                    st.session_state["goto"] = "Prompts"
                    st.rerun()
    st.stop()

if st.session_state.get("ui_mode") == "Assistant":
    render_assistant(client(), SAMPLE_PATH)
    st.stop()

page = st.radio(
    "Navigate",
    ["Library", "Upload", "Prompts", "Rollup", "Account"],
    horizontal=True,
    key="nav",
    label_visibility="collapsed",
)


def end_of_day(value: date) -> str:
    return datetime.combine(value, clock(23, 59, 59)).isoformat()


if page == "Upload":
    st.subheader("Upload")
    st.write("WAV files are measured locally. Analysis starts as soon as the upload is stored.")
    uploads = st.file_uploader(
        "Audio files",
        type=["wav", "mp3", "m4a", "ogg", "flac"],
        accept_multiple_files=True,
    )
    if st.button("Upload selected") and uploads:
        try:
            client().upload([(item.name, item.getvalue()) for item in uploads])
        except ApiError as exc:
            show_error(exc)
        else:
            st.session_state["polls"] = 0
            st.session_state["goto"] = "Library"
            st.rerun()
    if SAMPLE_PATH.exists() and st.button("Upload bundled sample"):
        try:
            client().upload([(SAMPLE_PATH.name, SAMPLE_PATH.read_bytes())])
        except ApiError as exc:
            show_error(exc)
        else:
            st.session_state["polls"] = 0
            st.session_state["goto"] = "Library"
            st.rerun()

elif page == "Prompts":
    st.subheader("Prompt options")
    st.write("Choose from the fixed Layer 2 catalog. The server prompt text is not editable.")
    try:
        options = client().prompt_options()
        saved_rows = client().get_config()["selections"]
        current = {item["option_id"]: item.get("params") or {} for item in saved_rows}
    except ApiError as exc:
        show_error(exc)
        st.stop()
    with st.form("prompts"):
        selections = []
        for option in options:
            enabled = st.checkbox(
                option["label"],
                value=option["id"] in current,
                help=option["description"],
            )
            params = {}
            saved = current.get(option["id"]) or {}
            for name, spec in option["params"].items():
                if spec["type"] == "enum":
                    values = spec["values"]
                    default = saved.get(name, spec["default"])
                    if default in values:
                        index = values.index(default)
                    else:
                        index = values.index(spec["default"])
                    widget_key = f"{option['id']}-{name}"
                    params[name] = st.selectbox(name, values, index=index, key=widget_key)
                elif spec["type"] == "int":
                    params[name] = st.slider(
                        name,
                        min_value=spec["min"],
                        max_value=spec["max"],
                        value=int(saved.get(name, spec["default"])),
                        key=f"{option['id']}-{name}",
                    )
            if enabled:
                selections.append({"option_id": option["id"], "params": params})
        if st.form_submit_button("Save options"):
            try:
                client().save_config(selections)
            except ApiError as exc:
                show_error(exc)
            else:
                st.success("Saved. New uploads use these options.")

elif page == "Rollup":
    st.subheader("Collective summary")
    group_by = st.selectbox(
        "Group by",
        ["user", "taxonomy_label", "week", "sentiment"],
        format_func=lambda value: {
            "user": "All of my files",
            "taxonomy_label": "Taxonomy label",
            "week": "Week",
            "sentiment": "Sentiment",
        }[value],
    )
    use_range = st.checkbox("Limit to a date range")
    time_from = None
    time_to = None
    if use_range:
        picked = st.date_input(
            "Range",
            value=(date.today().replace(day=1), date.today()),
        )
        if isinstance(picked, (list, tuple)) and len(picked) == 2:
            start, finish = picked
        else:
            start = finish = date.today()
        time_from = datetime.combine(start, clock.min).isoformat()
        time_to = end_of_day(finish)
    if st.button("Run summary job"):
        body = {"group_by": group_by}
        if time_from:
            body["time_from"] = time_from
            body["time_to"] = time_to
        try:
            created = client().run_summary(body)
        except ApiError as exc:
            show_error(exc)
        else:
            st.session_state["latest_rollup"] = created
    latest = st.session_state.get("latest_rollup")
    if latest:
        result = latest["result"]
        st.write(result.get("overall_summary") or "No recordings in this range.")
        for group in result.get("groups") or []:
            with st.container(border=True):
                st.markdown(f"**{group['key']}** · {group['file_count']} file(s)")
                st.write(group["summary"])
    try:
        history = client().list_summaries()["items"]
    except ApiError as exc:
        show_error(exc)
        history = []
    if history:
        st.caption(f"{len(history)} saved rollup(s). Latest trigger: {history[0]['trigger']}.")

elif page == "Account":
    st.subheader("Account")
    try:
        profile = client().me()
    except ApiError as exc:
        show_error(exc)
        st.stop()
    st.write(f"**Email** {profile['email']}")
    st.write(f"**User id** `{profile['id']}`")
    st.write(f"**Home region** {profile['home_region']}")
    st.caption("Audio, transcripts, and analysis are stored under this user id.")
    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()

else:
    st.subheader("Library")
    with st.expander("Filters"):
        use_from = st.checkbox("From date")
        date_from = st.date_input("From", disabled=not use_from)
        use_to = st.checkbox("To date")
        date_to = st.date_input("To", disabled=not use_to)
        min_duration = st.number_input("Min duration (sec)", min_value=0.0, value=0.0, step=0.5)
        max_duration = st.number_input("Max duration (sec)", min_value=0.0, value=0.0, step=0.5)
        taxonomy = st.text_input("Taxonomy contains")
        custom_name = st.selectbox(
            "Custom filter",
            ["none", "sentiment", "adjective_count", "noun_count", "wpm", "rms_mean"],
        )
        custom_value = st.text_input("Custom value", value="")
    params = {}
    if use_from:
        params["date_from"] = datetime.combine(date_from, clock.min).isoformat()
    if use_to:
        params["date_to"] = end_of_day(date_to)
    if min_duration:
        params["min_duration"] = min_duration
    if max_duration:
        params["max_duration"] = max_duration
    if taxonomy.strip():
        params["taxonomy"] = taxonomy.strip()
    if custom_name != "none" and custom_value.strip():
        params["custom"] = f"{custom_name}:{custom_value.strip()}"
    try:
        listing = client().list_files(params)
    except ApiError as exc:
        show_error(exc)
        st.stop()
    items = listing["items"]
    st.caption(f"{listing['total']} file(s)")
    pending = any(item["status"] in {"uploaded", "processing"} for item in items)
    if pending:
        st.info("Analysis is running.")
    for item in items:
        with st.container(border=True):
            st.markdown(f"**{item['original_filename']}**")
            duration = item["duration_sec"] if item["duration_sec"] is not None else "—"
            st.caption(f"{item['status']} · {duration}s · {item['created_at']}")
            if item.get("block_reason"):
                st.warning(item["block_reason"])
            if item.get("summary"):
                st.write(item["summary"])
            taxonomy_doc = item.get("taxonomy") or {}
            if taxonomy_doc:
                professional = ", ".join(taxonomy_doc.get("professional_topics") or []) or "—"
                personal = ", ".join(taxonomy_doc.get("personal_topics") or []) or "—"
                st.write("Professional: " + professional)
                st.write("Personal: " + personal)
                events = taxonomy_doc.get("upcoming_events") or []
                if events:
                    st.write("Upcoming: " + "; ".join(events[:2]))
            layer2 = item.get("layer2") or {}
            bits = []
            if "sentiment_lexicon" in layer2:
                bits.append(layer2["sentiment_lexicon"].get("label", ""))
            if "speaking_pace" in layer2:
                bits.append(f"{layer2['speaking_pace'].get('words_per_minute')} wpm")
            if "rms_energy" in layer2 and "rms_mean" in layer2["rms_energy"]:
                bits.append(f"rms {layer2['rms_energy']['rms_mean']}")
            if bits:
                st.caption(" · ".join(str(bit) for bit in bits if bit))
            if st.button("Transcript", key=f"open-{item['id']}"):
                st.session_state["open_file"] = item["id"]
            if st.session_state.get("open_file") == item["id"]:
                try:
                    detail = client().get_file(item["id"])
                except ApiError as exc:
                    show_error(exc)
                else:
                    st.text_area("Transcript", detail.get("transcript") or "", height=160)
            if st.button("Delete", key=f"del-{item['id']}"):
                try:
                    client().delete_file(item["id"])
                except ApiError as exc:
                    show_error(exc)
                else:
                    st.rerun()
    if pending:
        st.session_state["polls"] = st.session_state.get("polls", 0) + 1
        if st.session_state["polls"] < 20:
            time.sleep(1.2)
            st.rerun()
    else:
        st.session_state["polls"] = 0
