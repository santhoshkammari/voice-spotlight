# AGENTS.md — for AI agents operating on this repo

## project

voice-spotlight — voice-activated HUD for streaming AI answers.

## files

- **run.py** — entry point. `python run.py` to start daemon.
  - Writes PID to `~/.voice-spotlight/daemon.pid`
  - Spawns voice listener thread
  - Uses PyQt5 QApplication (translucent, always-on-top, click-through)

- **stt.py** — speech-to-text via Parakeet TDT ONNX.
  - Models at `~/.local/share/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8/`
  - 4 ONNX sessions: nemo, encoder, decoder, vocab.txt
  - Resamples to 16kHz via scipy.signal.resample_poly

- **agent.py** — MAF agent (OpenAI Agents SDK) → Qwen3-27B.
  - `stream_answer(question, on_token, on_done)` — blocking, call in thread
  - Maintains rolling 20-turn history in-memory
  - Uses tool set from `tools.py`

- **ui.py** — HUD overlay widget. No input box, voice only.
  - Collapsed = 4px cyan glow line
  - Expanded = 860×220, dark space theme, scan-line animation
  - Auto-collapses 12s after answer done
  - Signals: token, done, show_recording, hide_recording, clear

- **tools.py** — agent toolbelt (bash, read_file, write_file, edit_file,
  glob_files, grep_files, web_fetch, web_search, screenshot).
  All decorated with `@agents.function_tool`.

- **opencode.py** — alternative backend. Spawns opencode CLI as subprocess,
  parses JSON-streamed events, word-tokenizes for pseudo-streaming UX.

- **slash.py** — inline command parser for model switching.
  - `/model`, `/models`, `/help`, `/<alias>`, `/<alias> <prompt>`
  - Persists chosen model to `~/.spotlight/config.json`
  - Queries `opencode models` for available models

## architecture

```
mic → stt.transcribe() → agent_stream(question, ...) → ui.HUD
     ↑                    ↑                              ↑
  local ONNX          Qwen3-27B                      PyQt5 overlay
```

## config

No config file. Hotkey is hardcoded to F9 in `run.py:VOICE_HOTKEY`.
Model persistence in `~/.spotlight/config.json`.

## dependencies

```
PyQt5, numpy, sounddevice, pynput, onnxruntime,
scipy, openai, agents (openai-agents-python), httpx,
ddgs (duckduckgo-search), scrot
```

## gotchas

- Agent endpoint is set via `LLM_BASE_URL` env var
- STT models are machine-local, not in repo
- HUD uses X11BypassWindowManagerHint — Wayland untested
- Parallel recordings not supported (single-threaded voice loop)
