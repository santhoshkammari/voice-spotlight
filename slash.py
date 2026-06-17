import subprocess
import json
import os
import shutil
from dataclasses import dataclass
from typing import Optional


def _opencode_bin() -> str:
    found = shutil.which("opencode")
    if found:
        return found
    return "/home/ntlpt24/.opencode/bin/opencode"
CONFIG_DIR = os.path.expanduser("~/.spotlight")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_MODEL = "opencode/deepseek-v4-flash-free"


# ── persistence ──────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_config(data: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_current_model() -> str:
    return _load_config().get("model", DEFAULT_MODEL)

def set_current_model(model: str):
    cfg = _load_config()
    cfg["model"] = model
    _save_config(cfg)


# ── dynamic model list ────────────────────────────────────────────────────────

def fetch_models() -> list[str]:
    """Run `opencode models` and return list of model IDs."""
    try:
        result = subprocess.run(
            [_opencode_bin(), "models"],
            capture_output=True, text=True, timeout=10
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return [DEFAULT_MODEL]

def _make_alias(model_id: str) -> str:
    """Derive a short slash-command alias from a model ID."""
    # e.g. "opencode/deepseek-v4-flash-free" → "deepseek-v4-flash-free"
    #      "huggingface/Qwen/Qwen3-235B-A22B" → "qwen3-235b-a22b"
    parts = model_id.split("/")
    name = parts[-1].lower()
    return name

def get_model_map() -> dict[str, str]:
    """Return {alias: model_id} for all available models."""
    models = fetch_models()
    mapping = {}
    for m in models:
        alias = _make_alias(m)
        # avoid duplicate aliases by keeping first occurrence
        if alias not in mapping:
            mapping[alias] = m
    return mapping


# ── slash result ──────────────────────────────────────────────────────────────

@dataclass
class SlashResult:
    handled: bool
    show_text: Optional[str] = None
    new_model: Optional[str] = None
    prompt: Optional[str] = None      # run this as a prompt after model switch


# ── handler ───────────────────────────────────────────────────────────────────

def handle(text: str) -> SlashResult:
    text = text.strip()
    current = get_current_model()

    if text == "/help":
        return SlashResult(handled=True, show_text=_help_text(current))

    if text == "/model":
        return SlashResult(handled=True, show_text=f"current model:\n{current}")

    if text == "/models":
        models = fetch_models()
        lines = ["available models (use alias to switch):\n"]
        for m in models:
            alias = _make_alias(m)
            lines.append(f"  /{alias}")
        return SlashResult(handled=True, show_text="\n".join(lines))

    if not text.startswith("/"):
        return SlashResult(handled=False)

    # /alias  or  /alias rest of prompt
    parts = text[1:].split(None, 1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    model_map = get_model_map()

    if cmd in model_map:
        new_model = model_map[cmd]
        set_current_model(new_model)
        if rest:
            return SlashResult(handled=True, new_model=new_model, prompt=rest)
        else:
            return SlashResult(
                handled=True,
                new_model=new_model,
                show_text=f"switched to:\n{new_model}\n\ntype your prompt and press enter",
            )

    return SlashResult(
        handled=True,
        show_text=f"unknown: {text}\ntype /help or /models",
    )


def _help_text(current: str) -> str:
    return f"""commands
────────────────────────────
/help       this menu
/model      show current model
/models     list all models

switch model:
  /<alias>           switch model
  /<alias> <prompt>  switch + ask

current:
  {current}

aliases are the last segment of the
model id in lowercase, e.g.:
  /deepseek-v4-flash-free
  /kimi-k2-instruct
  /gemini-2.5-flash

esc to close"""
