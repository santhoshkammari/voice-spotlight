"""
Voice Spotlight — Chat + Agents UI.
Claude.ai / Claude Code inspired layout:
  Left sidebar: New Chat btn | Chats section (sessions) | Agents section (workers)
  Right panel: home / chat view / agent view — plain text, no boxes
"""

from __future__ import annotations
import sys, time, threading, json
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QLabel, QPushButton, QFrame, QScrollArea, QSizePolicy,
    QTextEdit, QLineEdit, QSplitter,
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer, QSize
from PyQt5.QtGui import QFont, QColor, QCursor

import session as sess_mgr
import subagent

# ── palette ───────────────────────────────────────────────────────────────────
BG       = "#0f0f0f"
BG_SIDE  = "#171717"
BG_HOVER = "#1f1f1f"
BG_SEL   = "#222222"
C_TEXT   = "#e8e8e8"
C_DIM    = "#606060"
C_DIM2   = "#404040"
C_USER   = "#999999"
C_AI     = "#e8e8e8"
C_ACCENT = "#cc785c"   # Claude's warm orange-ish
C_GREEN  = "#4caf7d"
C_RED    = "#e05252"
C_BORDER = "#1e1e1e"
FONT     = "SF Pro Display, Segoe UI, Arial"
FONT_SZ  = 13

STATUS_C = {"running": C_ACCENT, "completed": C_GREEN, "failed": C_RED,
            "cancelled": C_DIM, "unknown": C_DIM}

SS_GLOBAL = f"""
* {{ font-family: {FONT}; font-size: {FONT_SZ}px; }}
QWidget {{ background: {BG}; color: {C_TEXT}; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 4px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #2a2a2a; border-radius: 2px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QTextEdit {{ background: transparent; color: {C_TEXT}; border: none;
             selection-background-color: #2d3f55; font-size: {FONT_SZ}px; }}
QTextEdit:focus {{ outline: none; }}
QLineEdit {{ background: transparent; color: {C_TEXT}; border: none; font-size: {FONT_SZ}px; }}
"""

# ── signals ───────────────────────────────────────────────────────────────────

class Emitter(QObject):
    token = pyqtSignal(str)
    done  = pyqtSignal()
    error = pyqtSignal(str)


# ── sidebar ───────────────────────────────────────────────────────────────────

class SideItem(QPushButton):
    """Single row in sidebar — session or agent."""
    def __init__(self, primary: str, secondary: str = "", dot_color: str = "", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.primary   = primary
        self.secondary = secondary
        self.dot_color = dot_color
        self._refresh()
        self.toggled.connect(lambda _: self._refresh())

    def _refresh(self):
        sel = self.isChecked()
        bg  = BG_SEL if sel else "transparent"
        dot = f'<span style="color:{self.dot_color};font-size:8px;">●</span> ' if self.dot_color else ""
        dim = f'<div style="color:{C_DIM};font-size:11px;margin-top:1px;">{self.secondary}</div>' if self.secondary else ""
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: {C_TEXT};
                border: none; border-radius: 6px;
                padding: 7px 10px; text-align: left;
            }}
            QPushButton:hover {{ background: {BG_HOVER}; }}
        """)
        # can't do rich html on QPushButton — use QLabel trick via layout? Keep it simple:
        p = self.primary[:34] + "…" if len(self.primary) > 35 else self.primary
        self.setText(p)
        if self.secondary or self.dot_color:
            self.setToolTip(f"{self.primary}\n{self.secondary}")


class SectionHeader(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(f"""
            color: {C_DIM}; font-size: 11px; font-weight: 600;
            padding: 12px 12px 4px 12px; background: transparent;
            letter-spacing: 0.5px;
        """)


class Sidebar(QWidget):
    new_chat    = pyqtSignal()
    open_sess   = pyqtSignal(str)   # sid
    open_agent  = pyqtSignal(str)   # agent_id
    start_agent = pyqtSignal(str, str)  # name, prompt

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(230)
        self.setStyleSheet(f"background: {BG_SIDE}; border-right: 1px solid {C_BORDER};")
        self._sess_items:  dict[str, SideItem] = {}
        self._agent_items: dict[str, SideItem] = {}
        self._selected: SideItem | None = None
        self._setup()

    def _setup(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 12, 8, 12)
        root.setSpacing(0)

        # ── New Chat ──────────────────────────────────────────────────────────
        new_btn = QPushButton("+ New Chat")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {BG_HOVER}; color: {C_TEXT};
                border: 1px solid {C_BORDER}; border-radius: 8px;
                padding: 8px 14px; font-size: 12px; font-weight: 600; text-align: left;
            }}
            QPushButton:hover {{ background: #252525; }}
        """)
        new_btn.clicked.connect(self.new_chat)
        root.addWidget(new_btn)

        # ── scroll area for all items ─────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {BG_SIDE}; border: none; }}")

        self._inner = QWidget()
        self._inner.setStyleSheet(f"background: {BG_SIDE};")
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setContentsMargins(0, 4, 0, 4)
        self._inner_layout.setSpacing(0)

        # Chats section
        self._chats_hdr = SectionHeader("Chats")
        self._inner_layout.addWidget(self._chats_hdr)
        self._chats_area = QVBoxLayout()
        self._chats_area.setSpacing(1)
        self._inner_layout.addLayout(self._chats_area)

        # Agents section
        self._agents_hdr = SectionHeader("Agents")
        self._inner_layout.addWidget(self._agents_hdr)
        self._agents_area = QVBoxLayout()
        self._agents_area.setSpacing(1)
        self._inner_layout.addLayout(self._agents_area)

        # Start agent inline
        self._inner_layout.addWidget(self._make_start_agent_widget())
        self._inner_layout.addStretch()

        scroll.setWidget(self._inner)
        root.addWidget(scroll, 1)

    def _make_start_agent_widget(self) -> QWidget:
        w = QFrame()
        w.setStyleSheet(f"background: transparent;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(4)

        self._agent_name_input = QLineEdit()
        self._agent_name_input.setPlaceholderText("agent-name")
        self._agent_name_input.setStyleSheet(f"""
            QLineEdit {{
                background: {BG_HOVER}; color: {C_TEXT}; border: 1px solid {C_BORDER};
                border-radius: 6px; padding: 5px 8px; font-size: 11px;
            }}
        """)

        self._agent_prompt_input = QLineEdit()
        self._agent_prompt_input.setPlaceholderText("Task…")
        self._agent_prompt_input.setStyleSheet(self._agent_name_input.styleSheet())
        self._agent_prompt_input.returnPressed.connect(self._do_start)

        start_btn = QPushButton("▶ Start Agent")
        start_btn.setCursor(Qt.PointingHandCursor)
        start_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_ACCENT}; color: white; border: none;
                border-radius: 6px; padding: 6px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #d98a6e; }}
        """)
        start_btn.clicked.connect(self._do_start)

        layout.addWidget(self._agent_name_input)
        layout.addWidget(self._agent_prompt_input)
        layout.addWidget(start_btn)
        return w

    def _do_start(self):
        name   = self._agent_name_input.text().strip() or "agent"
        prompt = self._agent_prompt_input.text().strip()
        if not prompt:
            return
        self._agent_name_input.clear()
        self._agent_prompt_input.clear()
        self.start_agent.emit(name, prompt)

    # ── public refresh ────────────────────────────────────────────────────────

    def refresh_sessions(self, current_sid: str | None = None):
        while self._chats_area.count():
            item = self._chats_area.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._sess_items.clear()

        for s in sess_mgr.list_sessions(20):
            title = s["title"] or f"Session {s['id']}"
            btn = SideItem(title)
            btn.setChecked(s["id"] == current_sid)
            sid = s["id"]
            btn.clicked.connect(lambda _, i=sid: self._on_sess(i))
            self._chats_area.addWidget(btn)
            self._sess_items[sid] = btn

    def refresh_agents(self, current_aid: str | None = None):
        while self._agents_area.count():
            item = self._agents_area.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._agent_items.clear()

        try:
            agents = subagent.list_all()
        except Exception:
            agents = []

        for a in agents:
            color = STATUS_C.get(a.get("status", "unknown"), C_DIM)
            btn = SideItem(a.get("name", a["agent_id"]), a.get("status", ""), dot_color=color)
            btn.setChecked(a["agent_id"] == current_aid)
            aid = a["agent_id"]
            btn.clicked.connect(lambda _, i=aid: self._on_agent(i))
            self._agents_area.addWidget(btn)
            self._agent_items[aid] = btn

    def select_sess(self, sid: str):
        self._deselect_all()
        if sid in self._sess_items:
            self._sess_items[sid].setChecked(True)

    def select_agent(self, aid: str):
        self._deselect_all()
        if aid in self._agent_items:
            self._agent_items[aid].setChecked(True)

    def _deselect_all(self):
        for b in list(self._sess_items.values()) + list(self._agent_items.values()):
            b.setChecked(False)

    def _on_sess(self, sid):
        self._deselect_all()
        if sid in self._sess_items:
            self._sess_items[sid].setChecked(True)
        self.open_sess.emit(sid)

    def _on_agent(self, aid):
        self._deselect_all()
        if aid in self._agent_items:
            self._agent_items[aid].setChecked(True)
        self.open_agent.emit(aid)


# ── chat panel ────────────────────────────────────────────────────────────────

class MsgWidget(QFrame):
    """One message — plain text on background, no box."""
    def __init__(self, role: str, text: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self.setStyleSheet("background: transparent; border: none;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        if role == "user":
            # small "You" label
            who = QLabel("You")
            who.setStyleSheet(f"color: {C_DIM}; font-size: 11px; background: transparent; padding: 0;")
            who.setAlignment(Qt.AlignRight)
            layout.addWidget(who)

        self._lbl = QLabel()
        self._lbl.setWordWrap(True)
        self._lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._lbl.setStyleSheet(f"""
            color: {C_USER if role == 'user' else C_AI};
            font-size: {FONT_SZ}px; background: transparent; padding: 0;
            line-height: 1.6;
        """)
        if role == "user":
            self._lbl.setAlignment(Qt.AlignRight)
        else:
            self._lbl.setAlignment(Qt.AlignLeft)

        layout.addWidget(self._lbl)

        if text:
            self.set_text(text)

    def set_text(self, t: str):
        self._lbl.setText(t + ("▋" if not t.endswith("▋") and t == "" else ""))
        self._lbl.setText(t)

    def set_streaming(self, t: str):
        self._lbl.setText(t + "▋")

    def finalize(self, t: str):
        self._lbl.setText(t)


class ChatPanel(QWidget):
    send_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[MsgWidget] = []
        self._streaming: MsgWidget | None = None
        self._setup()

    def _setup(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(f"QScrollArea {{ background: {BG}; border: none; }}")

        self._container = QWidget()
        self._container.setStyleSheet(f"background: {BG};")
        self._msg_layout = QVBoxLayout(self._container)
        self._msg_layout.setContentsMargins(80, 24, 80, 24)
        self._msg_layout.setSpacing(20)
        self._msg_layout.addStretch()

        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll, 1)

        # input area
        input_wrap = QFrame()
        input_wrap.setStyleSheet(f"background: {BG}; border-top: 1px solid {C_BORDER};")
        iw = QVBoxLayout(input_wrap)
        iw.setContentsMargins(80, 0, 80, 16)
        iw.setSpacing(0)

        self._input = QTextEdit()
        self._input.setPlaceholderText("Message…")
        self._input.setFixedHeight(56)
        self._input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._input.installEventFilter(self)
        iw.addWidget(self._input)

        root.addWidget(input_wrap)

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if obj is self._input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
                self._send()
                return True
        return super().eventFilter(obj, event)

    def _send(self):
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()
        self._add_msg("user", text)
        self._streaming = self._add_msg("assistant", "")
        self._streaming.set_streaming("")
        self.send_requested.emit(text)

    def _add_msg(self, role: str, text: str) -> MsgWidget:
        w = MsgWidget(role, text)
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, w)
        self._rows.append(w)
        QTimer.singleShot(10, self._scroll_bottom)
        return w

    def on_token(self, full: str):
        if self._streaming:
            self._streaming.set_streaming(full)
            self._scroll_bottom()

    def on_done(self, full: str):
        if self._streaming:
            self._streaming.finalize(full)
            self._streaming = None

    def on_error(self, msg: str):
        if self._streaming:
            self._streaming.finalize(f"[error: {msg}]")
            self._streaming = None

    def load(self, sid: str):
        for r in self._rows:
            r.deleteLater()
        self._rows.clear()
        self._streaming = None
        data = sess_mgr.read_session(sid)
        if not data:
            return
        for m in data.get("messages", []):
            if m["role"] in ("user", "assistant"):
                self._add_msg(m["role"], m["content"])
        QTimer.singleShot(60, self._scroll_bottom)

    def clear(self):
        for r in self._rows:
            r.deleteLater()
        self._rows.clear()
        self._streaming = None

    def focus_input(self):
        self._input.setFocus()

    def _scroll_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())


# ── agent panel ───────────────────────────────────────────────────────────────

class AgentPanel(QWidget):
    followup_requested = pyqtSignal(str, str)   # agent_id, text

    def __init__(self, parent=None):
        super().__init__(parent)
        self._aid: str | None = None
        self._rows: list[MsgWidget] = []
        self._streaming: MsgWidget | None = None
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(1500)
        self._poll_timer.timeout.connect(self._poll)
        self._last_output = ""
        self._setup()

    def _setup(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header strip
        self._hdr = QLabel("")
        self._hdr.setStyleSheet(f"""
            color: {C_DIM}; font-size: 11px; padding: 10px 80px;
            border-bottom: 1px solid {C_BORDER}; background: {BG};
        """)
        root.addWidget(self._hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(f"QScrollArea {{ background: {BG}; border: none; }}")

        self._container = QWidget()
        self._container.setStyleSheet(f"background: {BG};")
        self._msg_layout = QVBoxLayout(self._container)
        self._msg_layout.setContentsMargins(80, 24, 80, 24)
        self._msg_layout.setSpacing(20)
        self._msg_layout.addStretch()
        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll, 1)

        # followup input
        input_wrap = QFrame()
        input_wrap.setStyleSheet(f"background: {BG}; border-top: 1px solid {C_BORDER};")
        iw = QVBoxLayout(input_wrap)
        iw.setContentsMargins(80, 0, 80, 16)

        self._input = QTextEdit()
        self._input.setPlaceholderText("Send a follow-up to this agent…")
        self._input.setFixedHeight(52)
        self._input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._input.installEventFilter(self)
        iw.addWidget(self._input)
        root.addWidget(input_wrap)

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if obj is self._input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
                self._send()
                return True
        return super().eventFilter(obj, event)

    def _send(self):
        text = self._input.toPlainText().strip()
        if not text or not self._aid:
            return
        self._input.clear()
        self._add_row("user", text)
        self._streaming = self._add_row("assistant", "")
        self._streaming.set_streaming("")
        self.followup_requested.emit(self._aid, text)

    def _add_row(self, role: str, text: str) -> MsgWidget:
        w = MsgWidget(role, text)
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, w)
        self._rows.append(w)
        QTimer.singleShot(10, self._scroll_bottom)
        return w

    def load(self, aid: str):
        self._poll_timer.stop()
        self._aid = aid
        self._last_output = ""
        for r in self._rows:
            r.deleteLater()
        self._rows.clear()
        self._streaming = None

        meta = subagent._read_meta(aid) or {}
        name   = meta.get("name", aid)
        status = meta.get("status", "unknown")
        prompt = meta.get("prompt", "")
        self._hdr.setText(f"{name}  ·  {status}  ·  {prompt[:80]}")

        # load history
        for m in self._load_history(aid):
            if m["role"] in ("user", "assistant"):
                self._add_row(m["role"], m["content"])

        # if running, show live tail
        if status == "running":
            out = meta.get("output", "")
            if out:
                self._last_output = out
                w = self._add_row("assistant", "")
                w.set_streaming(out)
                self._streaming = w
            self._poll_timer.start()
        else:
            # show final output if no history
            if not self._rows:
                out = meta.get("output", "")
                if out:
                    self._add_row("assistant", out)

        QTimer.singleShot(60, self._scroll_bottom)

    def on_token(self, full: str):
        if self._streaming:
            self._streaming.set_streaming(full)
            self._scroll_bottom()

    def on_done(self, full: str):
        if self._streaming:
            self._streaming.finalize(full)
            self._streaming = None

    def on_error(self, msg: str):
        if self._streaming:
            self._streaming.finalize(f"[error: {msg}]")
            self._streaming = None

    def _poll(self):
        if not self._aid:
            return
        meta = subagent._read_meta(self._aid) or {}
        status = meta.get("status", "unknown")
        name   = meta.get("name", self._aid)
        prompt = meta.get("prompt", "")
        self._hdr.setText(f"{name}  ·  {status}  ·  {prompt[:80]}")
        if status != "running":
            self._poll_timer.stop()
            if self._streaming:
                out = meta.get("output", "")
                self._streaming.finalize(out or "")
                self._streaming = None
            return
        out = meta.get("output", "")
        if out and out != self._last_output:
            self._last_output = out
            if self._streaming:
                self._streaming.set_streaming(out)
            else:
                w = self._add_row("assistant", "")
                w.set_streaming(out)
                self._streaming = w
        self._scroll_bottom()

    def _load_history(self, aid: str) -> list[dict]:
        p = Path.home() / ".voice-spotlight" / "agents" / f"{aid}.session.json"
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text())
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _scroll_bottom(self):
        self._scroll.verticalScrollBar().setValue(self._scroll.verticalScrollBar().maximum())


# ── home screen ───────────────────────────────────────────────────────────────

class HomePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {BG};")
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignCenter)
        root.setSpacing(12)

        greeting = QLabel("Voice Spotlight")
        greeting.setStyleSheet(f"color: {C_TEXT}; font-size: 26px; font-weight: 300; background: transparent;")
        greeting.setAlignment(Qt.AlignCenter)

        sub = QLabel("Start a chat or launch an agent from the sidebar.")
        sub.setStyleSheet(f"color: {C_DIM}; font-size: 13px; background: transparent;")
        sub.setAlignment(Qt.AlignCenter)

        root.addStretch()
        root.addWidget(greeting)
        root.addWidget(sub)
        root.addStretch()


# ── main window ───────────────────────────────────────────────────────────────

class ChatWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._current_sid: str | None = None
        self._current_aid: str | None = None
        self._stream_full = ""

        # one emitter for chat, one for agent followups
        self._chat_emitter  = Emitter()
        self._agent_emitter = Emitter()
        self._chat_emitter.token.connect(lambda t: (setattr(self, '_stream_full', t), self._chat.on_token(t)), Qt.QueuedConnection)
        self._chat_emitter.done.connect(lambda: (self._chat.on_done(self._stream_full), self._sidebar.refresh_sessions(self._current_sid)), Qt.QueuedConnection)
        self._chat_emitter.error.connect(lambda m: self._chat.on_error(m), Qt.QueuedConnection)

        self._agent_emitter.token.connect(lambda t: (setattr(self, '_stream_full', t), self._agent_panel.on_token(t)), Qt.QueuedConnection)
        self._agent_emitter.done.connect(lambda: self._agent_panel.on_done(self._stream_full), Qt.QueuedConnection)
        self._agent_emitter.error.connect(lambda m: self._agent_panel.on_error(m), Qt.QueuedConnection)

        self._setup()
        self._init()

        # agent list auto-refresh
        self._agent_refresh = QTimer()
        self._agent_refresh.setInterval(3000)
        self._agent_refresh.timeout.connect(lambda: self._sidebar.refresh_agents(self._current_aid))
        self._agent_refresh.start()

    def _setup(self):
        self.setWindowTitle("Voice Spotlight")
        self.resize(980, 680)
        self.setMinimumSize(600, 400)
        self.setStyleSheet(SS_GLOBAL)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # sidebar
        self._sidebar = Sidebar()
        self._sidebar.new_chat.connect(self._new_chat)
        self._sidebar.open_sess.connect(self._open_sess)
        self._sidebar.open_agent.connect(self._open_agent)
        self._sidebar.start_agent.connect(self._start_agent)
        root.addWidget(self._sidebar)

        # right content stack
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {BG};")

        self._home        = HomePanel()
        self._chat        = ChatPanel()
        self._agent_panel = AgentPanel()

        self._stack.addWidget(self._home)         # 0
        self._stack.addWidget(self._chat)         # 1
        self._stack.addWidget(self._agent_panel)  # 2

        self._chat.send_requested.connect(self._send_chat)
        self._agent_panel.followup_requested.connect(self._send_agent_followup)

        root.addWidget(self._stack, 1)

    def _init(self):
        data = sess_mgr.load_current()
        self._current_sid = data["id"]
        self._sidebar.refresh_sessions(self._current_sid)
        self._sidebar.refresh_agents()
        # show home or load current session if it has messages
        if data.get("messages"):
            self._open_sess(self._current_sid)
        else:
            self._stack.setCurrentIndex(0)

    # ── actions ───────────────────────────────────────────────────────────────

    def _new_chat(self):
        import agent as agent_mod
        agent_mod._reset_session()
        self._current_sid = agent_mod._current_session["id"]
        self._chat.clear()
        self._stack.setCurrentIndex(1)
        self._chat.focus_input()
        self._sidebar.refresh_sessions(self._current_sid)
        self._sidebar.select_sess(self._current_sid)

    def _open_sess(self, sid: str):
        # switch agent history to this session
        import agent as agent_mod
        with agent_mod._history_lock:
            sdata = sess_mgr.read_session(sid)
            if sdata:
                agent_mod._current_session = sdata
                agent_mod._history = list(sdata.get("messages", []))
                sess_mgr._set_current(sid)
        self._current_sid = sid
        self._chat.load(sid)
        self._stack.setCurrentIndex(1)
        self._sidebar.select_sess(sid)

    def _open_agent(self, aid: str):
        self._current_aid = aid
        self._agent_panel.load(aid)
        self._stack.setCurrentIndex(2)
        self._sidebar.select_agent(aid)

    def _start_agent(self, name: str, prompt: str):
        aid = subagent.launch(name, prompt)
        self._sidebar.refresh_agents(aid)
        self._open_agent(aid)

    # ── streaming ─────────────────────────────────────────────────────────────

    def _send_chat(self, text: str):
        self._stream_full = ""
        emitter = self._chat_emitter

        def _go():
            try:
                import agent as agent_mod, asyncio
                if agent_mod._gpu_reachable():
                    asyncio.run(agent_mod._run_stream(text, on_token=lambda t: emitter.token.emit(t)))
                else:
                    agent_mod._opencode_stream(text, on_token=lambda t: emitter.token.emit(t))
            except Exception as e:
                emitter.error.emit(str(e))
            finally:
                emitter.done.emit()

        threading.Thread(target=_go, daemon=True).start()

    def _send_agent_followup(self, aid: str, text: str):
        self._stream_full = ""
        emitter = self._agent_emitter

        def _go():
            try:
                import agent as agent_mod, asyncio
                if agent_mod._gpu_reachable():
                    asyncio.run(agent_mod._run_stream(text, on_token=lambda t: emitter.token.emit(t)))
                else:
                    agent_mod._opencode_stream(text, on_token=lambda t: emitter.token.emit(t))
            except Exception as e:
                emitter.error.emit(str(e))
            finally:
                emitter.done.emit()

        threading.Thread(target=_go, daemon=True).start()


# ── launch helper (called from HUD "Open" button) ─────────────────────────────

_window: ChatWindow | None = None


def open_chat():
    global _window
    if _window is None or not _window.isVisible():
        _window = ChatWindow()
        _window.show()
    else:
        _window.raise_()
        _window.activateWindow()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    w = ChatWindow()
    w.show()
    sys.exit(app.exec_())
