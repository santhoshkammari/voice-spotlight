"""
Voice-HUD agent — Qwen3-27B primary, opencode fallback.
Simple: manual history list, plain asyncio.run, no SQLiteSession.
"""

import asyncio
import json
import threading
from pathlib import Path
from typing import Callable

from openai import AsyncOpenAI
from agents import (
    Agent, Runner,
    OpenAIChatCompletionsModel,
    ModelSettings,
    set_default_openai_client,
    set_tracing_disabled,
)
from agents.stream_events import RawResponsesStreamEvent
from openai.types.responses import ResponseTextDeltaEvent
from agents import function_tool as tool
from typing import Annotated

from tools import all_tools
import session as sess_mgr

import os

BASE_URL       = os.environ.get("LLM_BASE_URL", "http://192.168.170.49:8077/v1")
MODEL_ID       = os.environ.get("LLM_MODEL_ID", "/home/ng6355/models/qwen3-6-27b")
MAX_TOKENS     = 200000   # conservative cap, no HTTP fetch needed
MAX_HISTORY    = 20       # messages to keep

set_tracing_disabled(True)

_client = AsyncOpenAI(base_url=BASE_URL, api_key="x", timeout=300.0)
set_default_openai_client(_client)

MODEL = OpenAIChatCompletionsModel(model=MODEL_ID, openai_client=_client)
SETTINGS = ModelSettings(
    temperature=0,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)


# ── session tools ─────────────────────────────────────────────────────────────

@tool
def list_sessions_tool() -> str:
    """List past conversation sessions."""
    sessions = sess_mgr.list_sessions()
    if not sessions:
        return "No past sessions."
    import time
    lines = []
    for s in sessions:
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started"])) if s.get("started") else "?"
        lines.append(f"[{s['id']}] {t} — {s['title']} ({s['message_count']} msgs)")
    return "\n".join(lines)


@tool
def read_session_tool(
    session_id: Annotated[str, "Session ID from list_sessions_tool"],
) -> str:
    """Read messages from a past session."""
    data = sess_mgr.read_session(session_id)
    if not data:
        return f"Session {session_id} not found."
    lines = [f"Session: {data['title']}"]
    for m in data.get("messages", []):
        content = m["content"][:300] + ("…" if len(m["content"]) > 300 else "")
        lines.append(f"\n[{m['role'].upper()}] {content}")
    return "\n".join(lines)


@tool
def new_session_tool() -> str:
    """Start a fresh conversation session."""
    _reset_session()
    return "Started new session."


_agent = Agent(
    name="voice-hud",
    instructions=(
        "You are Santhosh's personal AI assistant running on his laptop. "
        "You have full access to his system via bash, file read/write, web search, and screenshot tools. "
        "You remember past conversations — use list_sessions_tool / read_session_tool to recall them. "
        "Call new_session_tool if asked to reset. "
        "Be concise and direct. No filler."
    ),
    model=MODEL,
    model_settings=SETTINGS,
    tools=all_tools() + [list_sessions_tool, read_session_tool, new_session_tool],
)


# ── history ───────────────────────────────────────────────────────────────────

_history: list[dict] = []
_history_lock = threading.Lock()
_current_session: dict = {}


def _load_session() -> None:
    global _current_session, _history
    data = sess_mgr.load_current()
    _current_session = data
    _history = list(data.get("messages", []))


def _save() -> None:
    sid = _current_session.get("id")
    if sid:
        sess_mgr.save_messages(sid, list(_history))


def _reset_session() -> None:
    global _current_session, _history
    with _history_lock:
        _save()
        _current_session = sess_mgr.new_session()
        _history = []


_load_session()


# ── streaming ─────────────────────────────────────────────────────────────────

async def _run_stream(
    question: str,
    on_token: Callable[[str], None],
    cancel_event=None,
) -> None:
    with _history_lock:
        msgs = list(_history)

    # simple rolling window — no HTTP fetch for token count
    while len(msgs) > MAX_HISTORY:
        msgs.pop(0)

    input_msgs = msgs + [{"role": "user", "content": question}]
    full = ""
    interrupted = False

    stream = Runner.run_streamed(_agent, input_msgs)
    async for event in stream.stream_events():
        if cancel_event and cancel_event.is_set() and not interrupted:
            interrupted = True
            stream.cancel()
        if isinstance(event, RawResponsesStreamEvent):
            data = event.data
            if isinstance(data, ResponseTextDeltaEvent):
                full += data.delta
                if not interrupted:
                    on_token(full)

    if interrupted and full:
        full += "\n\n*[interrupted]*"
        on_token(full)

    with _history_lock:
        _history.append({"role": "user",      "content": question})
        _history.append({"role": "assistant",  "content": full})
        while len(_history) > MAX_HISTORY:
            _history.pop(0)
        _save()


def _gpu_reachable() -> bool:
    import socket
    try:
        s = socket.create_connection(("192.168.170.49", 8077), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def _opencode_stream(
    question: str,
    on_token: Callable[[str], None],
    cancel_event=None,
) -> None:
    import subprocess
    cmd = [
        "/home/ntlpt24/.opencode/bin/opencode", "run", question,
        "--model", "opencode/deepseek-v4-flash-free",
        "--format", "json",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, cwd="/home/ntlpt24")
    full = ""
    interrupted = False
    for line in proc.stdout:
        if cancel_event and cancel_event.is_set():
            interrupted = True
            proc.kill()
            break
        try:
            obj = json.loads(line)
            if obj.get("type") == "text":
                full += obj["part"]["text"]
                if not interrupted:
                    on_token(full)
        except Exception:
            pass
    proc.wait()

    if interrupted and full:
        full += "\n\n*[interrupted]*"
        on_token(full)

    with _history_lock:
        _history.append({"role": "user",      "content": question})
        _history.append({"role": "assistant",  "content": full})
        while len(_history) > MAX_HISTORY:
            _history.pop(0)
        _save()


def stream_answer(
    question: str,
    on_token: Callable[[str], None],
    on_done: Callable[[], None],
    cancel_event=None,
) -> None:
    def _go():
        try:
            if _gpu_reachable():
                asyncio.run(_run_stream(question, on_token, cancel_event))
            else:
                on_token("[GPU offline → opencode]\n\n")
                _opencode_stream(question, on_token, cancel_event)
        except Exception as e:
            on_token(f"[error: {e}]")
        finally:
            on_done()

    threading.Thread(target=_go, daemon=True).start()
