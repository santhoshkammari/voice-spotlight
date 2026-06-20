"""
Subagent orchestration — detached subprocess workers.

Each agent runs as an independent process, output streamed to a log file.
State tracked via JSON files in ~/.voice-spotlight/agents/.

API:
  launch(name, prompt, cmd=None)  → agent_id
  status(agent_id)                → dict
  list_all()                      → list[dict]
  kill(agent_id)
  read_output(agent_id, tail=50)  → str
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

AGENTS_DIR = Path.home() / ".voice-spotlight" / "agents"
AGENTS_DIR.mkdir(parents=True, exist_ok=True)


def _meta_path(agent_id: str) -> Path:
    return AGENTS_DIR / f"{agent_id}.json"

def _log_path(agent_id: str) -> Path:
    return AGENTS_DIR / f"{agent_id}.log"


def _write_meta(agent_id: str, data: dict) -> None:
    _meta_path(agent_id).write_text(json.dumps(data, indent=2))

def _read_meta(agent_id: str) -> Optional[dict]:
    p = _meta_path(agent_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _refresh_status(agent_id: str) -> dict:
    meta = _read_meta(agent_id)
    if not meta or not isinstance(meta, dict):
        return {"agent_id": agent_id, "status": "unknown"}
    if meta.get("status") == "running":
        pid = meta.get("pid")
        if pid and not _is_alive(pid):
            # re-read meta — worker may have updated status itself before dying
            meta = _read_meta(agent_id) or meta
            if not isinstance(meta, dict):
                meta = {"agent_id": agent_id}
            if meta.get("status") == "running":
                # still running in meta but pid dead — check sentinel
                sentinel = AGENTS_DIR / f"{agent_id}.exit"
                if sentinel.exists():
                    code = sentinel.read_text().strip()
                    meta["status"] = "completed" if code == "0" else "failed"
                else:
                    meta["status"] = "failed"
                _write_meta(agent_id, meta)
    return meta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

PYTHON = "/home/ntlpt24/main/bin/python3"
WORKER = str(Path(__file__).parent / "worker.py")


def launch(
    name: str,
    prompt: str,
    cmd: Optional[list[str]] = None,
    cwd: Optional[str] = None,
) -> str:
    """Spawn a detached autonomous agent via worker.py.

    worker.py runs the full GPU/opencode agent loop independently.
    Returns agent_id. Survives parent death.
    """
    agent_id = f"{name}-{uuid.uuid4().hex[:6]}"
    log = _log_path(agent_id)

    # write initial meta so HUD sees it immediately
    meta = {
        "agent_id": agent_id,
        "name": name,
        "prompt": prompt[:300],
        "pid": None,
        "status": "running",
        "started": time.time(),
        "output": "",
        "log": str(log),
        "type": "agent",
    }
    _write_meta(agent_id, meta)

    actual_cmd = cmd or [PYTHON, WORKER, agent_id, name, prompt]

    log_fd = open(log, "w")
    proc = subprocess.Popen(
        actual_cmd,
        stdout=log_fd,
        stderr=log_fd,
        stdin=subprocess.DEVNULL,
        cwd=cwd or str(Path.home()),
        start_new_session=True,
    )
    log_fd.close()

    meta["pid"] = proc.pid
    _write_meta(agent_id, meta)
    return agent_id


def status(agent_id: str) -> dict:
    return _refresh_status(agent_id)


def list_all(*, include_completed: bool = True) -> list[dict]:
    agents = []
    for p in sorted(AGENTS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        # skip session history files and tmp files
        if ".session." in p.name or p.suffix != ".json" or p.stem.endswith(".session"):
            continue
        agent_id = p.stem
        try:
            meta = _refresh_status(agent_id)
        except Exception:
            continue
        if not isinstance(meta, dict) or not meta.get("agent_id") and not meta.get("name"):
            continue
        status = meta.get("status", "unknown")
        if not include_completed and status not in ("running",):
            continue
        agents.append({
            "agent_id": agent_id,
            "name": meta.get("name", agent_id),
            "status": status,
            "started": meta.get("started"),
            "prompt_preview": meta.get("prompt", "")[:80],
        })
    return agents


def kill(agent_id: str) -> bool:
    meta = _read_meta(agent_id)
    if not meta:
        return False
    pid = meta.get("pid")
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass
    meta["status"] = "cancelled"
    _write_meta(agent_id, meta)
    return True


def read_output(agent_id: str, tail: int = 80) -> str:
    meta = _read_meta(agent_id)
    # prefer live output field written by worker
    if meta and meta.get("output"):
        lines = meta["output"].splitlines()
        return "\n".join(lines[-tail:])
    log = _log_path(agent_id)
    if not log.exists():
        return ""
    lines = log.read_text(errors="replace").splitlines()
    return "\n".join(lines[-tail:])


# ---------------------------------------------------------------------------
# Tool definitions for agent.py (pass to all_tools())
# ---------------------------------------------------------------------------

def get_tools() -> list[dict]:
    """Return OpenAI-style tool defs for the main agent."""
    return [
        {
            "type": "function",
            "name": "subagent_launch",
            "description": (
                "Launch a detached background agent to run a task independently. "
                "Returns agent_id. Use subagent_status to check progress."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short slug e.g. 'scrape-news'"},
                    "prompt": {"type": "string", "description": "Full task description"},
                },
                "required": ["name", "prompt"],
            },
        },
        {
            "type": "function",
            "name": "subagent_status",
            "description": "Check status of a background agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                },
                "required": ["agent_id"],
            },
        },
        {
            "type": "function",
            "name": "subagent_list",
            "description": "List all background agents and their status.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "function",
            "name": "subagent_output",
            "description": "Read the last N lines of a background agent's output log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "tail": {"type": "integer", "default": 50},
                },
                "required": ["agent_id"],
            },
        },
        {
            "type": "function",
            "name": "subagent_kill",
            "description": "Kill a running background agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                },
                "required": ["agent_id"],
            },
        },
    ]


def handle_tool_call(name: str, args: dict) -> str:
    """Dispatch a tool call from the agent loop."""
    if name == "subagent_launch":
        aid = launch(args["name"], args["prompt"])
        return json.dumps({"agent_id": aid, "status": "running"})
    elif name == "subagent_status":
        return json.dumps(status(args["agent_id"]))
    elif name == "subagent_list":
        return json.dumps(list_all())
    elif name == "subagent_output":
        out = read_output(args["agent_id"], tail=args.get("tail", 50))
        return out or "(no output yet)"
    elif name == "subagent_kill":
        ok = kill(args["agent_id"])
        return json.dumps({"killed": ok})
    return json.dumps({"error": f"unknown tool {name}"})
