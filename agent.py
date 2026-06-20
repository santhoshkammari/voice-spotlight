"""
MAF agent wired to Qwen3-27B with session persistence + auto context compression.
"""

import asyncio
import json
import threading
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

from tools import all_tools
import session as sess

import os

BASE_URL  = os.environ.get("LLM_BASE_URL", "http://192.168.170.49:8077/v1")
MODEL_ID  = os.environ.get("LLM_MODEL_ID", "/home/ng6355/models/qwen3-6-27b")
VLM_MAX_TOKENS = 250000   # fetched from /v1/models at startup

set_tracing_disabled(True)

_client = AsyncOpenAI(base_url=BASE_URL, api_key="x", timeout=300.0)
set_default_openai_client(_client)

MODEL = OpenAIChatCompletionsModel(model=MODEL_ID, openai_client=_client)
SETTINGS = ModelSettings(
    temperature=0,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)


# ── session tools (agent can list/read/reset sessions) ────────────────────────

from agents import function_tool as tool
from typing import Annotated

@tool
def list_sessions_tool() -> str:
    """List past conversation sessions."""
    sessions = sess.list_sessions()
    if not sessions:
        return "No past sessions."
    lines = []
    for s in sessions:
        import time
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started"])) if s.get("started") else "?"
        lines.append(f"[{s['id']}] {t} — {s['title']} ({s['message_count']} msgs)")
    return "\n".join(lines)


@tool
def read_session_tool(
    session_id: Annotated[str, "Session ID from list_sessions_tool"],
) -> str:
    """Read the messages from a past session."""
    data = sess.read_session(session_id)
    if not data:
        return f"Session {session_id} not found."
    msgs = data.get("messages", [])
    lines = [f"Session: {data['title']}"]
    for m in msgs:
        role = m["role"].upper()
        content = m["content"][:300] + ("…" if len(m["content"]) > 300 else "")
        lines.append(f"\n[{role}] {content}")
    return "\n".join(lines)


@tool
def new_session_tool() -> str:
    """Start a fresh conversation session, archiving the current one."""
    _reset_session()
    return "Started new session."


def _all_tools():
    return all_tools() + [list_sessions_tool, read_session_tool, new_session_tool]


_agent = Agent(
    name="voice-hud",
    instructions=(
        "You are Santhosh's personal AI assistant running on his laptop. "
        "You have full access to his system via bash, file read/write, web search, and screenshot tools. "
        "You remember past conversations — use list_sessions_tool / read_session_tool to recall them. "
        "Say 'starting fresh session' then call new_session_tool if asked to reset. "
        "Be concise and direct. No filler."
    ),
    model=MODEL,
    model_settings=SETTINGS,
    tools=_all_tools(),
)


# ── session state ─────────────────────────────────────────────────────────────

_session_lock = threading.Lock()
_current_session: dict = {}
_history: list[dict] = []


def _load_session() -> None:
    global _current_session, _history
    data = sess.load_current()
    _current_session = data
    _history = list(data.get("messages", []))


def _save() -> None:
    sid = _current_session.get("id")
    if sid:
        sess.save_messages(sid, list(_history))


def _reset_session() -> None:
    global _current_session, _history
    with _session_lock:
        _save()
        data = sess.new_session()
        _current_session = data
        _history = []


# load on import
_load_session()


# ── VLM max tokens (fetched once) ─────────────────────────────────────────────

def _fetch_vlm_max_tokens() -> int:
    try:
        import httpx
        r = httpx.get(f"{BASE_URL}/models", timeout=3)
        data = r.json()
        for m in data.get("data", []):
            if "max_model_len" in m:
                return int(m["max_model_len"])
    except Exception:
        pass
    return VLM_MAX_TOKENS


_vlm_max = _fetch_vlm_max_tokens()


# ── streaming ─────────────────────────────────────────────────────────────────

async def _run_stream(question: str, on_token: Callable[[str], None]) -> None:
    with _session_lock:
        msgs = sess.compress_if_needed(list(_history), _vlm_max)
        input_msgs = msgs + [{"role": "user", "content": question}]

    full = ""
    stream = Runner.run_streamed(_agent, input_msgs)
    async for event in stream.stream_events():
        if isinstance(event, RawResponsesStreamEvent):
            data = event.data
            if isinstance(data, ResponseTextDeltaEvent):
                full += data.delta
                on_token(full)

    with _session_lock:
        _history.append({"role": "user",      "content": question})
        _history.append({"role": "assistant",  "content": full})
        _save()


def _gpu_reachable() -> bool:
    import socket
    try:
        s = socket.create_connection(("192.168.170.49", 8077), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def _opencode_stream(question: str, on_token: Callable[[str], None]) -> None:
    cmd = [
        "/home/ntlpt24/.opencode/bin/opencode", "run", question,
        "--model", "opencode/deepseek-v4-flash-free",
        "--format", "json",
    ]
    import subprocess
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            cwd="/home/ntlpt24")
    full = ""
    for line in proc.stdout:
        try:
            obj = json.loads(line)
            if obj.get("type") == "text":
                full += obj["part"]["text"]
                on_token(full)
        except Exception:
            pass
    proc.wait()
    with _session_lock:
        _history.append({"role": "user",      "content": question})
        _history.append({"role": "assistant",  "content": full})
        # compress opencode history at 75% of its max
        compressed = sess.compress_if_needed(_history, sess.OPENCODE_MAX_TOKENS)
        _history.clear()
        _history.extend(compressed)
        _save()


def stream_answer(question: str, on_token: Callable[[str], None], on_done: Callable[[], None]) -> None:
    def _go():
        try:
            if _gpu_reachable():
                asyncio.run(_run_stream(question, on_token))
            else:
                on_token("[GPU offline → opencode]\n\n")
                _opencode_stream(question, on_token)
        except Exception as e:
            on_token(f"[error: {e}]")
        finally:
            on_done()

    threading.Thread(target=_go, daemon=True).start()
