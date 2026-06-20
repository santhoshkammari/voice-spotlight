"""
Lightweight markdown → QPainter renderer.
Handles: # headers, **bold**, *italic*, `code`, - bullets, blank lines.
No deps beyond PyQt5.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List

from PyQt5.QtGui import QPainter, QFont, QFontMetrics, QColor
from PyQt5.QtCore import Qt


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False


@dataclass
class Block:
    kind: str          # "para" | "h1" | "h2" | "h3" | "bullet" | "code_line"
    spans: List[Span] = field(default_factory=list)
    indent: int = 0


def _parse_spans(line: str) -> List[Span]:
    spans = []
    # tokenise **bold**, *italic*, `code`
    pattern = re.compile(r'(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`)')
    pos = 0
    for m in pattern.finditer(line):
        if m.start() > pos:
            spans.append(Span(line[pos:m.start()]))
        raw = m.group(0)
        if raw.startswith("**"):
            spans.append(Span(m.group(2), bold=True))
        elif raw.startswith("*"):
            spans.append(Span(m.group(3), italic=True))
        else:
            spans.append(Span(m.group(4), code=True))
        pos = m.end()
    if pos < len(line):
        spans.append(Span(line[pos:]))
    return spans or [Span(line)]


def parse(text: str) -> List[Block]:
    blocks = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        if line.startswith("### "):
            blocks.append(Block("h3", _parse_spans(line[4:])))
        elif line.startswith("## "):
            blocks.append(Block("h2", _parse_spans(line[3:])))
        elif line.startswith("# "):
            blocks.append(Block("h1", _parse_spans(line[2:])))
        elif re.match(r'^(\s*)[-*]\s', line):
            m = re.match(r'^(\s*)[-*]\s(.*)', line)
            indent = len(m.group(1)) // 2
            blocks.append(Block("bullet", _parse_spans(m.group(2)), indent=indent))
        elif line.startswith("    ") or line.startswith("\t"):
            blocks.append(Block("code_line", [Span(line.lstrip())]))
        else:
            blocks.append(Block("para", _parse_spans(line)))
    return blocks


def draw(
    p: QPainter,
    text: str,
    x: int, y: int, w: int, h: int,
    base_font: QFont,
    c_text: QColor,
    c_dim: QColor,
    c_code_bg: QColor,
    c_accent: QColor,
) -> int:
    """Draw markdown text, return final y position."""
    blocks = parse(text)

    mono_name   = "JetBrains Mono"
    ui_name     = base_font.family()
    base_size   = base_font.pointSize()

    SIZES = {"h1": base_size + 4, "h2": base_size + 2, "h3": base_size + 1,
              "para": base_size, "bullet": base_size, "code_line": base_size - 1}

    cy = y
    line_gap = 4

    for block in blocks:
        if cy > y + h:
            break

        size  = SIZES.get(block.kind, base_size)
        is_code = block.kind == "code_line"

        # code block background
        if is_code:
            fm = QFontMetrics(QFont(mono_name, size))
            line_h = fm.height()
            p.setPen(Qt.NoPen)
            p.setBrush(c_code_bg)
            p.drawRoundedRect(x, cy, w, line_h + 4, 3, 3)

        # header accent bar
        if block.kind in ("h1", "h2"):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c_accent.red(), c_accent.green(), c_accent.blue(), 60))
            bar_h = 2 if block.kind == "h2" else 3
            p.drawRect(x, cy, w, bar_h)
            cy += bar_h + 2

        # bullet dot
        if block.kind == "bullet":
            dot_x = x + block.indent * 14
            dot_font = QFont(ui_name, size)
            fm = QFontMetrics(dot_font)
            p.setPen(c_dim)
            p.setFont(dot_font)
            p.drawText(dot_x, cy + fm.ascent(), "•")
            text_x = dot_x + fm.horizontalAdvance("• ")
        else:
            text_x = x

        # render spans
        cx2 = text_x
        row_h = 0
        for span in block.spans:
            fname = mono_name if (span.code or is_code) else ui_name
            f = QFont(fname, size)
            f.setBold(span.bold or block.kind in ("h1", "h2", "h3"))
            f.setItalic(span.italic)
            p.setFont(f)
            fm = QFontMetrics(f)
            row_h = max(row_h, fm.height())

            color = c_dim if span.code else c_text
            if block.kind in ("h1", "h2", "h3"):
                color = c_text
            p.setPen(color)

            # word-wrap within available width
            words = span.text.split(" ")
            buf = ""
            for word in words:
                test = (buf + " " + word).lstrip()
                if fm.horizontalAdvance(test) > (w - (cx2 - x)):
                    if buf:
                        p.drawText(cx2, cy + fm.ascent(), buf)
                    cy += fm.height()
                    cx2 = text_x
                    buf = word
                else:
                    buf = test
            if buf:
                p.drawText(cx2, cy + fm.ascent(), buf)
                cx2 += fm.horizontalAdvance(buf)

        cy += row_h + line_gap

    return cy
