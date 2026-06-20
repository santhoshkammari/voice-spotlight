# Voice Spotlight — Features

## Shipped

### UI
- [x] macOS Spotlight-style frosted glass HUD (black/white, transparent)
- [x] Auto-expands height as tokens stream in, collapses when idle
- [x] Draggable window (click-drag anywhere)
- [x] Resizable (drag bottom/left/right edges)
- [x] Tab-switchable — normal window manager entry (Alt+Tab)
- [x] Markdown rendering — headers, **bold**, *italic*, `code`, bullet lists
- [x] Pulsing red dot while recording, pulsing green dots for running subagents

### Hotkeys
- [x] F9 hold → record, release → transcribe + send
- [x] Shift+F9 → toggle (start if down, kill if running) via `toggle.sh`

### Agent
- [x] GPU4/Qwen3-27B primary backend, opencode/DeepSeek fallback
- [x] Persistent sessions — history saved to `~/.voice-spotlight/sessions/`
- [x] Continues same session across restarts
- [x] Semantic context compression at 75% of max tokens (`compressagent.py`) — LLM summarises old messages into a dense catch-up brief (intent, decisions, ideas, progress), keeps system + summary + last 6 msgs verbatim
- [x] Session tools: `list_sessions`, `read_session`, `new_session`
- [x] Subagent orchestration — launch detached background tasks, track status

### Subagents
- [x] `subagent_launch` — spawn fully autonomous agent via `worker.py` (own session, own tools, survives parent death)
- [x] `subagent_status` / `subagent_list` / `subagent_output` / `subagent_kill`
- [x] Live agent dashboard in HUD — name + pulsing green dot + last output snippet, polls every 2s
- [x] Agent session history persisted per-worker in `~/.voice-spotlight/agents/<id>.session.json`
- [x] Dashboard auto-hides 5s after all agents finish

## Planned / Ideas
- [ ] Scroll support in HUD for long responses
- [ ] Voice activity detection (auto start/stop without holding F9)
- [ ] Pinned notes / reminders accessible via voice
- [ ] Screenshot → vision query (already has screenshot tool, needs vision model)
