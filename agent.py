"""
MAF agent wired to GPU4 Qwen3-27B.
Streams token deltas via Runner.run_streamed().
"""

import asyncio
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

BASE_URL = "http://192.168.170.49:8077/v1"
MODEL_ID = "/home/ng6355/models/qwen3-6-27b"

set_tracing_disabled(True)

_client = AsyncOpenAI(base_url=BASE_URL, api_key="x", timeout=300.0)
set_default_openai_client(_client)

MODEL = OpenAIChatCompletionsModel(model=MODEL_ID, openai_client=_client)

SETTINGS = ModelSettings(
    temperature=0,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)

_agent = Agent(
    name="voice-hud",
    instructions=(
        "You are Santhosh's personal AI assistant running on his laptop. "
        "You have full access to his system via bash, file read/write, web search, and screenshot tools. "
        "Be concise and direct. No filler. "
        "When he asks about his screen or desktop, use the screenshot tool. "
        "When you take a screenshot, describe what you see in the image path result."
    ),
    model=MODEL,
    model_settings=SETTINGS,
    tools=all_tools(),
)

_history: list[dict] = []
_history_lock = threading.Lock()


async def _run_stream(question: str, on_token: Callable[[str], None]) -> None:
    """Run one turn, call on_token for each streamed delta."""
    with _history_lock:
        history_snapshot = list(_history)

    input_msgs = history_snapshot + [{"role": "user", "content": question}]
    full = ""

    stream = Runner.run_streamed(_agent, input_msgs)
    async for event in stream.stream_events():
        if isinstance(event, RawResponsesStreamEvent):
            data = event.data
            if isinstance(data, ResponseTextDeltaEvent):
                full += data.delta
                on_token(full)   # HUD expects cumulative text each call

    with _history_lock:
        _history.append({"role": "user",      "content": question})
        _history.append({"role": "assistant",  "content": full})
        # rolling 20-message window
        while len(_history) > 20:
            _history.pop(0)


def stream_answer(question: str, on_token: Callable[[str], None], on_done: Callable[[], None]) -> None:
    """
    Blocking call — runs in a background thread.
    Calls on_token(cumulative_text) for each delta, on_done() when finished.
    """
    async def _go():
        try:
            await _run_stream(question, on_token)
        except Exception as e:
            on_token(f"[agent error: {e}]")
        finally:
            on_done()

    asyncio.run(_go())
