"""
Detached sub-agent worker process.

Usage:
  python worker.py <agent_id> <name> <prompt>

Runs the full agent loop (GPU/opencode), writes status+output to
~/.voice-spotlight/agents/<agent_id>.json continuously.
Fully independent — survives parent death.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

AGENTS_DIR = Path.home() / ".voice-spotlight" / "agents"
AGENTS_DIR.mkdir(parents=True, exist_ok=True)

WORKER_DIR = Path(__file__).parent
sys.path.insert(0, str(WORKER_DIR))

BASE_URL = os.environ.get("LLM_BASE_URL", "http://192.168.170.49:8077/v1")
MODEL_ID = os.environ.get("LLM_MODEL_ID", "/home/ng6355/models/qwen3-6-27b")
OPENCODE_MAX_TOKENS = 32000


def _meta_path(agent_id: str) -> Path:
    return AGENTS_DIR / f"{agent_id}.json"


def _write_meta(agent_id: str, data: dict) -> None:
    tmp = _meta_path(agent_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.rename(_meta_path(agent_id))


def _read_meta(agent_id: str) -> dict:
    p = _meta_path(agent_id)
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _patch(agent_id: str, **kwargs) -> None:
    meta = _read_meta(agent_id)
    meta.update(kwargs)
    _write_meta(agent_id, meta)


def _gpu_reachable() -> bool:
    import socket
    try:
        s = socket.create_connection(("192.168.170.49", 8077), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def _estimate_tokens(messages: list[dict]) -> int:
    return sum(len(m.get("content", "")) for m in messages) // 4


def _compress(messages: list[dict], max_tokens: int) -> list[dict]:
    if _estimate_tokens(messages) < max_tokens * 0.75:
        return messages
    from compressagent import compress_messages
    return compress_messages(messages, keep_last=6)


# ── GPU / Qwen3 backend ───────────────────────────────────────────────────────

async def _run_gpu(agent_id: str, name: str, prompt: str, history: list[dict]) -> str:
    from openai import AsyncOpenAI
    from agents import Agent as MafAgent, Runner, OpenAIChatCompletionsModel, ModelSettings, set_tracing_disabled
    from agents.stream_events import RawResponsesStreamEvent
    from openai.types.responses import ResponseTextDeltaEvent
    from tools import all_tools

    set_tracing_disabled(True)
    client = AsyncOpenAI(base_url=BASE_URL, api_key="x", timeout=300.0)
    model  = OpenAIChatCompletionsModel(model=MODEL_ID, openai_client=client)
    settings = ModelSettings(temperature=0, extra_body={"chat_template_kwargs": {"enable_thinking": False}})

    agent = MafAgent(
        name=f"worker-{name}",
        instructions=(
            f"You are a focused sub-agent named '{name}'. "
            "Complete your assigned task thoroughly and independently. "
            "You have access to bash, file read/write, web search tools. "
            "Write your work to files when producing artefacts. Be concise in status updates."
        ),
        model=model,
        model_settings=settings,
        tools=all_tools(),
    )

    input_msgs = history + [{"role": "user", "content": prompt}]
    full = ""
    stream = Runner.run_streamed(agent, input_msgs, max_turns=None)
    async for event in stream.stream_events():
        if isinstance(event, RawResponsesStreamEvent):
            data = event.data
            if hasattr(data, "delta"):
                full += data.delta
                _patch(agent_id, output=full[-4000:], updated=time.time())
    return full


# ── opencode fallback ─────────────────────────────────────────────────────────

def _run_opencode(agent_id: str, prompt: str) -> str:
    import subprocess
    cmd = [
        "/home/ntlpt24/.opencode/bin/opencode", "run", prompt,
        "--model", "opencode/deepseek-v4-flash-free",
        "--format", "json",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, cwd=str(Path.home()))
    full = ""
    for line in proc.stdout:
        try:
            obj = json.loads(line)
            if obj.get("type") == "text":
                full += obj["part"]["text"]
                _patch(agent_id, output=full[-4000:], updated=time.time())
        except Exception:
            pass
    proc.wait()
    return full


# ── session for this worker ───────────────────────────────────────────────────

def _worker_session_path(agent_id: str) -> Path:
    return AGENTS_DIR / f"{agent_id}.session.json"


def _load_worker_history(agent_id: str) -> list[dict]:
    p = _worker_session_path(agent_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def _save_worker_history(agent_id: str, history: list[dict]) -> None:
    _worker_session_path(agent_id).write_text(json.dumps(history, indent=2))


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 4:
        print("Usage: worker.py <agent_id> <name> <prompt>")
        sys.exit(1)

    agent_id = sys.argv[1]
    name     = sys.argv[2]
    prompt   = " ".join(sys.argv[3:])

    _patch(agent_id, status="running", output="", updated=time.time())

    history = _load_worker_history(agent_id)

    sentinel = AGENTS_DIR / f"{agent_id}.exit"
    try:
        if _gpu_reachable():
            history = _compress(history, 250000)
            result = asyncio.run(_run_gpu(agent_id, name, prompt, history))
        else:
            _patch(agent_id, output="[GPU offline → opencode]\n")
            result = _run_opencode(agent_id, prompt)
            history = _compress(history, OPENCODE_MAX_TOKENS)

        history.append({"role": "user",      "content": prompt})
        history.append({"role": "assistant",  "content": result})
        _save_worker_history(agent_id, history)
        _patch(agent_id, status="completed", output=result[-4000:], updated=time.time())
        sentinel.write_text("0")

    except Exception as e:
        _patch(agent_id, status="failed", output=f"[error] {e}", updated=time.time())
        sentinel.write_text("1")
        sys.exit(1)


if __name__ == "__main__":
    main()
