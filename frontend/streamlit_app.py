import json
import os
from pathlib import Path

import streamlit as st
from api_client import ApiClient, ApiError
from assistant_ui import render_assistant

# Must be the first Streamlit call on the page: title, icon, layout.
st.set_page_config(
    page_title="Audio Analytics",
    page_icon="🎙",
    layout="centered",
    initial_sidebar_state="collapsed",
)
# Compact CSS for the login page, injected with st.markdown(unsafe_allow_html=True).
# Streamlit now tags layout blocks with data-testid attributes; the old
# "section.main > div.block-container" selector no longer matches anything.
CLASSIC_CSS = """
    <style>
    [data-testid="stMainBlockContainer"] {
        max-width: 440px;
        padding-top: 1.1rem;
        padding-left: 1rem;
        padding-right: 1rem;
    }
    /* Full-width buttons. Streamlit wraps each button in a fit-content element
       container, so widen the wrappers too. Tertiary (link-style) buttons such as
       "Back to Assistant" keep their natural width. */
    [data-testid="stElementContainer"]:has([data-testid="stButton"] button:not([kind="tertiary"])),
    [data-testid="stElementContainer"]:has([data-testid="stFormSubmitButton"]),
    [data-testid="stButton"]:has(button:not([kind="tertiary"])),
    [data-testid="stFormSubmitButton"],
    [data-testid="stFormSubmitButton"] > div {
        width: 100% !important;
    }
    [data-testid="stButton"] button:not([kind="tertiary"]),
    [data-testid="stFormSubmitButton"] button { width: 100% !important; min-height: 44px; }
    [data-testid="stHeading"] h1 {
        font-size: 1.55rem !important; margin-bottom: 0.2rem; padding-bottom: 0.2rem;
    }
    /* 16px inputs stop iOS from zooming in on focus. */
    textarea, input, select { font-size: 16px !important; }
    html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        overflow-x: hidden;
    }
    /* Page names stay whole. The row wraps instead of hiding the last name. */
    [data-testid="stRadio"] [role="radiogroup"] {
        flex-wrap: wrap; overflow: visible; gap: 0.35rem 0.85rem;
    }
    [data-testid="stRadio"] label { white-space: nowrap; }
    /* Phones: use the full width and let wide tables scroll sideways. */
    @media (max-width: 768px) {
        [data-testid="stMainBlockContainer"] {
            max-width: 100%;
            padding-top: 0.6rem;
            padding-left: 0.8rem;
            padding-right: 0.8rem;
        }
        [data-testid="stHeading"] h1 { font-size: 1.35rem !important; }
        [data-testid="stDataFrame"], [data-testid="stTable"] { overflow-x: auto; }
        [data-testid="stTabs"] [role="tablist"] { width: 100%; }
        [data-testid="stTabs"] [role="tab"] { flex: 1 1 0; justify-content: center; }
    }
    </style>
    """

# Backend URL; in Docker Compose this points at the api service.
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
SAMPLE_PATH = Path(__file__).resolve().parent / "samples" / "sample_call.wav"


# st.session_state persists across reruns (Streamlit re-executes this script on every click).
def client() -> ApiClient:
    return ApiClient(API_BASE_URL, st.session_state.get("token"))


def show_error(exc: ApiError) -> None:
    st.error(exc.detail)


# Auth lives only in session_state; nothing is ever read from the URL. Drop any query
# param except ?activity= (the panel preference) so no token-like value lingers there.
for _param in [k for k in st.query_params if k != "activity"]:
    del st.query_params[_param]

# Right after Log out: reload the page once, keeping only ?activity=. The reload starts a
# fresh browser session (no token) and stops fragment timers left from the chat screen.
if st.session_state.pop("va_logged_out", False) and not st.session_state.get("token"):
    _activity = st.query_params.get("activity")
    _target = "?activity=" + ("1" if _activity == "1" else "0") if _activity else ""
    st.html(
        f"<script>window.location.replace(window.location.pathname + {json.dumps(_target)});"
        "</script>",
        unsafe_allow_javascript=True,
    )

# Logged out, this is the sign-in page. Signed in, the assistant is the only screen.
if not st.session_state.get("token"):
    st.markdown(CLASSIC_CSS, unsafe_allow_html=True)
    st.title("Audio Analytics")

# Health check. st.stop() halts the script if the API is down.
try:
    meta = ApiClient(API_BASE_URL).meta()
except ApiError as exc:
    st.error(f"API unavailable at {API_BASE_URL}: {exc.detail}")
    st.stop()
except Exception as exc:
    st.error(f"API unavailable at {API_BASE_URL}: {exc}")
    st.stop()

if not st.session_state.get("token"):
    if meta["llm_provider"] == "mock":
        st.caption("Mock mode is on. Speech-to-text and summaries use deterministic stand-ins.")
    else:
        providers = f"{meta['llm_provider']} / {meta['safety_provider']}"
        st.caption(f"Providers: {providers}.")

# Not logged in: show login/signup tabs and stop rendering the rest of the app.
if not st.session_state.get("token"):
    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])
    with login_tab:
        # st.form batches the inputs: nothing reruns until the submit button is pressed.
        with st.form("login"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            if st.form_submit_button("Log in"):
                try:
                    body = client().login(email, password)
                except ApiError as exc:
                    show_error(exc)
                else:
                    # Keep the access token in session_state so later API calls are authenticated.
                    st.session_state["token"] = body["access_token"]
                    # st.rerun() restarts the script so the assistant renders.
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
                    st.rerun()
    st.stop()

render_assistant(client(), SAMPLE_PATH, meta)
st.stop()
