"""
Semantic context compression.

Takes a list of messages, passes the old ones (everything except last 6)
to an LLM, gets back a structured catch-up summary:
- what we were working on / intent
- decisions made, solutions found
- ideas discussed
- current state / progress

Returns: system msgs + [{"role":"system","content":"<summary>"}] + last 6 msgs
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

BASE_URL = "http://192.168.170.49:8077/v1"
MODEL_ID = "/home/ng6355/models/qwen3-6-27b"
OPENCODE_MAX_TOKENS = 32_000

COMPRESS_PROMPT = """\
You are a context compressor. Below is a conversation history that needs to be summarized.
Extract ONLY what matters for continuing the conversation:
- What was being worked on (task/intent)
- Key decisions made or solutions found
- Important ideas, plans, or approaches discussed
- Current progress / state (what's done, what's pending)
- Any critical facts, file paths, commands, or outputs that are still relevant

Output a dense paragraph or two. No filler. No "the user said..." narration.
Write it as a compact briefing that lets someone pick up exactly where we left off.

CONVERSATION TO COMPRESS:
{conversation}
"""


def _fmt_messages(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m["role"].upper()
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        lines.append(f"[{role}]\n{content}")
    return "\n\n".join(lines)


def _gpu_reachable() -> bool:
    import socket
    try:
        s = socket.create_connection(("192.168.170.49", 8077), timeout=2)
        s.close()
        return True
    except OSError:
        return False


async def _compress_via_gpu(conversation: str) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=BASE_URL, api_key="x", timeout=120.0)
    resp = await client.chat.completions.create(
        model=MODEL_ID,
        messages=[{"role": "user", "content": COMPRESS_PROMPT.format(conversation=conversation)}],
        temperature=0,
        max_tokens=1024,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return resp.choices[0].message.content.strip()


def _compress_via_opencode(conversation: str) -> str:
    import subprocess
    prompt = COMPRESS_PROMPT.format(conversation=conversation)
    cmd = [
        "/home/ntlpt24/.opencode/bin/opencode", "run", prompt,
        "--model", "opencode/deepseek-v4-flash-free",
        "--format", "json",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)
    full = ""
    for line in proc.stdout:
        try:
            obj = json.loads(line)
            if obj.get("type") == "text":
                full += obj["part"]["text"]
        except Exception:
            pass
    proc.wait()
    return full.strip()


def compress(conversation: str) -> str:
    """Run compression synchronously, auto-picks GPU or opencode."""
    if _gpu_reachable():
        return asyncio.run(_compress_via_gpu(conversation))
    return _compress_via_opencode(conversation)


def compress_messages(
    messages: list[dict],
    keep_last: int = 6,
    threshold_tokens: int = 0,
) -> list[dict]:
    """
    Compress messages when there's enough old history to compress.

    Splits into:
      system_msgs          — kept as-is
      to_compress          — everything except last `keep_last` non-system msgs
      tail                 — last `keep_last` non-system msgs (kept verbatim)

    If to_compress is empty (not enough history), returns messages unchanged.
    Returns: system_msgs + summary_msg + tail
    """
    system_msgs = [m for m in messages if m["role"] == "system"]
    non_system  = [m for m in messages if m["role"] != "system"]

    if len(non_system) <= keep_last:
        return messages  # nothing old enough to compress

    to_compress = non_system[:-keep_last]
    tail        = non_system[-keep_last:]

    conversation = _fmt_messages(to_compress)
    summary = compress(conversation)

    summary_msg = {
        "role": "system",
        "content": f"[CONTEXT SUMMARY — earlier conversation]\n{summary}",
    }

    return system_msgs + [summary_msg] + tail
