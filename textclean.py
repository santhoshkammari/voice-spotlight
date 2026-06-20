"""
Two-layer input filter:
  1. audio_ok(audio, rate)  — RMS energy gate, reject silence before STT
  2. clean(text)            — fix/reject STT hallucination artifacts

Returns None to signal "discard this input".
"""

from __future__ import annotations
import re
import numpy as np


# ── 1. Audio gate ─────────────────────────────────────────────────────────────

MIN_DURATION_S  = 0.4   # ignore presses shorter than 400ms
RMS_THRESHOLD   = 0.003  # below this = silence / background noise


def audio_ok(audio: np.ndarray, sample_rate: int) -> bool:
    """Return False if audio is too short or too quiet to bother transcribing."""
    duration = len(audio) / sample_rate
    if duration < MIN_DURATION_S:
        return False
    rms = float(np.sqrt(np.mean(audio ** 2)))
    return rms >= RMS_THRESHOLD


# ── 2. Text cleanup / rejection ───────────────────────────────────────────────

# Characters that are almost certainly hallucination noise
_NOISE_CHARS = re.compile(r'[\$\#\@\^\*\~\|\{\}\[\]<>\\=_]{2,}')

# CJK unicode blocks (Chinese, Japanese, Korean)
_CJK = re.compile(r'[一-鿿぀-ヿ가-힯㐀-䶿]')

# Repeated punctuation: ....... or . . . . or , , , ,
_REPEAT_PUNCT = re.compile(r'([.!?,;])\s*(\1\s*){2,}')

# Repeated words: "thank you thank you thank you"
_REPEAT_WORD  = re.compile(r'\b(\w+)(\s+\1){3,}\b', re.IGNORECASE)

# Filler-only lines that Parakeet emits on silence
_FILLER = re.compile(
    r'^[\s.!?,;:…\-–—]+$'
    r'|^(uh+|um+|mm+|hmm+|huh+|ah+)[.!?\s]*$',
    re.IGNORECASE,
)

MIN_REAL_WORDS = 1        # must have at least this many real words
MAX_CJK_RATIO  = 0.25     # reject if >25% chars are CJK


def clean(text: str) -> str | None:
    """
    Clean STT output. Returns cleaned string, or None if it should be discarded.
    """
    if not text:
        return None

    t = text.strip()

    # reject pure noise/filler
    if _FILLER.match(t):
        return None

    # reject if too many CJK characters (hallucinated foreign language)
    if len(t) > 0:
        cjk_count = len(_CJK.findall(t))
        if cjk_count / len(t) > MAX_CJK_RATIO:
            return None

    # reject if too many noise symbols
    if _NOISE_CHARS.search(t):
        return None

    # collapse repeated punctuation: "....." → "."  ", , , ," → ","
    t = _REPEAT_PUNCT.sub(r'\1', t)

    # collapse repeated words: "you you you you" → "you"
    t = _REPEAT_WORD.sub(r'\1', t)

    # collapse repeated phrases (2-4 word chunks repeated 3+ times)
    for n in (4, 3, 2):
        pat = re.compile(
            r'\b((?:\w+\s+){' + str(n-1) + r'}\w+)(\s+\1){2,}\b',
            re.IGNORECASE
        )
        t = pat.sub(r'\1', t)

    # collapse runs of whitespace
    t = re.sub(r'  +', ' ', t).strip()

    # must have at least one real alphabetic word after cleanup
    words = [w for w in t.split() if re.search(r'[a-zA-Z]', w)]
    if len(words) < MIN_REAL_WORDS:
        return None

    return t
