"""
Full chat UI — Claude Code / ChatGPT style.
Sessions sidebar (left) + chat view (right).
Launched from HUD "Open" button or directly: python chat.py
"""

import sys
import time
import threading
from PyQt5.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QScrollArea, QLabel,
    QLineEdit, QPushButton, QFrame, QSizePolicy, QSplitter,
    QTextEdit,
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer, QSize
from PyQt5.QtGui import QColor, QFont, QPalette, QTextCursor, QIcon

import session as sess_mgr

# ── palette ───────────────────────────────────────────────────────────────────
BG_MAIN   = "#111111"
BG_SIDE   = "#1a1a1a"
BG_MSG_U  = "#1e3a5f"   # user bubble
BG_MSG_A  = "#1e1e1e"   # assistant bubble
BG_INPUT  = "#1e1e1e"
BG_HOVER  = "#252525"
BG_SEL    = "#2a2a2a"

C_TEXT    = "#ebebeb"
C_DIM     = "#888888"
C_ACCENT  = "#4a9eff"
C_BORDER  = "#2a2a2a"
C_REC     = "#ff5050"

FONT_UI   = "SF Pro Display, Segoe UI, Arial"
FONT_MONO = "JetBrains Mono, Consolas, monospace"

SS_BASE = f"""
QWidget {{ background: {BG_MAIN}; color: {C_TEXT}; font-family: {FONT_UI}; font-size: 13px; }}
QScrollBar:vertical {{ background: #1a1a1a; width: 6px; border-radius: 3px; }}
QScrollBar::handle:vertical {{ background: #333; border-radius: 3px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}
"""

SS_SIDE = f"""
QListWidget {{ background: {BG_SIDE}; border: none; outline: none; padding: 4px 0; }}
QListWidget::item {{ padding: 10px 14px; border-radius: 8px; margin: 1px 6px; color: {C_DIM}; font-size: 12px; }}
QListWidget::item:selected {{ background: {BG_SEL}; color: {C_TEXT}; }}
QListWidget::item:hover:!selected {{ background: {BG_HOVER}; color: {C_TEXT}; }}
"""

SS_INPUT = f"""
QTextEdit {{
    background: {BG_INPUT}; color: {C_TEXT};
    border: 1px solid {C_BORDER}; border-radius: 12px;
    padding: 10px 14px; font-size: 13px;
    font-family: {FONT_UI};
}}
QTextEdit:focus {{ border: 1px solid #3a3a3a; }}
"""

SS_BTN_SEND = f"""
QPushButton {{
    background: {C_ACCENT}; color: white; border: none;
    border-radius: 10px; padding: 8px 20px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover {{ background: #5aaeff; }}
QPushButton:pressed {{ background: #3a8eee; }}
QPushButton:disabled {{ background: #2a2a2a; color: #555; }}
"""

SS_BTN_NEW = f"""
QPushButton {{
    background: transparent; color: {C_ACCENT}; border: 1px solid #2a3a5a;
    border-radius: 8px; padding: 6px 14px; font-size: 12px;
}}
QPushButton:hover {{ background: #1a2a3a; }}
"""

SS_BTN_DEL = f"""
QPushButton {{
    background: transparent; color: #555; border: none;
    border-radius: 4px; padding: 2px 6px; font-size: 11px;
}}
QPushButton:hover {{ color: #ff5050; }}
"""


# ── emitter ───────────────────────────────────────────────────────────────────

class ChatEmitter(QObject):
    token   = pyqtSignal(str)   # full accumulated text so far
    done    = pyqtSignal()
    error   = pyqtSignal(str)


# ── message bubble ────────────────────────────────────────────────────────────

class Bubble(QFrame):
    def __init__(self, role: str, content: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self._is_streaming = (role == "assistant" and not content)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self._label = QTextEdit()
        self._label.setReadOnly(True)
        self._label.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._label.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._label.document().setDocumentMargin(12)
        self._label.setFrameShape(QFrame.NoFrame)

        if role == "user":
            self._label.setStyleSheet(f"""
                QTextEdit {{
                    background: {BG_MSG_U}; color: {C_TEXT};
                    border-radius: 14px; border-bottom-right-radius: 4px;
                    font-size: 13px; font-family: {FONT_UI};
                }}
            """)
            layout.addStretch()
            layout.addWidget(self._label)
            self._label.setMaximumWidth(520)
        else:
            self._label.setStyleSheet(f"""
                QTextEdit {{
                    background: {BG_MSG_A}; color: {C_TEXT};
                    border-radius: 14px; border-bottom-left-radius: 4px;
                    font-size: 13px; font-family: {FONT_UI};
                    border: 1px solid {C_BORDER};
                }}
            """)
            layout.addWidget(self._label)
            layout.addStretch()
            self._label.setMaximumWidth(580)

        self.set_text(content or ("▋" if self._is_streaming else ""))

    def set_text(self, text: str):
        self._label.setPlainText(text)
        self._resize()

    def append_text(self, full_text: str):
        cursor_suffix = "▋" if self._is_streaming else ""
        self._label.setPlainText(full_text + cursor_suffix)
        self._resize()

    def finalize(self, full_text: str):
        self._is_streaming = False
        self._label.setPlainText(full_text)
        self._resize()

    def _resize(self):
        doc = self._label.document()
        doc.setTextWidth(self._label.viewport().width() or 500)
        h = int(doc.size().height()) + 4
        self._label.setFixedHeight(max(40, h))


# ── chat panel ────────────────────────────────────────────────────────────────

class ChatPanel(QWidget):
    send_requested = pyqtSignal(str)  # text to send

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bubbles: list[Bubble] = []
        self._streaming_bubble: Bubble | None = None
        self._session_id: str | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # header
        self._header = QLabel("New conversation")
        self._header.setStyleSheet(f"color: {C_DIM}; font-size: 11px; padding: 12px 20px; border-bottom: 1px solid {C_BORDER};")
        layout.addWidget(self._header)

        # scroll area for messages
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(f"QScrollArea {{ background: {BG_MAIN}; border: none; }}")

        self._msg_container = QWidget()
        self._msg_container.setStyleSheet(f"background: {BG_MAIN};")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(24, 16, 24, 16)
        self._msg_layout.setSpacing(8)
        self._msg_layout.addStretch()

        self._scroll.setWidget(self._msg_container)
        layout.addWidget(self._scroll, 1)

        # input row
        input_frame = QFrame()
        input_frame.setStyleSheet(f"background: {BG_MAIN}; border-top: 1px solid {C_BORDER};")
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(16, 12, 16, 16)
        input_layout.setSpacing(10)

        self._input = QTextEdit()
        self._input.setStyleSheet(SS_INPUT)
        self._input.setPlaceholderText("Message…")
        self._input.setFixedHeight(48)
        self._input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._input.installEventFilter(self)

        self._send_btn = QPushButton("Send")
        self._send_btn.setStyleSheet(SS_BTN_SEND)
        self._send_btn.setFixedSize(72, 40)
        self._send_btn.clicked.connect(self._do_send)

        input_layout.addWidget(self._input)
        input_layout.addWidget(self._send_btn)
        layout.addWidget(input_frame)

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        from PyQt5.QtGui import QKeyEvent
        if obj is self._input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if not (event.modifiers() & Qt.ShiftModifier):
                    self._do_send()
                    return True
        return super().eventFilter(obj, event)

    def _do_send(self):
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()
        self._send_btn.setEnabled(False)
        self.add_bubble("user", text)
        self._start_streaming_bubble()
        self.send_requested.emit(text)

    def add_bubble(self, role: str, content: str) -> Bubble:
        b = Bubble(role, content)
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, b)
        self._bubbles.append(b)
        QTimer.singleShot(10, self._scroll_bottom)
        return b

    def _start_streaming_bubble(self) -> Bubble:
        b = Bubble("assistant", "")
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, b)
        self._bubbles.append(b)
        self._streaming_bubble = b
        QTimer.singleShot(10, self._scroll_bottom)
        return b

    def on_token(self, full_text: str):
        if self._streaming_bubble:
            self._streaming_bubble.append_text(full_text)
            self._scroll_bottom()

    def on_done(self, full_text: str):
        if self._streaming_bubble:
            self._streaming_bubble.finalize(full_text)
            self._streaming_bubble = None
        self._send_btn.setEnabled(True)
        self._input.setFocus()

    def on_error(self, msg: str):
        if self._streaming_bubble:
            self._streaming_bubble.finalize(f"[error: {msg}]")
            self._streaming_bubble = None
        self._send_btn.setEnabled(True)

    def load_session(self, sid: str):
        self._session_id = sid
        data = sess_mgr.read_session(sid)
        if not data:
            return
        self._header.setText(data.get("title", sid))
        # clear existing
        for b in self._bubbles:
            b.deleteLater()
        self._bubbles.clear()
        for m in data.get("messages", []):
            if m["role"] in ("user", "assistant"):
                self.add_bubble(m["role"], m["content"])
        QTimer.singleShot(50, self._scroll_bottom)

    def clear_for_new(self):
        self._session_id = None
        self._header.setText("New conversation")
        for b in self._bubbles:
            b.deleteLater()
        self._bubbles.clear()
        self._streaming_bubble = None
        self._send_btn.setEnabled(True)

    def _scroll_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())


# ── sessions sidebar ──────────────────────────────────────────────────────────

class Sidebar(QWidget):
    session_selected = pyqtSignal(str)   # sid
    new_requested    = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setStyleSheet(f"background: {BG_SIDE};")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # top bar
        top = QFrame()
        top.setStyleSheet(f"background: {BG_SIDE}; border-bottom: 1px solid {C_BORDER};")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(10, 12, 10, 12)
        title = QLabel("Sessions")
        title.setStyleSheet(f"color: {C_TEXT}; font-size: 13px; font-weight: 600; background: transparent;")
        new_btn = QPushButton("+ New")
        new_btn.setStyleSheet(SS_BTN_NEW)
        new_btn.clicked.connect(self.new_requested.emit)
        top_layout.addWidget(title)
        top_layout.addStretch()
        top_layout.addWidget(new_btn)
        layout.addWidget(top)

        # list
        self._list = QListWidget()
        self._list.setStyleSheet(SS_SIDE)
        self._list.setSpacing(0)
        self._list.itemClicked.connect(lambda item: self.session_selected.emit(item.data(Qt.UserRole)))
        layout.addWidget(self._list, 1)

    def refresh(self, current_sid: str | None = None):
        self._list.clear()
        for s in sess_mgr.list_sessions(50):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, s["id"])
            title = s["title"] if len(s["title"]) <= 28 else s["title"][:26] + "…"
            t = time.strftime("%b %d", time.localtime(s["started"])) if s.get("started") else ""
            item.setText(f"{title}\n{t}  ·  {s['message_count']} msgs")
            item.setSizeHint(QSize(200, 52))
            self._list.addItem(item)
            if s["id"] == current_sid:
                self._list.setCurrentItem(item)

    def mark_current(self, sid: str):
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) == sid:
                self._list.setCurrentItem(item)
                return


# ── main window ───────────────────────────────────────────────────────────────

class ChatWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._current_sid: str | None = None
        self._streaming_full = ""
        self._emitter = ChatEmitter()
        self._emitter.token.connect(self._on_token, Qt.QueuedConnection)
        self._emitter.done.connect(self._on_done, Qt.QueuedConnection)
        self._emitter.error.connect(self._on_error, Qt.QueuedConnection)
        self._setup_ui()
        self._init_session()

    def _setup_ui(self):
        self.setWindowTitle("Voice Spotlight — Chat")
        self.resize(980, 700)
        self.setMinimumSize(600, 400)
        self.setStyleSheet(SS_BASE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._sidebar = Sidebar()
        self._chat = ChatPanel()

        # divider
        div = QFrame()
        div.setFrameShape(QFrame.VLine)
        div.setStyleSheet(f"color: {C_BORDER};")

        layout.addWidget(self._sidebar)
        layout.addWidget(div)
        layout.addWidget(self._chat, 1)

        self._sidebar.session_selected.connect(self._load_session)
        self._sidebar.new_requested.connect(self._new_session)
        self._chat.send_requested.connect(self._send)

    def _init_session(self):
        data = sess_mgr.load_current()
        self._current_sid = data["id"]
        if data.get("messages"):
            self._chat.load_session(self._current_sid)
        self._sidebar.refresh(self._current_sid)

    def _load_session(self, sid: str):
        self._current_sid = sid
        self._chat.load_session(sid)
        self._sidebar.mark_current(sid)
        # switch agent to this session
        import agent as agent_mod
        agent_mod._history_lock.acquire()
        try:
            sdata = sess_mgr.read_session(sid)
            if sdata:
                agent_mod._current_session = sdata
                agent_mod._history = list(sdata.get("messages", []))
                sess_mgr._set_current(sid)
        finally:
            agent_mod._history_lock.release()

    def _new_session(self):
        import agent as agent_mod
        agent_mod._reset_session()
        self._current_sid = agent_mod._current_session["id"]
        self._chat.clear_for_new()
        self._sidebar.refresh(self._current_sid)

    def _send(self, text: str):
        self._streaming_full = ""
        import agent as agent_mod
        # if this chat panel is on a different session than agent, switch first
        if self._current_sid and self._current_sid != agent_mod._current_session.get("id"):
            self._load_session(self._current_sid)

        def _go():
            try:
                import asyncio
                if agent_mod._gpu_reachable():
                    asyncio.run(agent_mod._run_stream(
                        text,
                        on_token=lambda t: self._emitter.token.emit(t),
                    ))
                else:
                    agent_mod._opencode_stream(
                        text,
                        on_token=lambda t: self._emitter.token.emit(t),
                    )
            except Exception as e:
                self._emitter.error.emit(str(e))
            finally:
                self._emitter.done.emit()

        threading.Thread(target=_go, daemon=True).start()

    def _on_token(self, full: str):
        self._streaming_full = full
        self._chat.on_token(full)

    def _on_done(self):
        self._chat.on_done(self._streaming_full)
        self._sidebar.refresh(self._current_sid)

    def _on_error(self, msg: str):
        self._chat.on_error(msg)


# ── launch ────────────────────────────────────────────────────────────────────

_chat_window: ChatWindow | None = None


def open_chat():
    """Open or raise the chat window. Call from HUD button (main Qt thread)."""
    global _chat_window
    if _chat_window is None or not _chat_window.isVisible():
        _chat_window = ChatWindow()
        _chat_window.show()
    else:
        _chat_window.raise_()
        _chat_window.activateWindow()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = ChatWindow()
    win.show()
    sys.exit(app.exec_())
