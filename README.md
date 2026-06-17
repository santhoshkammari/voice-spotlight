# voice-spotlight

Press **F9**. Speak. Release. Answer streams into a transparent overlay.

No windows. No clicks. No mouse. Voice in, text out, zero friction.

## why

Every AI chat UI makes you tab out, type, wait, read. That's garbage for
anything faster than a full-screen session. I wanted something that lives
on top of everything — code editor, terminal, browser, whatever — and
gets out of the way the second you don't need it.

Voice is faster than typing. A HUD is faster than a window. This is the
intersection.

## how it works

```
F9 hold  →  mic capture @ native sample rate
release  →  Parakeet TDT ONNX (local, ~500ms)
         →  QA agent on Qwen3-27B (streamed)
         →  PyQt5 overlay, top-center, 860×220
         →  auto-collapse after 12s idle
```

## components

| file      | role |
|-----------|------|
| `run.py`  | daemon — PID file, signal handling, voice loop |
| `stt.py`  | Parakeet TDT ONNX — local speech-to-text |
| `agent.py`| MAF agent wired to Qwen3-27B, streaming |
| `ui.py`   | PyQt5 transparent HUD, cyberpunk paint |
| `tools.py`| bash/read/write/edit/glob/grep/web/screenshot tools for agent |
| `opencode.py` | alternative agent backend (opencode subprocess) |
| `slash.py` | slash-command model switcher (/model, /help, /<alias>) |

## run

```sh
python run.py              # daemonizes
kill $(cat ~/.voice-spotlight/daemon.pid)   # stop
```

No tray icon. No menu bar. No setup wizard. Just a cyan glow at the
top of your screen when it's ready.

## stack

- PyQt5 (HUD)
- ONNX Runtime + Parakeet TDT (STT)
- OpenAI Agents SDK (agent framework)
- Qwen3-27B (inference)

## philosophy

> "A tool should be invisible when you don't need it and immediate when
>  you do."
