"""
HUD overlay — macOS Spotlight style.
Frosted glass, black/white, auto-expands as tokens stream.
"""

import threading
from PyQt5.QtWidgets import QWidget, QApplication, QDesktopWidget
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal, QObject, QTimer, QRect
from PyQt5.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QLinearGradient, QBrush
import mdrender


WIDTH       = 720
PAD_H       = 22    # horizontal text padding
PAD_TOP     = 52    # space above text (for search bar row)
PAD_BOT     = 20
LINE_H      = 22    # px per text line
MIN_H       = 56    # height when just showing the bar (recording / idle)
MAX_H       = 520

RADIUS      = 14

# Palette — monochrome glass
C_BG        = QColor(18, 18, 18, 210)   # near-black, semi-transparent
C_BORDER    = QColor(255, 255, 255, 28) # very subtle white border
C_TEXT      = QColor(240, 240, 240, 255)
C_DIM       = QColor(160, 160, 160, 200)
C_REC       = QColor(255, 80,  80,  255)
C_CURSOR    = QColor(255, 255, 255, 200)
C_DIVIDER   = QColor(255, 255, 255, 18)
C_HINT      = QColor(120, 120, 120, 140)
C_CODE_BG   = QColor(40,  40,  40,  180)
C_ACCENT    = QColor(100, 180, 255, 200)

FONT_UI     = "SF Pro Display"
FONT_MONO   = "JetBrains Mono"


class Emitter(QObject):
    token          = pyqtSignal(str)
    done           = pyqtSignal()
    show_recording = pyqtSignal()
    hide_recording = pyqtSignal()
    clear          = pyqtSignal()
    agents_update  = pyqtSignal(list)   # list of agent dicts


class HUD(QWidget):
    def __init__(self):
        super().__init__()
        self._text       = ""
        self._recording  = False
        self._expanded   = False
        self._dot_phase  = 0
        self._agents     = []
        self._cur_h      = MIN_H
        self._scroll_y      = 0    # pixels scrolled from top of content
        self._user_scrolled = False  # True once user manually scrolls up
        self.emitter     = Emitter()

        self._build()
        self._connect()
        self._start_timers()

    def _build(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Voice Spotlight")
        self.setMinimumSize(400, MIN_H)

        screen = QDesktopWidget().screenGeometry(0)
        x = screen.x() + (screen.width() - WIDTH) // 2
        y = screen.y() + int(screen.height() * 0.22)
        self._x = x
        self._y = y
        self.setGeometry(x, y, WIDTH, MIN_H)
        self._drag_pos = None
        self._resize_edge = None
        self.hide()

    def _connect(self):
        self.emitter.token.connect(self._on_token, Qt.QueuedConnection)
        self.emitter.done.connect(self._on_done, Qt.QueuedConnection)
        self.emitter.show_recording.connect(self._on_recording_start, Qt.QueuedConnection)
        self.emitter.hide_recording.connect(self._on_recording_stop, Qt.QueuedConnection)
        self.emitter.clear.connect(self._on_clear, Qt.QueuedConnection)
        self.emitter.agents_update.connect(self._on_agents_update, Qt.QueuedConnection)

    def _start_timers(self):
        self._anim_timer = QTimer()
        self._anim_timer.setInterval(40)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start()

        self._collapse_timer = QTimer()
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse)

    def _tick(self):
        if self._recording or self._expanded:
            self._dot_phase = (self._dot_phase + 1) % 60
            self.update()

    # ── signals ───────────────────────────────────────────────────────────────

    def _on_recording_start(self):
        self._recording = True
        self._text = ""
        self._scroll_y = 0
        self._user_scrolled = False
        self._collapse_timer.stop()
        self._set_height(MIN_H, animate=False)
        self.show()
        self.update()

    def _on_recording_stop(self):
        self._recording = False
        self.update()

    def _on_token(self, text):
        self._text = text
        self._collapse_timer.stop()
        self._expanded = True
        self._reflow_height()
        self._autoscroll_to_bottom()
        self.show()
        self.update()

    def _on_done(self):
        self._collapse_timer.start(10000)

    def _on_clear(self):
        self._text = ""
        if not self._agents:
            self._collapse()
        else:
            self._reflow_height()
            self.update()

    def _on_agents_update(self, agents: list):
        self._agents = agents
        if agents:
            self._collapse_timer.stop()   # keep HUD alive while agents run
            self._expanded = True
            self._reflow_height()
            self.show()
        else:
            # no more running agents — start collapse countdown if no text
            if not self._text and not self._recording:
                self._collapse_timer.start(5000)
        self.update()

    # ── layout ────────────────────────────────────────────────────────────────

    def _content_height(self) -> int:
        """Total rendered height of current text content in pixels."""
        if not self._text:
            return 0
        fm      = QFontMetrics(QFont(FONT_UI, 13))
        line_h  = fm.height() + 4   # +4 matches mdrender line_gap
        avail_w = self.width() - PAD_H * 2
        lines   = 0
        for para in self._text.split("\n"):
            if not para:
                lines += 1
                continue
            adv = fm.horizontalAdvance(para)
            lines += max(1, (adv + avail_w - 1) // avail_w)
        agent_h = (len(self._agents) * 36 + 12) if self._agents else 0
        return PAD_TOP + lines * line_h + PAD_BOT + agent_h

    def _autoscroll_to_bottom(self):
        """Push scroll offset so latest content is visible — unless user scrolled up."""
        if self._user_scrolled:
            return
        viewport_h = self.height() - PAD_TOP - PAD_BOT
        total_h    = self._content_height() - PAD_TOP - PAD_BOT
        self._scroll_y = max(0, total_h - viewport_h)

    def _reflow_height(self):
        if not self._text:
            if self._agents:
                h = PAD_TOP + min(len(self._agents), 10) * 36 + PAD_BOT
                self._set_height(max(MIN_H, h))
            else:
                self._set_height(MIN_H)
            return
        fm      = QFontMetrics(QFont(FONT_UI, 13))
        line_h  = fm.height() + 4
        avail_w = self.width() - PAD_H * 2
        lines   = 0
        for para in self._text.split("\n"):
            if not para:
                lines += 1
                continue
            adv = fm.horizontalAdvance(para)
            lines += max(1, (adv + avail_w - 1) // avail_w)
        agent_h = (len(self._agents) * 36 + 12) if self._agents else 0
        needed  = PAD_TOP + lines * line_h + PAD_BOT + agent_h
        h = max(MIN_H, min(needed, MAX_H))
        self._set_height(int(h))

    def _set_height(self, h, animate=True):
        if abs(self._cur_h - h) < 2:
            return
        self._cur_h = h
        cur = self.geometry()
        if not animate or self._expanded:
            # instant resize while streaming — no animation thrash
            self.setGeometry(cur.x(), cur.y(), cur.width(), h)
            return
        if hasattr(self, "_anim_geo") and self._anim_geo.state() == QPropertyAnimation.Running:
            self._anim_geo.stop()
        self._anim_geo = QPropertyAnimation(self, b"geometry")
        self._anim_geo.setDuration(160)
        self._anim_geo.setStartValue(QRect(cur.x(), cur.y(), cur.width(), cur.height()))
        self._anim_geo.setEndValue(QRect(cur.x(), cur.y(), cur.width(), h))
        self._anim_geo.setEasingCurve(QEasingCurve.OutCubic)
        self._anim_geo.start()

    def _collapse(self):
        self._text = ""
        self._recording = False
        self._scroll_y = 0
        self._user_scrolled = False
        if self._agents:
            # keep showing agent panel
            self._expanded = True
            self._reflow_height()
            self.update()
            return
        self._expanded = False
        self.hide()
        self._cur_h = MIN_H
        cur = self.geometry()
        self.setGeometry(cur.x(), cur.y(), cur.width(), MIN_H)

    # ── paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        w, h = self.width(), self.height()

        # frosted glass background
        p.setPen(Qt.NoPen)
        p.setBrush(C_BG)
        p.drawRoundedRect(0, 0, w, h, RADIUS, RADIUS)

        # subtle border
        p.setPen(QPen(C_BORDER, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(0, 0, w, h, RADIUS, RADIUS)

        # ── top bar row ────────────────────────────────────────────────────
        icon_x = PAD_H
        icon_y = 14

        if self._recording:
            # pulsing red mic dot
            pulse = abs(self._dot_phase - 30) / 30.0
            alpha = int(180 + 75 * pulse)
            p.setBrush(QColor(255, 80, 80, alpha))
            p.setPen(Qt.NoPen)
            p.drawEllipse(icon_x, icon_y + 2, 10, 10)

            p.setPen(QColor(255, 80, 80, alpha))
            f = QFont(FONT_UI, 11)
            p.setFont(f)
            p.drawText(icon_x + 16, icon_y + 11, "Listening…")
        else:
            # mic icon (simple circle + line)
            p.setBrush(C_DIM)
            p.setPen(Qt.NoPen)
            p.drawEllipse(icon_x, icon_y + 2, 10, 10)

            label = "Voice AI" if not self._text else "Response"
            p.setPen(C_DIM)
            f = QFont(FONT_UI, 11)
            p.setFont(f)
            p.drawText(icon_x + 16, icon_y + 11, label)

        # F9 hint right-aligned
        p.setPen(C_HINT)
        f2 = QFont(FONT_UI, 10)
        p.setFont(f2)
        p.drawText(0, icon_y, w - PAD_H, 14, Qt.AlignRight, "F9")

        if h <= MIN_H + 4:
            p.end()
            return

        # divider
        p.setPen(QPen(C_DIVIDER, 1))
        p.drawLine(PAD_H, 40, w - PAD_H, 40)

        # ── text area ──────────────────────────────────────────────────────
        if not self._text and not self._agents:
            p.setPen(C_HINT)
            hf = QFont(FONT_UI, 13)
            p.setFont(hf)
            p.drawText(PAD_H, PAD_TOP, w - PAD_H * 2, h - PAD_TOP - PAD_BOT,
                       Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter,
                       "Hold F9, speak, release…")
            p.end()
            return

        # agents only (no text)
        if not self._text and self._agents:
            self._paint_agents(p, w, PAD_TOP, h)
            p.end()
            return

        # text (+ maybe agents below) — rendered as markdown with scroll
        tx = PAD_H
        tw = w - PAD_H * 2
        agent_panel_h = (len(self._agents) * 36 + 12) if self._agents else 0
        # clip text area — agents strip is pinned at bottom
        text_viewport_h = h - PAD_TOP - PAD_BOT - agent_panel_h

        # clip to text viewport so text doesn't bleed into agent strip or top bar
        p.setClipRect(tx, PAD_TOP, tw, text_viewport_h)

        base_font = QFont(FONT_UI, 13)
        # offset ty by negative scroll so content scrolls up
        ty = PAD_TOP - self._scroll_y
        end_y = mdrender.draw(
            p, self._text,
            tx, ty, tw, text_viewport_h + self._scroll_y,
            base_font, C_TEXT, C_DIM, C_CODE_BG, C_ACCENT,
        )

        # blinking cursor after last rendered line (only if in viewport)
        if self._dot_phase < 30 and end_y > PAD_TOP:
            p.setPen(C_CURSOR)
            p.setFont(QFont(FONT_MONO, 13))
            p.drawText(tx, end_y, "▎")

        p.setClipping(False)

        # agent strip pinned at bottom
        if self._agents:
            strip_y = h - agent_panel_h
            p.setPen(QPen(C_DIVIDER, 1))
            p.drawLine(PAD_H, strip_y, w - PAD_H, strip_y)
            self._paint_agents(p, w, strip_y + 8, h)

        p.end()

    def _paint_agents(self, p, w, y_start, h):
        STATUS_COLOR = {
            "running":   QColor(80,  200, 120, 220),
            "completed": QColor(120, 120, 120, 180),
            "failed":    QColor(255,  80,  80, 220),
            "cancelled": QColor(160, 160, 160, 140),
        }
        row_h = 36
        y = y_start
        name_f  = QFont(FONT_UI,   11); name_f.setBold(True)
        snip_f  = QFont(FONT_MONO, 9)
        name_fm = QFontMetrics(name_f)
        snip_fm = QFontMetrics(snip_f)

        for agent in self._agents[:10]:
            if y + row_h > h:
                break
            st  = agent.get("status", "?")
            col = STATUS_COLOR.get(st, C_DIM)
            if st == "running":
                pulse = abs(self._dot_phase - 30) / 30.0
                col = QColor(80, 200, 120, int(160 + 60 * pulse))

            # dot
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(PAD_H, y + 6, 8, 8)

            # name
            p.setFont(name_f)
            p.setPen(C_TEXT)
            name = agent.get("name", agent.get("agent_id", "?"))
            p.drawText(PAD_H + 16, y + name_fm.ascent(), name)

            # status badge right
            p.setFont(snip_f)
            p.setPen(col)
            p.drawText(0, y, w - PAD_H, 16, Qt.AlignRight | Qt.AlignVCenter, st)

            # last output snippet
            snippet = ""
            out = agent.get("output", "")
            if out:
                last = [l.strip() for l in out.splitlines() if l.strip()]
                snippet = last[-1][:80] if last else ""
            if snippet:
                p.setFont(snip_f)
                p.setPen(C_DIM)
                p.drawText(PAD_H + 16, y + name_fm.height() + snip_fm.ascent() - 2, snippet)

            y += row_h

    # ── drag to move + edge resize ────────────────────────────────────────

    RESIZE_MARGIN = 8

    def _edge(self, pos):
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        m = self.RESIZE_MARGIN
        bottom = y > h - m
        right  = x > w - m
        left   = x < m
        if bottom and right: return "br"
        if bottom and left:  return "bl"
        if bottom:           return "b"
        if right:            return "r"
        if left:             return "l"
        return None

    def mousePressEvent(self, e):
        from PyQt5.QtCore import Qt as _Qt
        if e.button() == _Qt.LeftButton:
            edge = self._edge(e.pos())
            if edge:
                self._resize_edge = edge
                self._resize_start_geo = self.geometry()
                self._resize_start_pos = e.globalPos()
            else:
                self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()
                self._resize_edge = None

    def mouseMoveEvent(self, e):
        from PyQt5.QtCore import Qt as _Qt
        if e.buttons() & _Qt.LeftButton:
            if self._resize_edge:
                delta = e.globalPos() - self._resize_start_pos
                g = self._resize_start_geo
                x, y, w, h = g.x(), g.y(), g.width(), g.height()
                edge = self._resize_edge
                if "r" in edge: w = max(400, w + delta.x())
                if "l" in edge: x += delta.x(); w = max(400, w - delta.x())
                if "b" in edge: h = max(MIN_H, h + delta.y())
                self.setGeometry(x, y, w, h)
                self._cur_h = h
            elif self._drag_pos:
                self.move(e.globalPos() - self._drag_pos)
        else:
            edge = self._edge(e.pos())
            from PyQt5.QtCore import Qt as _Qt
            cursors = {
                "br": _Qt.SizeFDiagCursor, "bl": _Qt.SizeBDiagCursor,
                "b":  _Qt.SizeVerCursor,
                "r":  _Qt.SizeHorCursor,  "l":  _Qt.SizeHorCursor,
            }
            self.setCursor(cursors.get(edge, _Qt.ArrowCursor))

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        self._resize_edge = None

    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        step  = 40
        self._scroll_y -= int(delta / 120) * step
        viewport_h = self.height() - PAD_TOP - PAD_BOT
        total_h    = self._content_height() - PAD_TOP - PAD_BOT
        max_scroll = max(0, total_h - viewport_h)
        self._scroll_y = max(0, min(self._scroll_y, max_scroll))
        # if user scrolled up from bottom, stop auto-scroll
        self._user_scrolled = self._scroll_y < max_scroll
        self.update()
