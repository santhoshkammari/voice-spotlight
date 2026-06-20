"""
Persistent session management.

Sessions stored in ~/.voice-spotlight/sessions/ as JSON.
Each session: { id, title, started, messages: [{role, content}] }
Current session pointer: ~/.voice-spotlight/current_session.txt
"""

from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from typing import Optional

SESSIONS_DIR = Path.home() / ".voice-spotlight" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_PTR  = Path.home() / ".voice-spotlight" / "current_session.txt"

OPENCODE_MAX_TOKENS = 32000   # conservative default for DeepSeek via opencode


def _session_path(sid: str) -> Path:
    return SESSIONS_DIR / f"{sid}.json"


def _write(sid: str, data: dict) -> None:
    _session_path(sid).write_text(json.dumps(data, indent=2))


def _read(sid: str) -> Optional[dict]:
    p = _session_path(sid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ── current session ───────────────────────────────────────────────────────────

def current_id() -> Optional[str]:
    if CURRENT_PTR.exists():
        sid = CURRENT_PTR.read_text().strip()
        if _session_path(sid).exists():
            return sid
    return None


def _set_current(sid: str) -> None:
    CURRENT_PTR.write_text(sid)


def load_current() -> dict:
    """Load current session, creating one if none exists."""
    sid = current_id()
    if sid:
        data = _read(sid)
        if data:
            return data
    return new_session()


def new_session(title: str = "") -> dict:
    """Start a fresh session, saving the old one first."""
    sid = uuid.uuid4().hex[:8]
    data = {
        "id": sid,
        "title": title or f"Session {time.strftime('%Y-%m-%d %H:%M')}",
        "started": time.time(),
        "messages": [],
    }
    _write(sid, data)
    _set_current(sid)
    return data


def save_messages(sid: str, messages: list[dict]) -> None:
    data = _read(sid)
    if not data:
        return
    data["messages"] = messages
    # auto-title from first user message
    if not data["title"].startswith("Session") and data["title"]:
        pass
    elif messages:
        first = next((m["content"] for m in messages if m["role"] == "user"), "")
        if first:
            data["title"] = first[:60]
    _write(sid, data)


def list_sessions(n: int = 20) -> list[dict]:
    sessions = []
    for p in sorted(SESSIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:n]:
        try:
            d = json.loads(p.read_text())
            sessions.append({
                "id": d["id"],
                "title": d.get("title", "?"),
                "started": d.get("started"),
                "message_count": len(d.get("messages", [])),
            })
        except Exception:
            pass
    return sessions


def read_session(sid: str) -> Optional[dict]:
    return _read(sid)


# ── token counting (rough estimate: 1 token ≈ 4 chars) ───────────────────────

def estimate_tokens(messages: list[dict]) -> int:
    total = sum(len(m.get("content", "")) for m in messages)
    return total // 4


def compress_if_needed(messages: list[dict], max_tokens: int, threshold: float = 0.75) -> list[dict]:
    """Semantic compression at 75% of max tokens — summarises old messages via LLM."""
    if estimate_tokens(messages) < max_tokens * threshold:
        return messages
    from compressagent import compress_messages
    return compress_messages(messages, keep_last=6)
