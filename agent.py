"""
Voice-HUD agent — Qwen3-27B primary, opencode fallback.

Uses framework-native SQLiteSession for persistent history + compaction.
Cancellation via stream.cancel() (framework-native).
"""

import asyncio
import json
import threading
from pathlib import Path
from typing import Callable, Optional

from openai import AsyncOpenAI
from agents import (
    Agent, Runner,
    OpenAIChatCompletionsModel,
    ModelSettings,
    set_default_openai_client,
    set_tracing_disabled,
)
from agents.memory.sqlite_session import SQLiteSession
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from openai.types.responses import ResponseTextDeltaEvent

from tools import all_tools
import session as sess_mgr

import os

BASE_URL        = os.environ.get("LLM_BASE_URL", "http://192.168.170.49:8077/v1")
MODEL_ID        = os.environ.get("LLM_MODEL_ID", "/home/ng6355/models/qwen3-6-27b")
VLM_MAX_TOKENS  = 250000
DB_PATH         = Path.home() / ".voice-spotlight" / "sessions.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

set_tracing_disabled(True)

_client = AsyncOpenAI(base_url=BASE_URL, api_key="x", timeout=300.0)
set_default_openai_client(_client)

MODEL = OpenAIChatCompletionsModel(model=MODEL_ID, openai_client=_client)
SETTINGS = ModelSettings(
    temperature=0,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)


# ── session tools ─────────────────────────────────────────────────────────────

from agents import function_tool as tool
from typing import Annotated

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
    """Read the messages from a past session."""
    data = sess_mgr.read_session(session_id)
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


# ── SQLite session (framework-native persistence) ─────────────────────────────

_session_lock  = threading.Lock()
_current_sid   = ""
_sql_session: Optional[SQLiteSession] = None


def _get_or_create_session() -> SQLiteSession:
    global _current_sid, _sql_session
    # load/create the meta session pointer
    meta = sess_mgr.load_current()
    sid  = meta["id"]
    if _sql_session is None or _current_sid != sid:
        _sql_session = SQLiteSession(session_id=sid, db_path=DB_PATH)
        _current_sid = sid
    return _sql_session


def _reset_session() -> None:
    global _sql_session, _current_sid
    with _session_lock:
        sess_mgr.new_session()
        _sql_session  = None
        _current_sid  = ""


# init on import
_get_or_create_session()


# ── VLM max tokens ────────────────────────────────────────────────────────────

def _fetch_vlm_max_tokens() -> int:
    try:
        import httpx
        r = httpx.get(f"{BASE_URL}/models", timeout=3)
        for m in r.json().get("data", []):
            if "max_model_len" in m:
                return int(m["max_model_len"])
    except Exception:
        pass
    return VLM_MAX_TOKENS

_vlm_max = _fetch_vlm_max_tokens()


# ── GPU / Qwen3 streaming ─────────────────────────────────────────────────────

async def _run_stream(
    question: str,
    on_token: Callable[[str], None],
    cancel_event=None,
) -> None:
    sql = _get_or_create_session()
    full = ""
    interrupted = False

    # pass session= so framework handles history + compaction automatically
    stream = Runner.run_streamed(_agent, question, session=sql)

    async for event in stream.stream_events():
        if cancel_event and cancel_event.is_set() and not interrupted:
            interrupted = True
            stream.cancel()   # framework-native immediate cancel
        if isinstance(event, RawResponsesStreamEvent):
            data = event.data
            if isinstance(data, ResponseTextDeltaEvent):
                full += data.delta
                if not interrupted:
                    on_token(full)

    if interrupted and full:
        full += "\n\n*[interrupted]*"
        on_token(full)


# ── GPU reachability ──────────────────────────────────────────────────────────

def _gpu_reachable() -> bool:
    import socket
    try:
        s = socket.create_connection(("192.168.170.49", 8077), timeout=2)
        s.close()
        return True
    except OSError:
        return False


# ── opencode fallback ─────────────────────────────────────────────────────────

# separate history list for opencode (no SQLiteSession support there)
_oc_history: list[dict] = []
_oc_lock = threading.Lock()

def _opencode_stream(
    question: str,
    on_token: Callable[[str], None],
    cancel_event=None,
) -> None:
    import subprocess
    from session import compress_if_needed, OPENCODE_MAX_TOKENS

    with _oc_lock:
        msgs = compress_if_needed(list(_oc_history), OPENCODE_MAX_TOKENS)
        # opencode takes the last user message as the prompt; history via context prefix
        context = "\n".join(
            f"{'User' if m['role']=='user' else 'Assistant'}: {m['content']}"
            for m in msgs[-10:]
        )
        full_prompt = f"{context}\nUser: {question}" if context else question

    cmd = [
        "/home/ntlpt24/.opencode/bin/opencode", "run", full_prompt,
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

    with _oc_lock:
        _oc_history.append({"role": "user",      "content": question})
        _oc_history.append({"role": "assistant",  "content": full})
        compressed = compress_if_needed(_oc_history, OPENCODE_MAX_TOKENS)
        _oc_history.clear()
        _oc_history.extend(compressed)


# ── public API ────────────────────────────────────────────────────────────────

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
