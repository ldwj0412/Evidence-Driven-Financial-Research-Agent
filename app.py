import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import streamlit as st

from financial_research.domain import Clarification
from financial_research.fixtures import EXAMPLES, PROMPTS
from financial_research.presentation import workspace_view
from financial_research.service import ResearchService

ASSETS = Path(__file__).with_name("financial_research") / "ui"

st.set_page_config(page_title="Financial Research Agent", layout="wide")
st.html("""
<style>
[data-testid="stHeader"], #MainMenu { display: none; }
[data-testid="stMainBlockContainer"] { max-width: none; padding: 0; }
</style>
""")


@st.cache_resource
def runtime():
    service = ResearchService(db=os.environ.get("FINANCIAL_RESEARCH_DB"), recover=True)
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="investigation")
    if not service.store.selection():
        service.store.new_session()
    return service, worker


@st.cache_resource
def register_component(css, javascript):
    return st.components.v2.component(
        "research_workspace", html='<div class="research-app"></div>', css=css, js=javascript
    )


service, worker = runtime()
component = register_component(
    (ASSETS / "style.css").read_text(encoding="utf-8"),
    (ASSETS / "workspace.js").read_text(encoding="utf-8"),
)


def submit(turn, answers=None):
    return asyncio.run(service.execute(turn, answers))


def handle(event, selection, selected):
    command = event.get("command")
    if command == "mode" and event.get("mode") in {"replay", "live"}:
        st.session_state.mode = event["mode"]
    elif command == "new":
        service.store.new_session()
    elif command == "delete_session" and event.get("id") == selection["session_id"]:
        service.delete_session(selection["session_id"])
    elif command == "session":
        session = event.get("id")
        if session not in {item["id"] for item in service.store.sessions()}:
            raise ValueError("Select an existing session.")
        turns = service.store.turns(session)
        service.store.select(session, turns[-1]["id"] if turns else None)
    elif command == "turn":
        service.store.select(selection["session_id"], event.get("id"))
    elif command == "run":
        mode = st.session_state.mode
        if mode == "live":
            settings = service.settings
            if not settings.model_ready:
                raise ValueError(settings.model_error + " You can also choose Fixture replay.")
        parent = (
            selected["id"]
            if selected and selected["status"] == "Complete" and selected["mode"] == mode
            else None
        )
        turn = service.start(selection["session_id"], event.get("question"), mode, parent)
        st.session_state.worker_future = worker.submit(submit, turn)
    elif command == "clarify":
        if not selected or selected["status"] != "Waiting for clarification":
            raise ValueError("Open the pending clarification before answering.")
        answers = Clarification.model_validate(selected["clarification"]).validate_answers(
            event.get("answers") or {}
        )
        st.session_state.worker_future = worker.submit(submit, selected["id"], answers)


if "mode" not in st.session_state:
    st.session_state.mode = "live" if service.settings.model_ready else "replay"


def in_progress(turn):
    future = st.session_state.get("worker_future")
    return bool(turn and turn["status"] in {"Queued", "Running"} or future and not future.done())


active_turn = service.store.selection()["turn_id"]
polling = in_progress(service.store.turn(active_turn) if active_turn else None)


@st.fragment(run_every=0.35 if polling else None)
def research_workspace():
    selection = service.store.selection()
    selected = service.view(selection["turn_id"]) if selection["turn_id"] else None
    if polling and not in_progress(selected):
        st.rerun()
    result = component(
        key="research-workspace",
        on_action_change=lambda: None,
        data={
            "mode": st.session_state.mode,
            "selection": selection,
            "sessions": service.store.sessions(),
            "turns": service.store.turns(selection["session_id"]),
            "selected": workspace_view(selected),
            "messages": list(service.settings.messages),
            "examples": [{"label": label, "question": PROMPTS[key]} for label, key in EXAMPLES.items()],
            "error": st.session_state.get("workspace_error", ""),
            "acknowledged": st.session_state.get("last_nonce"),
        },
    )
    event = result.get("action")
    if not isinstance(event, dict) or event.get("nonce") == st.session_state.get("last_nonce"):
        return
    st.session_state.last_nonce = event.get("nonce")
    st.session_state.workspace_error = ""
    try:
        handle(event, selection, selected)
    except ValueError as exc:
        st.session_state.workspace_error = service.settings.redact(str(exc))
    st.rerun()


research_workspace()
