"""
voice-hud — background daemon
  • python run.py        start (stays running, no window in taskbar)
  • hold F9             record speech → STT → streams answer into HUD
  • Esc (hold F9 then)  not needed — HUD auto-collapses after 12s
  • kill the process    to stop
"""

import os
import sys
import signal
import threading

PID_FILE     = os.path.expanduser("~/.voice-spotlight/daemon.pid")
VOICE_HOTKEY = "F9"


# ── pid helpers ──────────────────────────────────────────────────────────────

def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def _read_pid():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        return pid if _pid_alive(pid) else None
    except Exception:
        return None

def _write_pid():
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def _clear_pid():
    try:
        if _read_pid() == os.getpid():
            os.remove(PID_FILE)
    except Exception:
        pass


# ── voice thread ─────────────────────────────────────────────────────────────

def _voice_loop(hud):
    import numpy as np
    import sounddevice as sd
    from pynput import keyboard
    import stt

    key_map = {f"F{i}": getattr(keyboard.Key, f"f{i}") for i in range(1, 13)}
    target_key = key_map.get(VOICE_HOTKEY.upper(), keyboard.Key.f9)

    device_info = sd.query_devices(kind="input")
    native_rate = int(device_info["default_samplerate"])

    while True:
        frames      = []
        stop_event  = threading.Event()
        stream_ref  = [None]
        recording   = [False]

        def _cb(indata, *_):
            if indata.shape[1] > 1:
                frames.append(indata.mean(axis=1).copy())
            else:
                frames.append(indata[:, 0].copy())

        def on_press(key):
            if key == target_key and not recording[0]:
                recording[0] = True
                hud.emitter.show_recording.emit()
                stream_ref[0] = sd.InputStream(
                    samplerate=native_rate, channels=1, dtype="float32", callback=_cb
                )
                stream_ref[0].start()

        def on_release(key):
            if key == target_key and recording[0]:
                recording[0] = False
                if stream_ref[0]:
                    stream_ref[0].stop()
                    stream_ref[0].close()
                    stream_ref[0] = None
                stop_event.set()

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        stop_event.wait()
        listener.stop()

        hud.emitter.hide_recording.emit()

        if not frames:
            continue

        audio   = np.concatenate(frames)
        audio16 = stt._resample(audio, native_rate)
        text    = stt.transcribe(audio16).strip()

        if not text:
            continue

        # stream answer into HUD
        _stream_answer(hud, text)


def _stream_answer(hud, question):
    # ── opencode backend (commented out) ──────────────────────────────
    # from opencode import opencode_stream
    # cancel = threading.Event()
    # try:
    #     for chunk in opencode_stream(question, cancel_event=cancel):
    #         hud.emitter.token.emit(chunk)
    # except Exception as e:
    #     hud.emitter.token.emit(f"[error: {e}]")
    # finally:
    #     hud.emitter.done.emit()

    # ── MAF agent backend (GPU4 Qwen3-27B, real tool use, streaming) ──
    from agent import stream_answer as agent_stream
    agent_stream(
        question,
        on_token=lambda text: hud.emitter.token.emit(text),
        on_done=lambda: hud.emitter.done.emit(),
    )


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    existing = _read_pid()
    if existing:
        print(f"[voice-hud] already running (pid {existing}). Kill it first.")
        return

    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    from ui import HUD

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    hud = HUD()

    signal.signal(signal.SIGINT,  lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    # heartbeat so Python delivers signals while Qt runs
    hb = QTimer()
    hb.start(200)
    hb.timeout.connect(lambda: None)

    vt = threading.Thread(target=_voice_loop, args=(hud,), daemon=True)
    vt.start()

    _write_pid()
    print(f"[voice-hud] running (pid {os.getpid()}) — hold {VOICE_HOTKEY} to speak")
    try:
        sys.exit(app.exec_())
    finally:
        _clear_pid()


if __name__ == "__main__":
    main()
