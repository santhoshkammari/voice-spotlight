import subprocess
import json
import shutil
import threading
from typing import Iterator
from slash import get_current_model


def _opencode_bin() -> str:
    found = shutil.which("opencode")
    if found:
        return found
    return "/home/ntlpt24/.opencode/bin/opencode"


def opencode_stream(prompt: str, model: str = None, cancel_event: threading.Event = None) -> Iterator[str]:
    if model is None:
        model = get_current_model()
    cmd = [_opencode_bin(), "run", "--format", "json", "-m", model, prompt]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    full_text = ""
    try:
        for line in proc.stdout:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                return
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "text":
                full_text = event.get("part", {}).get("text", "")
            elif etype == "error":
                msg = event.get("message") or event.get("error") or str(event)
                yield f"\n[error: {msg}]"
                return
    except Exception as e:
        yield f"\n[stream error: {e}]"
        return
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()

    if not full_text:
        return

    # Simulate word-by-word streaming since opencode delivers full text at once
    words = full_text.split(" ")
    accumulated = ""
    for i, word in enumerate(words):
        if cancel_event and cancel_event.is_set():
            return
        accumulated += ("" if i == 0 else " ") + word
        yield accumulated
