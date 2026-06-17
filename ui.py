"""
HUD overlay — floats at top-center, always on top, click-through when idle.
No input box. Voice only. Streams answer like a futuristic heads-up display.
"""

import threading
from PyQt5.QtWidgets import QWidget, QApplication, QDesktopWidget
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QLinearGradient


WIDTH  = 860
HEIGHT = 220   # expanded height
COLLAPSED_H = 4   # invisible sliver when idle (just a thin glow line)


# Space-theme palette
C_BG        = QColor(4,   6,  20, 230)   # near-black deep space
C_BORDER    = QColor(0, 180, 255, 80)    # cyan glow border
C_GLOW      = QColor(0, 200, 255, 18)    # soft outer glow fill
C_TEXT      = QColor(200, 240, 255, 255) # ice-white text
C_DIM       = QColor(80, 160, 200, 160)  # dimmer for status line
C_ACCENT    = QColor(0, 220, 255, 255)   # bright cyan accent
C_REC       = QColor(255, 60,  80, 255)  # red recording dot


FONT_MAIN   = ("JetBrains Mono", "Fira Code", "Monospace")
FONT_STATUS = ("SF Pro Display", "Segoe UI", "Ubuntu", "sans-serif")


class Emitter(QObject):
    token          = pyqtSignal(str)
    done           = pyqtSignal()
    show_recording = pyqtSignal()
    hide_recording = pyqtSignal()
    clear          = pyqtSignal()


class HUD(QWidget):
    def __init__(self):
        super().__init__()
        self._text       = ""
        self._recording  = False
        self._expanded   = False
        self._dot_phase  = 0       # animated recording dot
        self._scan_phase = 0       # scan-line animation
        self.emitter     = Emitter()

        self._build()
        self._connect()
        self._start_timers()

    def _build(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                   # no taskbar entry
            | Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # never steal focus
        self.setFocusPolicy(Qt.NoFocus)

        # position: top-center of primary screen
        screen = QDesktopWidget().screenGeometry(0)
        x = screen.x() + (screen.width() - WIDTH) // 2
        y = screen.y() + 18
        self.setGeometry(x, y, WIDTH, COLLAPSED_H)
        self.show()

    def _connect(self):
        self.emitter.token.connect(self._on_token, Qt.QueuedConnection)
        self.emitter.done.connect(self._on_done, Qt.QueuedConnection)
        self.emitter.show_recording.connect(self._on_recording_start, Qt.QueuedConnection)
        self.emitter.hide_recording.connect(self._on_recording_stop, Qt.QueuedConnection)
        self.emitter.clear.connect(self._on_clear, Qt.QueuedConnection)

    def _start_timers(self):
        # animate recording dot + scan line at 30fps
        self._anim_timer = QTimer()
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start()

        # auto-collapse after 12s of no activity
        self._collapse_timer = QTimer()
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse)

    def _tick(self):
        if self._recording or self._expanded:
            self._dot_phase  = (self._dot_phase  + 1) % 60
            self._scan_phase = (self._scan_phase + 2) % HEIGHT
            self.update()

    # ── signals ───────────────────────────────────────────────────────────────

    def _on_recording_start(self):
        self._recording = True
        self._text = ""
        self._collapse_timer.stop()
        self._expand()
        self.update()

    def _on_recording_stop(self):
        self._recording = False
        self.update()

    def _on_token(self, text):
        self._text = text
        self._collapse_timer.stop()
        self._expand()
        self.update()

    def _on_done(self):
        self._collapse_timer.start(12000)   # collapse 12s after answer finishes

    def _on_clear(self):
        self._text = ""
        self._collapse()

    # ── expand / collapse ─────────────────────────────────────────────────────

    def _expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._anim(COLLAPSED_H, HEIGHT)

    def _collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._text = ""
        self._anim(self.height(), COLLAPSED_H)

    def _anim(self, h0, h1):
        screen = QDesktopWidget().screenGeometry(0)
        x = screen.x() + (screen.width() - WIDTH) // 2
        y = screen.y() + 18
        self._a = QPropertyAnimation(self, b"geometry")
        self._a.setDuration(260)
        from PyQt5.QtCore import QRect
        self._a.setStartValue(QRect(x, y, WIDTH, h0))
        self._a.setEndValue(QRect(x, y, WIDTH, h1))
        self._a.setEasingCurve(QEasingCurve.OutCubic)
        self._a.start()

    # ── paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        w, h = self.width(), self.height()

        if h <= COLLAPSED_H + 2:
            # collapsed: just a glowing line
            p.setPen(Qt.NoPen)
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(0.3, C_ACCENT)
            grad.setColorAt(0.7, C_ACCENT)
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(grad)
            p.drawRect(0, 0, w, COLLAPSED_H)
            return

        # background
        p.setPen(Qt.NoPen)
        p.setBrush(C_BG)
        p.drawRoundedRect(1, 1, w - 2, h - 2, 10, 10)

        # subtle scan-line shimmer
        scan_y = self._scan_phase % h
        scan_grad = QLinearGradient(0, scan_y - 6, 0, scan_y + 6)
        scan_grad.setColorAt(0.0, QColor(0, 200, 255, 0))
        scan_grad.setColorAt(0.5, QColor(0, 200, 255, 10))
        scan_grad.setColorAt(1.0, QColor(0, 200, 255, 0))
        p.setBrush(scan_grad)
        p.drawRoundedRect(1, 1, w - 2, h - 2, 10, 10)

        # border glow
        pen = QPen(C_BORDER)
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(1, 1, w - 2, h - 2, 10, 10)

        # corner accents
        p.setPen(QPen(C_ACCENT, 2))
        aw = 18
        # top-left
        p.drawLine(4,      4,      4 + aw, 4)
        p.drawLine(4,      4,      4,      4 + aw)
        # top-right
        p.drawLine(w - 4 - aw, 4,      w - 4, 4)
        p.drawLine(w - 4,      4,      w - 4, 4 + aw)
        # bottom-left
        p.drawLine(4,          h - 4,  4 + aw, h - 4)
        p.drawLine(4,          h - 4 - aw, 4,  h - 4)
        # bottom-right
        p.drawLine(w - 4 - aw, h - 4,  w - 4, h - 4)
        p.drawLine(w - 4,      h - 4 - aw, w - 4, h - 4)

        # ── status line ────────────────────────────────────────────────────
        p.setPen(C_DIM)
        sf = QFont(FONT_STATUS[0], 9)
        sf.setFamily(", ".join(FONT_STATUS))
        p.setFont(sf)

        if self._recording:
            # pulsing dot
            pulse = abs(self._dot_phase - 30) / 30.0   # 0→1→0
            dot_alpha = int(120 + 135 * pulse)
            dot_color = QColor(255, 60, 80, dot_alpha)
            p.setBrush(dot_color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(16, 14, 8, 8)
            p.setPen(QColor(255, 60, 80, dot_alpha))
            p.drawText(30, 22, "RECORDING")
        else:
            p.setPen(C_DIM)
            p.drawText(16, 22, "AI · VOICE")

        # right-side label
        p.setPen(C_DIM)
        label = "F9 to speak"
        p.drawText(w - 90, 22, label)

        # separator line
        p.setPen(QPen(QColor(0, 180, 255, 40), 1))
        p.drawLine(14, 30, w - 14, 30)

        # ── main text area ─────────────────────────────────────────────────
        if not self._text and not self._recording:
            p.setPen(QColor(60, 100, 140, 120))
            hint_f = QFont(FONT_MAIN[0], 13)
            hint_f.setFamily(", ".join(FONT_MAIN))
            p.setFont(hint_f)
            p.drawText(20, 34, w - 40, h - 44, Qt.TextWordWrap, "hold F9 · speak · release")
            p.end()
            return

        tf = QFont(FONT_MAIN[0], 13)
        tf.setFamily(", ".join(FONT_MAIN))
        tf.setLetterSpacing(QFont.AbsoluteSpacing, 0.3)
        p.setFont(tf)
        p.setPen(C_TEXT)

        text_rect_x = 20
        text_rect_y = 38
        text_rect_w = w - 40
        text_rect_h = h - 50

        p.drawText(text_rect_x, text_rect_y, text_rect_w, text_rect_h,
                   Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                   self._text)

        # trailing cursor blink
        if self._recording or self._dot_phase < 30:
            fm = QFontMetrics(tf)
            lines = self._text.split("\n")
            last_line = lines[-1] if lines else ""
            # estimate last-line x
            wrapped_lines = []
            for line in lines:
                while fm.horizontalAdvance(line) > text_rect_w:
                    for cut in range(len(line), 0, -1):
                        if fm.horizontalAdvance(line[:cut]) <= text_rect_w:
                            wrapped_lines.append(line[:cut])
                            line = line[cut:]
                            break
                wrapped_lines.append(line)
            cx = text_rect_x + fm.horizontalAdvance(wrapped_lines[-1] if wrapped_lines else "")
            cy = text_rect_y + (len(wrapped_lines) - 1) * fm.height()
            p.setPen(C_ACCENT)
            p.drawText(cx + 2, cy + fm.ascent(), "▋")

        p.end()

    # prevent the widget from ever accepting clicks / focus
    def mousePressEvent(self, e):
        e.ignore()

    def focusInEvent(self, e):
        e.ignore()
