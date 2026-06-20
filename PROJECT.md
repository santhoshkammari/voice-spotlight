# Voice Spotlight — Project Context

Read this at the start of every new chat. It tells you what exists, what works, what was fixed, and what NOT to do again.

---

## What This Is

F9 voice HUD overlay. Hold F9 → mic records → Parakeet STT → Qwen3-27B agent (GPU4 fallback opencode/DeepSeek) → answer streams into a floating glass panel on screen. No terminal needed after launch.

---

## Stack

| Layer | Tech |
|---|---|
| UI | PyQt5, frameless window, `QPropertyAnimation`, custom `QPainter` markdown renderer |
| STT | Parakeet TDT ONNX (local, `~/.local/share/com.pais.handy/models/`) |
| Agent | Microsoft Agents SDK (`from agents import Agent, Runner`) |
| Primary LLM | Qwen3-27B via vLLM at `192.168.170.49:8077/v1` |
| Fallback LLM | opencode CLI (`~/.opencode/bin/opencode`) with DeepSeek |
| Session DB | `SQLiteSession` from agents framework → `~/.voice-spotlight/sessions.db` |
| Subagents | `worker.py` spawned via `subprocess` with `start_new_session=True` |
| Python | `/home/ntlpt24/main/bin/python3` — NOT system python |

---

## Files

```
run.py          — daemon entry point, voice loop, F9 key handler, cancel_event
ui.py           — HUD widget (PyQt5), markdown paint, scroll, agent dashboard
agent.py        — LLM streaming, SQLiteSession, cancel, opencode fallback, session tools
stt.py          — Parakeet ONNX transcription + resample
textclean.py    — audio RMS gate + regex hallucination filter
mdrender.py     — lightweight markdown → QPainter (headers, bold, italic, code, bullets)
session.py      — JSON session index for listing/reading past sessions by title
subagent.py     — spawn/track/kill detached agents, read their output
worker.py       — standalone agent process (runs autonomously, writes status to agents dir)
tools.py        — @tool functions: bash, read/write/edit file, glob, grep, web, screenshot, subagent_*
toggle.sh       — Shift+F9 shortcut: kills if running, starts if not
features.md     — running feature checklist
```

State dirs:
- `~/.voice-spotlight/sessions.db` — SQLite conversation history (framework-managed)
- `~/.voice-spotlight/sessions/` — JSON index for session titles/dates (our layer)
- `~/.voice-spotlight/agents/` — subagent meta JSON + logs

---

## Features Shipped

### HUD / UI
- macOS Spotlight glass style: near-black semi-transparent, white text, subtle border, no title bar
- Frameless but normal window — draggable (click-drag), resizable (edge drag), tab-switchable (Alt+Tab)
- Auto-expands height as tokens stream, capped at `MAX_H=520`, then scrolls
- Auto-scroll to bottom while streaming; if user scrolls up mid-stream → stays there (reads while streaming)
- Mouse wheel scroll through full response
- Markdown rendering: `# headers` with accent bar, `**bold**`, `*italic*`, `` `code` `` with dark bg, `- bullets`
- Pulsing red dot while recording mic
- Agent dashboard panel: shows running subagents with pulsing green dot + name + last output snippet
- HUD stays visible while subagents running, auto-hides 5s after all done

### Hotkeys
- **F9 hold** → record, **release** → transcribe + send to LLM
- **F9 press while LLM streaming** → cancels current stream, appends `*[interrupted]*` to partial response
- **Shift+F9** → toggle via `toggle.sh` (OS-level GNOME shortcut, not in-process)

### Agent / LLM
- GPU4/Qwen3 primary, opencode/DeepSeek fallback (auto-detected via socket connect)
- `SQLiteSession` from agents framework — persistent history across restarts, auto-managed by `Runner.run_streamed(session=sql)`
- Context compression at 75% of max tokens (VLM: 250k from `/v1/models`, opencode: 32k hardcoded)
- Session tools the agent can call: `list_sessions_tool`, `read_session_tool`, `new_session_tool`
- Cancellation: `stream.cancel()` — framework-native immediate cancel, not a manual loop-break hack

### Input Filtering
- **Audio gate** (before STT): rejects presses <400ms or RMS energy <0.003 (silence/noise) — no STT cost
- **Text cleanup** (after STT): rejects dots/filler/CJK hallucinations, collapses repeated words/phrases

### Subagents
- `subagent_launch(name, prompt)` → spawns `worker.py` as detached process (`start_new_session=True`)
- Worker runs full agent loop (GPU/opencode + all tools), writes live output to `agents/<id>.json`
- Survives parent death — fully independent process group
- HUD polls every 2s, shows live agent name + last output line
- Tools: `subagent_launch`, `subagent_status`, `subagent_list`, `subagent_output`, `subagent_kill`

---

## Bugs Fixed

| Bug | Fix |
|---|---|
| F9 escape codes `^[[20~` spamming terminal | Detach stdin (`</dev/null`), `nohup` launch, removed `X11BypassWindowManagerHint` |
| Auto-restart on login | Deleted `~/.config/autostart/voice-spotlight.desktop` |
| HUD not draggable/resizable/tab-switchable | Removed `Qt.Tool` + `X11BypassWindowManagerHint`, added mouse drag/resize handlers |
| Empty/silent F9 → unnecessary LLM call | Audio RMS gate + text cleanup layer, both return early |
| STT hallucination (dots, CJK, repeats) | `textclean.py` regex filter — rejects or collapses garbage |
| F9 while streaming → confusion/overlap | `cancel_event` set on F9 press → `stream.cancel()` stops stream, saves partial |
| Content clipped at MAX_H with dead space | Fixed font metric mismatch: `_reflow_height` used FONT_MONO but render used FONT_UI |
| Scroll jumps to bottom while reading | `_user_scrolled` flag — auto-scroll disabled once user wheels up, resets on new response |
| HUD hiding while subagents still running | `_collapse_timer` stopped while `self._agents` non-empty, resumes after all done |

---

## How We Use the Microsoft Agents Framework

```python
from agents import Agent, Runner, OpenAIChatCompletionsModel, ModelSettings
from agents.memory.sqlite_session import SQLiteSession
from agents import function_tool as tool
```

**What we use:**
- `Runner.run_streamed(agent, question, session=sql)` — single call handles history load, compaction, and save
- `SQLiteSession(session_id, db_path)` — file-backed persistent conversation history, thread-safe
- `stream.cancel()` on `StreamedRunResult` — immediate framework-native cancel, drains cleanly
- `RawResponsesStreamEvent` + `ResponseTextDeltaEvent` — token-level streaming
- `@function_tool` decorator — turns Python functions into agent tools with auto schema
- `ModelSettings(extra_body=...)` — passes vLLM-specific params (disable thinking mode)
- `set_tracing_disabled(True)` — suppress framework tracing noise

**What we don't use (available if needed):**
- `RunHooksBase` — lifecycle hooks (`on_tool_start`, `on_tool_end`) — could show tool calls live in HUD
- `RunItemStreamEvent` — fires per tool call/result, richer than raw tokens
- `InputGuardrail` / `OutputGuardrail` — not needed, textclean.py is faster
- `AgentUpdatedStreamEvent` — fires on agent handoff
- `handoffs/` — multi-agent handoff (subagents use subprocess instead)

---

## Do NOT Do These Again

- **Don't use `_read_meta` directly in `run.py` poller** — import via `subagent` module only
- **Don't hardcode `LINE_H=22`** for height calc — use `QFontMetrics(QFont(FONT_UI, 13)).height() + 4`
- **Don't use `FONT_MONO` for layout math** when rendering uses `FONT_UI` — font metrics must match renderer
- **Don't check `cancel_event` after stream loop** — check it inside the loop and call `stream.cancel()`, then keep draining
- **Don't add `*[interrupted]*` unless `full` is non-empty** — no label on empty cancels
- **Don't auto-scroll on every token** if user has manually scrolled — check `_user_scrolled` flag
- **Don't spawn subagents with opencode CLI directly** — use `worker.py` which has full tool access and proper session
- **Don't use system python** — always `/home/ntlpt24/main/bin/python3`
- **Don't restart with `pkill` then immediately launch** — pid file check in `run.py` will block if old process not fully dead; use `sleep 0.3`
- **Don't write `Co-Authored-By` in commits** — user's git config, skip it
- **Don't add autostart** — was removed intentionally, use Shift+F9 toggle instead
- **Don't modify `_history` list directly in `agent.py`** — history is now owned by `SQLiteSession`, the `_oc_history` list is only for opencode fallback path
