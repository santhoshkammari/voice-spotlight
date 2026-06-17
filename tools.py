"""Built-in tools as agent_framework @tool decorated functions."""
from __future__ import annotations

import difflib
import glob as _glob
import os
import re
import subprocess
from pathlib import Path
from typing import Annotated

from agents import function_tool as tool

_OUTPUT_LIMIT = 8000
_READ_LIMIT = 10000


def _truncate(text: str, limit: int = _OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated {len(text) - limit} chars]"


def _resolve(p: str) -> Path:
    p = os.path.expanduser(p)
    path = Path(p)
    return (path if path.is_absolute() else Path.cwd() / path).resolve()


def _tool_env() -> dict[str, str]:
    env = os.environ.copy()
    env["TERM"] = "dumb"
    kivi_env = env.get("KIVI_ENV_PATH", "")
    if kivi_env:
        p = Path(kivi_env)
        bin_dir = str(p.parent if p.suffix else p / "bin")
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


# ── Core tools ────────────────────────────────────────────────────────


@tool
def bash(
    command: Annotated[str, "Shell command to run"],
    timeout: Annotated[int, "Timeout in seconds (max 300)"] = 120,
) -> str:
    """Execute a shell command and return stdout+stderr."""
    timeout = min(int(timeout), 300)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, env=_tool_env(),
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if not output:
            output = f"[exit code {result.returncode}]" if result.returncode != 0 else "[no output]"
        elif result.returncode != 0:
            output = f"{output}\n[exit code {result.returncode}]"
        return _truncate(output)
    except subprocess.TimeoutExpired as exc:
        partial = ""
        if exc.stdout or exc.stderr:
            partial = ((exc.stdout or "") + (exc.stderr or "")).strip()
        msg = f"[bash error] timed out ({timeout}s)"
        return _truncate(f"{msg}\n{partial}".strip())


@tool
def read_file(
    path: Annotated[str, "Path to the file to read"],
) -> str:
    """Read a file and return its contents with line numbers (cat -n style)."""
    try:
        p = _resolve(path)
        content = p.read_text()
        lines = content.splitlines(keepends=True)
        numbered = "".join(f"{i + 1}\t{line}" for i, line in enumerate(lines))
        return _truncate(numbered, _READ_LIMIT)
    except Exception as e:
        return f"[read_file error] {e}"


@tool
def write_file(
    path: Annotated[str, "Path to the file to write"],
    content: Annotated[str, "Content to write to the file"],
) -> str:
    """Write content to a file, creating parent directories if needed."""
    try:
        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"[wrote {len(content.encode())} bytes to {p}]"
    except Exception as e:
        return f"[write_file error] {e}"


@tool
def edit_file(
    path: Annotated[str, "Path to the file to edit"],
    old_string: Annotated[str, "Exact string to find (must be unique in file)"],
    new_string: Annotated[str, "Replacement string"],
) -> str:
    """Replace first occurrence of old_string with new_string in a file. Errors if not found or not unique."""
    try:
        p = _resolve(path)
        original = p.read_text()
        count = original.count(old_string)
        if count == 0:
            return f"[edit_file error] old_string not found in {p}"
        if count > 1:
            return f"[edit_file error] old_string found {count} times in {p} — must be unique"
        updated = original.replace(old_string, new_string, 1)
        p.write_text(updated)
        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            f"a/{p.name}", f"b/{p.name}",
        ))
        return diff or "[no changes]"
    except Exception as e:
        return f"[edit_file error] {e}"


@tool
def glob_files(
    pattern: Annotated[str, "Glob pattern to match files (e.g. '**/*.py')"],
    directory: Annotated[str, "Root directory to search in"] = ".",
) -> str:
    """Find files matching a glob pattern under a directory."""
    try:
        root = _resolve(directory)
        # strip leading slash — absolute patterns not supported by pathlib glob
        pat = pattern.lstrip("/") if pattern.startswith("/") else pattern
        matches = sorted(
            str(p.relative_to(root))
            for p in root.glob(pat)
            if p.is_file()
        )
        return "\n".join(matches[:200]) if matches else "[glob_files] no matches"
    except Exception as e:
        return f"[glob_files error] {e}"


@tool
def grep_files(
    pattern: Annotated[str, "Regex pattern to search for"],
    path: Annotated[str, "File or directory to search in"] = ".",
    recursive: Annotated[bool, "Search recursively"] = True,
) -> str:
    """Search file contents for a regex pattern, return matching lines with line numbers."""
    try:
        search_path = str(_resolve(path))
        flags = ["-rn"] if recursive else ["-n"]
        result = subprocess.run(
            ["grep"] + flags + ["--", pattern, search_path],
            capture_output=True, text=True, timeout=30, env=_tool_env(),
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode == 1 and not output:
            return "[grep_files] no matches"
        return _truncate(output) if output else "[no output]"
    except subprocess.TimeoutExpired:
        return "[grep_files error] timed out"
    except FileNotFoundError:
        return "[grep_files error] grep not found"
    except Exception as e:
        return f"[grep_files error] {e}"


# ── Web tools ─────────────────────────────────────────────────────────


@tool
def web_fetch(
    url: Annotated[str, "URL to fetch"],
) -> str:
    """Fetch a URL and return its text content (HTML stripped to plain text)."""
    try:
        try:
            import httpx
            resp = httpx.get(url, timeout=30, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 (kivi-agent)"})
            resp.raise_for_status()
            raw = resp.text
        except ImportError:
            import urllib.request
            with urllib.request.urlopen(url, timeout=30) as r:
                raw = r.read().decode("utf-8", errors="replace")

        # strip HTML tags to plain text
        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&quot;", '"', text)
        text = re.sub(r"&#39;", "'", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()
        return _truncate(text)
    except Exception as e:
        return f"[web_fetch error] {e}"


@tool
def web_search(
    query: Annotated[str, "Search query"],
    max_results: Annotated[int, "Maximum number of results to return"] = 5,
) -> str:
    """Search the web using DuckDuckGo and return results with titles, URLs, and descriptions."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return f"No results found for: {query}"
        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')}")
            lines.append(f"   URL: {r.get('href', '')}")
            lines.append(f"   {r.get('body', '')[:200]}")
            lines.append("")
        return "\n".join(lines)
    except ImportError:
        return "[web_search error] ddgs package not installed. Run: uv pip install ddgs"
    except Exception as e:
        return f"[web_search error] {e}"


# ── Screenshot tool ───────────────────────────────────────────────────


@tool
def screenshot(
    region: Annotated[str, "Optional region as 'x,y,w,h' — omit for full screen"] = "",
) -> str:
    """Take a screenshot of the screen (or a region) and return the path to the saved PNG."""
    import tempfile
    path = tempfile.mktemp(prefix="vhud_shot_", suffix=".png")
    if region:
        try:
            x, y, w, h = [int(v.strip()) for v in region.split(",")]
            cmd = ["scrot", "-a", f"{x},{y},{w},{h}", path]
        except Exception:
            return f"[screenshot error] region must be 'x,y,w,h', got: {region}"
    else:
        cmd = ["scrot", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=_tool_env())
        if result.returncode != 0:
            return f"[screenshot error] {(result.stderr or '').strip() or 'scrot failed'}"
        return f"[screenshot saved] {path}"
    except FileNotFoundError:
        return "[screenshot error] scrot not found — install with: sudo apt install scrot"
    except Exception as e:
        return f"[screenshot error] {e}"


# ── Exports ───────────────────────────────────────────────────────────


def all_tools() -> list:
    """Return all built-in tool functions."""
    return [bash, read_file, write_file, edit_file, glob_files, grep_files,
            web_fetch, web_search, screenshot]


def web_tools() -> list:
    """Return web-related tool functions."""
    return [web_fetch, web_search]

