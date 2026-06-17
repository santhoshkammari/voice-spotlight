"""
STT module — Parakeet TDT ONNX transcription.

Usage:
    python stt.py                        # record with F9 hold/release, then transcribe
    python stt.py --wav path/to/file.wav # transcribe a wav file
"""

import argparse
import numpy as np

MODEL_DIR   = "/home/ntlpt24/.local/share/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8"
SAMPLE_RATE = 16000
BLANK_ID    = 8192
EOT_ID      = 3
SOS_ID      = 4

_nemo = _encoder = _decoder = _vocab = None

def _load():
    global _nemo, _encoder, _decoder, _vocab
    if _nemo is not None:
        return
    import onnxruntime as ort
    _nemo    = ort.InferenceSession(f"{MODEL_DIR}/nemo128.onnx")
    _encoder = ort.InferenceSession(f"{MODEL_DIR}/encoder-model.int8.onnx")
    _decoder = ort.InferenceSession(f"{MODEL_DIR}/decoder_joint-model.int8.onnx")
    _vocab = {}
    with open(f"{MODEL_DIR}/vocab.txt") as f:
        for line in f:
            parts = line.rstrip("\n").split(" ")
            _vocab[int(parts[1])] = parts[0]

def transcribe(audio: np.ndarray) -> str:
    """Transcribe float32 mono 16kHz audio array → text."""
    _load()
    waveform = audio.astype(np.float32)[np.newaxis, :]
    features, feat_lens = _nemo.run(None, {
        "waveforms": waveform,
        "waveforms_lens": np.array([waveform.shape[1]], dtype=np.int64),
    })
    enc_out, _ = _encoder.run(None, {"audio_signal": features, "length": feat_lens})
    tokens = []
    s1 = np.zeros((2, 1, 640), np.float32)
    s2 = np.zeros((2, 1, 640), np.float32)
    last = np.array([[SOS_ID]], dtype=np.int32)
    for t in range(enc_out.shape[2]):
        out, _, s1, s2 = _decoder.run(None, {
            "encoder_outputs": enc_out[:, :, t:t+1],
            "targets": last,
            "target_length": np.array([1], dtype=np.int32),
            "input_states_1": s1,
            "input_states_2": s2,
        })
        tid = int(np.argmax(out[0, 0, 0][:8193]))
        if tid not in (BLANK_ID, EOT_ID):
            tokens.append(tid)
            last = np.array([[tid]], dtype=np.int32)
    return "".join(_vocab.get(t, "") for t in tokens).replace("▁", " ").strip()

def _resample(audio: np.ndarray, from_hz: int, to_hz: int = SAMPLE_RATE) -> np.ndarray:
    if from_hz == to_hz:
        return audio
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(from_hz, to_hz)
    return resample_poly(audio, to_hz // g, from_hz // g).astype(np.float32)

def transcribe_wav(path: str) -> str:
    import wave
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if rate != SAMPLE_RATE:
        audio = _resample(audio, rate)
    return transcribe(audio)


def record_and_transcribe(hotkey: str = "F9") -> str:
    """Hold hotkey to record, release to stop, returns transcribed text."""
    import sys
    import termios
    import threading
    import sounddevice as sd
    from pynput import keyboard

    key_map = {f"F{i}": getattr(keyboard.Key, f"f{i}") for i in range(1, 13)}
    target_key = key_map.get(hotkey.upper(), keyboard.Key.f9)

    # Record at device native rate, resample after (same approach as Handy)
    device_info = sd.query_devices(kind="input")
    native_rate = int(device_info["default_samplerate"])

    frames = []
    stop_event = threading.Event()
    stream_ref = [None]

    def _cb(indata, *_):
        # mix to mono if multichannel
        if indata.shape[1] > 1:
            frames.append(indata.mean(axis=1).copy())
        else:
            frames.append(indata[:, 0].copy())

    def on_press(key):
        if key == target_key and stream_ref[0] is None:
            print("[STT] Recording...", flush=True)
            stream_ref[0] = sd.InputStream(
                samplerate=native_rate, channels=1, dtype="float32", callback=_cb
            )
            stream_ref[0].start()

    def on_release(key):
        if key == target_key and stream_ref[0] is not None:
            stream_ref[0].stop()
            stream_ref[0].close()
            stream_ref[0] = None
            stop_event.set()

    print(f"[STT] Hold {hotkey} to record, release to stop.", flush=True)

    # suppress terminal echo so F-key escape codes don't print
    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    new_attrs = termios.tcgetattr(fd)
    new_attrs[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, new_attrs)

    try:
        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        stop_event.wait()
        listener.stop()
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old_attrs)

    if not frames:
        return ""
    audio = np.concatenate(frames)
    duration = len(audio) / native_rate
    print(f"[STT] Captured {duration:.1f}s @ {native_rate}Hz, resampling to {SAMPLE_RATE}Hz...", flush=True)
    audio16 = _resample(audio, native_rate)
    return transcribe(audio16)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--wav", default=None, help="Transcribe a wav file")
    p.add_argument("--hotkey", default="F9")
    args = p.parse_args()

    if args.wav:
        print(transcribe_wav(args.wav))
    else:
        text = record_and_transcribe(args.hotkey)
        print(f"[STT] Result: {text}")
