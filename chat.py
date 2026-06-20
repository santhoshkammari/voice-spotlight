"""
Voice Spotlight — Chat + Agents UI.
Minimal dark theme: no boxes, no bubbles — pure text flow.
Left nav: Chat | Agents tabs.
"""

from __future__ import annotations
import sys, time, threading, json
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QLabel, QLineEdit, QPushButton, QFrame, QScrollArea, QSizePolicy,
    QTextEdit, QListWidget, QListWidgetItem,
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer, QSize
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QBrush, QTextCursor

import session as sess_mgr
import subagent

# ── palette ───────────────────────────────────────────────────────────────────
BG       = "#0d0d0d"
BG_NAV   = "#111111"
C_TEXT   = "#e8e8e8"
C_DIM    = "#555555"
C_USER   = "#888888"      # user messages — subdued
C_AI     = "#e8e8e8"      # assistant — bright
C_ACCENT = "#4a9eff"
C_GREEN  = "#3ecf6a"
C_RED    = "#ff5050"
C_BORDER = "#1e1e1e"
FONT     = "SF Pro Display, Segoe UI, Arial"
FONT_SZ  = 13

SS = f"""
* {{ font-family: {FONT}; font-size: {FONT_SZ}px; }}
QWidget {{ background: {BG}; color: {C_TEXT}; }}
QScrollBar:vertical {{ background: transparent; width: 4px; }}
QScrollBar::handle:vertical {{ background: #2a2a2a; border-radius: 2px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QLineEdit, QTextEdit {{
    background: transparent; color: {C_TEXT};
    border: none; border-top: 1px solid {C_BORDER};
    padding: 12px 0; font-size: {FONT_SZ}px;
    selection-background-color: #2a3a5a;
}}
QLineEdit:focus, QTextEdit:focus {{ border-top: 1px solid #2a2a2a; }}
"""

# ── emitters ──────────────────────────────────────────────────────────────────

class Emitter(QObject):
    token  = pyqtSignal(str)   # accumulated full text
    done   = pyqtSignal()
    error  = pyqtSignal(str)


# ── nav rail ──────────────────────────────────────────────────────────────────

class NavBtn(QPushButton):
    def __init__(self, label, parent=None):
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setFixedHeight(40)
        self._update()
        self.toggled.connect(lambda _: self._update())

    def _update(self):
        color = C_ACCENT if self.isChecked() else C_DIM
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {color};
                border: none; border-left: 2px solid {'transparent' if not self.isChecked() else C_ACCENT};
                padding: 0 18px; font-size: 12px; font-weight: 600;
                text-align: left;
            }}
        """)


class NavRail(QFrame):
    tab_changed = pyqtSignal(int)   # 0=chat, 1=agents

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(90)
        self.setStyleSheet(f"background: {BG_NAV}; border-right: 1px solid {C_BORDER};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.setSpacing(4)

        self._btns = []
        for i, label in enumerate(["Chat", "Agents"]):
            b = NavBtn(label)
            b.clicked.connect(lambda _, idx=i: self._select(idx))
            layout.addWidget(b)
            self._btns.append(b)
        layout.addStretch()
        self._select(0)

    def _select(self, idx):
        for i, b in enumerate(self._btns):
            b.setChecked(i == idx)
        self.tab_changed.emit(idx)


# ── chat view ─────────────────────────────────────────────────────────────────

class MsgRow(QFrame):
    """One message — no box, just text. User = right-dim, AI = left-bright."""
    def __init__(self, role: str, text: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)

        self._lbl = QLabel()
        self._lbl.setWordWrap(True)
        self._lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        if role == "user":
            self._lbl.setStyleSheet(f"color: {C_USER}; font-size: {FONT_SZ}px; background: transparent;")
            self._lbl.setAlignment(Qt.AlignRight)
        else:
            self._lbl.setStyleSheet(f"color: {C_AI}; font-size: {FONT_SZ}px; background: transparent;")
            self._lbl.setAlignment(Qt.AlignLeft)

        layout.addWidget(self._lbl)
        self.set_text(text)

    def set_text(self, t: str):
        self._lbl.setText(t if t else "▋")

    def finalize(self, t: str):
        self._lbl.setText(t)


class SessionLabel(QFrame):
    clicked = pyqtSignal(str)
    def __init__(self, sid, title, parent=None):
        super().__init__(parent)
        self._sid = sid
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        lbl = QLabel(title[:40])
        lbl.setStyleSheet(f"color: {C_DIM}; font-size: 11px; background: transparent;")
        layout.addWidget(lbl)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, e):
        self.clicked.emit(self._sid)


class ChatView(QWidget):
    send_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[MsgRow] = []
        self._streaming_row: MsgRow | None = None
        self._current_sid: str | None = None
        self._emitter = Emitter()
        self._emitter.token.connect(self._on_token, Qt.QueuedConnection)
        self._emitter.done.connect(self._on_done, Qt.QueuedConnection)
        self._emitter.error.connect(self._on_error, Qt.QueuedConnection)
        self._full = ""
        self._setup()

    def _setup(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 0, 48, 0)
        layout.setSpacing(0)

        # session history strip (top, collapsible)
        self._sess_strip = QFrame()
        self._sess_strip.setStyleSheet("background: transparent;")
        sess_layout = QHBoxLayout(self._sess_strip)
        sess_layout.setContentsMargins(0, 8, 0, 8)
        sess_layout.setSpacing(16)
        self._sess_layout = sess_layout
        layout.addWidget(self._sess_strip)

        # divider
        div = QFrame(); div.setFrameShape(QFrame.HLine)
        div.setStyleSheet(f"color: {C_BORDER};"); layout.addWidget(div)

        # scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._msg_layout = QVBoxLayout(self._container)
        self._msg_layout.setContentsMargins(0, 16, 0, 16)
        self._msg_layout.setSpacing(12)
        self._msg_layout.addStretch()
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

        # input
        self._input = QTextEdit()
        self._input.setPlaceholderText("Message…  (Enter to send, Shift+Enter newline)")
        self._input.setFixedHeight(52)
        self._input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._input.installEventFilter(self)
        layout.addWidget(self._input)

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
        row = MsgRow("user", text)
        self._add_row(row)
        ai_row = MsgRow("assistant", "")
        self._add_row(ai_row)
        self._streaming_row = ai_row
        self._full = ""
        self.send_requested.emit(text)

    def _add_row(self, row: MsgRow):
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, row)
        self._rows.append(row)
        QTimer.singleShot(10, self._scroll_bottom)

    def _on_token(self, full: str):
        self._full = full
        if self._streaming_row:
            self._streaming_row.set_text(full)
            self._scroll_bottom()

    def _on_done(self):
        if self._streaming_row:
            self._streaming_row.finalize(self._full)
            self._streaming_row = None
        self._refresh_sessions()

    def _on_error(self, msg: str):
        if self._streaming_row:
            self._streaming_row.finalize(f"[error: {msg}]")
            self._streaming_row = None

    def load_session(self, sid: str):
        self._current_sid = sid
        for r in self._rows:
            r.deleteLater()
        self._rows.clear()
        self._streaming_row = None
        data = sess_mgr.read_session(sid)
        if data:
            for m in data.get("messages", []):
                if m["role"] in ("user", "assistant"):
                    r = MsgRow(m["role"], m["content"])
                    self._msg_layout.insertWidget(self._msg_layout.count() - 1, r)
                    self._rows.append(r)
        QTimer.singleShot(50, self._scroll_bottom)

    def new_session(self):
        self._current_sid = None
        for r in self._rows:
            r.deleteLater()
        self._rows.clear()
        self._streaming_row = None

    def _refresh_sessions(self):
        while self._sess_layout.count():
            item = self._sess_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for s in sess_mgr.list_sessions(6):
            lbl = SessionLabel(s["id"], s["title"])
            lbl.clicked.connect(self._on_sess_click)
            self._sess_layout.addWidget(lbl)
        self._sess_layout.addStretch()

    def _on_sess_click(self, sid: str):
        import agent as agent_mod
        with agent_mod._history_lock:
            sdata = sess_mgr.read_session(sid)
            if sdata:
                agent_mod._current_session = sdata
                agent_mod._history = list(sdata.get("messages", []))
                sess_mgr._set_current(sid)
        self.load_session(sid)

    def _scroll_bottom(self):
        self._scroll.verticalScrollBar().setValue(self._scroll.verticalScrollBar().maximum())

    def do_send(self, text: str):
        """Called by parent after routing through agent."""
        pass  # send_requested signal handles it


# ── agents view ───────────────────────────────────────────────────────────────

STATUS_COLOR = {
    "running":   C_ACCENT,
    "completed": C_GREEN,
    "failed":    C_RED,
    "cancelled": C_DIM,
    "unknown":   C_DIM,
}


class AgentRow(QFrame):
    clicked = pyqtSignal(str)   # agent_id

    def __init__(self, meta: dict, parent=None):
        super().__init__(parent)
        self._aid = meta["agent_id"]
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(12)

        # status dot
        dot = QLabel("●")
        color = STATUS_COLOR.get(meta.get("status", "unknown"), C_DIM)
        dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
        dot.setFixedWidth(14)
        layout.addWidget(dot)

        # name + prompt
        col = QVBoxLayout(); col.setSpacing(2)
        name_lbl = QLabel(meta.get("name", meta["agent_id"]))
        name_lbl.setStyleSheet(f"color: {C_TEXT}; font-size: 13px; font-weight: 600; background: transparent;")
        prompt_lbl = QLabel(meta.get("prompt_preview", "")[:80])
        prompt_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 11px; background: transparent;")
        col.addWidget(name_lbl)
        col.addWidget(prompt_lbl)
        layout.addLayout(col, 1)

        # time
        if meta.get("started"):
            t = time.strftime("%H:%M", time.localtime(meta["started"]))
            tl = QLabel(t)
            tl.setStyleSheet(f"color: {C_DIM}; font-size: 10px; background: transparent;")
            layout.addWidget(tl)

    def mousePressEvent(self, e):
        self.clicked.emit(self._aid)


class AgentDetail(QWidget):
    back_requested = pyqtSignal()
    send_requested = pyqtSignal(str, str)   # agent_id, text

    def __init__(self, parent=None):
        super().__init__(parent)
        self._aid: str | None = None
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(1500)
        self._poll_timer.timeout.connect(self._poll)
        self._setup()

    def _setup(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 0, 48, 0)
        layout.setSpacing(0)

        # top bar
        top = QHBoxLayout()
        back = QPushButton("← Agents")
        back.setStyleSheet(f"background: transparent; color: {C_ACCENT}; border: none; font-size: 12px; padding: 8px 0;")
        back.clicked.connect(self._on_back)
        self._title = QLabel("")
        self._title.setStyleSheet(f"color: {C_TEXT}; font-size: 13px; font-weight: 600; background: transparent;")
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {C_DIM}; background: transparent;")
        top.addWidget(back)
        top.addStretch()
        top.addWidget(self._status_dot)
        top.addWidget(self._title)
        layout.addLayout(top)

        div = QFrame(); div.setFrameShape(QFrame.HLine)
        div.setStyleSheet(f"color: {C_BORDER};"); layout.addWidget(div)

        # output scroll
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._out_container = QWidget()
        self._out_container.setStyleSheet("background: transparent;")
        out_layout = QVBoxLayout(self._out_container)
        out_layout.setContentsMargins(0, 16, 0, 16)
        out_layout.setSpacing(12)

        self._chat_rows: list[MsgRow] = []
        self._out_layout = out_layout
        out_layout.addStretch()

        self._scroll.setWidget(self._out_container)
        layout.addWidget(self._scroll, 1)

        # input
        self._input = QTextEdit()
        self._input.setPlaceholderText("Send a follow-up to this agent… (Enter to send)")
        self._input.setFixedHeight(52)
        self._input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._input.installEventFilter(self)
        layout.addWidget(self._input)

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if obj is self._input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
                self._send_followup()
                return True
        return super().eventFilter(obj, event)

    def _send_followup(self):
        text = self._input.toPlainText().strip()
        if not text or not self._aid:
            return
        self._input.clear()
        # show user msg
        r = MsgRow("user", text)
        self._out_layout.insertWidget(self._out_layout.count() - 1, r)
        self._chat_rows.append(r)
        self.send_requested.emit(self._aid, text)

    def load(self, agent_id: str):
        self._aid = agent_id
        meta = subagent._read_meta(agent_id) or {}
        self._title.setText(meta.get("name", agent_id))
        status = meta.get("status", "unknown")
        self._status_dot.setStyleSheet(f"color: {STATUS_COLOR.get(status, C_DIM)}; background: transparent;")
        # clear rows
        for r in self._chat_rows:
            r.deleteLater()
        self._chat_rows.clear()

        # load worker session history
        hist = self._load_history(agent_id)
        for m in hist:
            if m["role"] in ("user", "assistant"):
                r = MsgRow(m["role"], m["content"])
                self._out_layout.insertWidget(self._out_layout.count() - 1, r)
                self._chat_rows.append(r)

        # if still running, show live output as streaming row
        if status == "running":
            out = meta.get("output", "")
            if out:
                r = MsgRow("assistant", out)
                self._out_layout.insertWidget(self._out_layout.count() - 1, r)
                self._chat_rows.append(r)
            self._poll_timer.start()
        else:
            self._poll_timer.stop()

        QTimer.singleShot(50, self._scroll_bottom)

    def _load_history(self, agent_id: str) -> list[dict]:
        p = Path.home() / ".voice-spotlight" / "agents" / f"{agent_id}.session.json"
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text())
        except Exception:
            return []

    def _poll(self):
        if not self._aid:
            return
        meta = subagent._read_meta(self._aid) or {}
        status = meta.get("status", "unknown")
        self._status_dot.setStyleSheet(f"color: {STATUS_COLOR.get(status, C_DIM)}; background: transparent;")
        if status != "running":
            self._poll_timer.stop()
            return
        out = meta.get("output", "")
        if self._chat_rows and self._chat_rows[-1].role == "assistant":
            self._chat_rows[-1].set_text(out)
        elif out:
            r = MsgRow("assistant", out)
            self._out_layout.insertWidget(self._out_layout.count() - 1, r)
            self._chat_rows.append(r)
        self._scroll_bottom()

    def add_assistant_row(self, text: str) -> MsgRow:
        r = MsgRow("assistant", text)
        self._out_layout.insertWidget(self._out_layout.count() - 1, r)
        self._chat_rows.append(r)
        self._scroll_bottom()
        return r

    def _on_back(self):
        self._poll_timer.stop()
        self.back_requested.emit()

    def _scroll_bottom(self):
        self._scroll.verticalScrollBar().setValue(self._scroll.verticalScrollBar().maximum())


class AgentsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._emitter = Emitter()
        self._emitter.token.connect(self._on_token, Qt.QueuedConnection)
        self._emitter.done.connect(self._on_done, Qt.QueuedConnection)
        self._emitter.error.connect(self._on_error, Qt.QueuedConnection)
        self._streaming_row: MsgRow | None = None
        self._current_aid: str | None = None
        self._full = ""
        self._setup()

        self._refresh_timer = QTimer()
        self._refresh_timer.setInterval(3000)
        self._refresh_timer.timeout.connect(self._refresh_list)
        self._refresh_timer.start()

    def _setup(self):
        self._stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack)

        # ── page 0: list ──────────────────────────────────────────────────────
        list_page = QWidget()
        list_page.setStyleSheet("background: transparent;")
        lp_layout = QVBoxLayout(list_page)
        lp_layout.setContentsMargins(48, 16, 48, 16)
        lp_layout.setSpacing(0)

        hdr = QLabel("Agents")
        hdr.setStyleSheet(f"color: {C_TEXT}; font-size: 16px; font-weight: 700; background: transparent; padding-bottom: 12px;")
        lp_layout.addWidget(hdr)

        div = QFrame(); div.setFrameShape(QFrame.HLine)
        div.setStyleSheet(f"color: {C_BORDER};"); lp_layout.addWidget(div)

        # scroll for agent rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._list_container = QWidget()
        self._list_container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 8, 0, 8)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_container)
        lp_layout.addWidget(scroll, 1)

        # bottom: new agent input
        new_div = QFrame(); new_div.setFrameShape(QFrame.HLine)
        new_div.setStyleSheet(f"color: {C_BORDER};"); lp_layout.addWidget(new_div)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 8, 0, 0)
        self._new_name = QLineEdit()
        self._new_name.setPlaceholderText("agent-name")
        self._new_name.setFixedWidth(120)
        self._new_name.setStyleSheet(f"""
            QLineEdit {{ background: transparent; color: {C_TEXT}; border: 1px solid {C_BORDER};
                border-radius: 6px; padding: 6px 10px; font-size: 12px; }}
            QLineEdit:focus {{ border: 1px solid #2a2a2a; }}
        """)
        self._new_prompt = QLineEdit()
        self._new_prompt.setPlaceholderText("Task description…")
        self._new_prompt.setStyleSheet(f"""
            QLineEdit {{ background: transparent; color: {C_TEXT}; border: 1px solid {C_BORDER};
                border-radius: 6px; padding: 6px 10px; font-size: 12px; }}
            QLineEdit:focus {{ border: 1px solid #2a2a2a; }}
        """)
        self._new_prompt.returnPressed.connect(self._launch_agent)
        start_btn = QPushButton("Start")
        start_btn.setStyleSheet(f"""
            QPushButton {{ background: {C_ACCENT}; color: white; border: none;
                border-radius: 6px; padding: 6px 16px; font-size: 12px; font-weight: 600; }}
            QPushButton:hover {{ background: #5aaeff; }}
        """)
        start_btn.clicked.connect(self._launch_agent)
        bottom.addWidget(self._new_name)
        bottom.addWidget(self._new_prompt, 1)
        bottom.addWidget(start_btn)
        lp_layout.addLayout(bottom)

        self._stack.addWidget(list_page)

        # ── page 1: detail ────────────────────────────────────────────────────
        self._detail = AgentDetail()
        self._detail.back_requested.connect(lambda: self._stack.setCurrentIndex(0))
        self._detail.send_requested.connect(self._send_to_agent)
        self._stack.addWidget(self._detail)

        self._refresh_list()

    def _refresh_list(self):
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            agents = subagent.list_all()
        except Exception:
            agents = []
        if not agents:
            empty = QLabel("No agents yet. Start one below.")
            empty.setStyleSheet(f"color: {C_DIM}; font-size: 12px; background: transparent; padding: 20px 0;")
            self._list_layout.insertWidget(0, empty)
            return
        for meta in agents:
            row = AgentRow(meta)
            row.clicked.connect(self._open_agent)
            div = QFrame(); div.setFrameShape(QFrame.HLine)
            div.setStyleSheet(f"color: {C_BORDER};")
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._list_layout.insertWidget(self._list_layout.count() - 1, div)

    def _open_agent(self, aid: str):
        self._current_aid = aid
        self._detail.load(aid)
        self._stack.setCurrentIndex(1)

    def _launch_agent(self):
        name = self._new_name.text().strip() or "agent"
        prompt = self._new_prompt.text().strip()
        if not prompt:
            return
        self._new_name.clear()
        self._new_prompt.clear()
        aid = subagent.launch(name, prompt)
        self._refresh_list()

    def _send_to_agent(self, aid: str, text: str):
        """Send a follow-up message to a running/completed agent via a new worker run."""
        self._streaming_row = self._detail.add_assistant_row("▋")
        self._full = ""
        self._current_aid = aid

        def _go():
            try:
                import agent as agent_mod, asyncio
                history = self._detail._load_history(aid)
                history.append({"role": "user", "content": text})
                if agent_mod._gpu_reachable():
                    asyncio.run(agent_mod._run_stream(
                        text,
                        on_token=lambda t: self._emitter.token.emit(t),
                        cancel_event=None,
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
        self._full = full
        if self._streaming_row:
            self._streaming_row.set_text(full)

    def _on_done(self):
        if self._streaming_row:
            self._streaming_row.finalize(self._full)
            self._streaming_row = None

    def _on_error(self, msg: str):
        if self._streaming_row:
            self._streaming_row.finalize(f"[error: {msg}]")
            self._streaming_row = None

    def show_page(self):
        self._stack.setCurrentIndex(0)
        self._refresh_list()


# ── main window ───────────────────────────────────────────────────────────────

class ChatWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._current_sid: str | None = None
        self._streaming_full = ""
        self._emitter = Emitter()
        self._emitter.token.connect(self._on_token, Qt.QueuedConnection)
        self._emitter.done.connect(self._on_done, Qt.QueuedConnection)
        self._emitter.error.connect(self._on_error, Qt.QueuedConnection)
        self._setup()
        self._init_session()

    def _setup(self):
        self.setWindowTitle("Voice Spotlight")
        self.resize(860, 640)
        self.setMinimumSize(560, 400)
        self.setStyleSheet(SS)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._nav = NavRail()
        self._nav.tab_changed.connect(self._on_tab)
        root.addWidget(self._nav)

        self._pages = QStackedWidget()
        self._pages.setStyleSheet("background: transparent;")
        root.addWidget(self._pages, 1)

        self._chat_view = ChatView()
        self._chat_view.send_requested.connect(self._send)
        self._pages.addWidget(self._chat_view)   # index 0

        self._agents_view = AgentsView()
        self._pages.addWidget(self._agents_view)  # index 1

    def _on_tab(self, idx: int):
        self._pages.setCurrentIndex(idx)
        if idx == 1:
            self._agents_view.show_page()

    def _init_session(self):
        data = sess_mgr.load_current()
        self._current_sid = data["id"]
        if data.get("messages"):
            self._chat_view.load_session(self._current_sid)
        self._chat_view._refresh_sessions()

    def _send(self, text: str):
        self._streaming_full = ""

        def _go():
            try:
                import agent as agent_mod, asyncio
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
        self._chat_view._on_token(full)

    def _on_done(self):
        self._chat_view._on_done()

    def _on_error(self, msg: str):
        self._chat_view._on_error(msg)


# ── launch helpers ────────────────────────────────────────────────────────────

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
