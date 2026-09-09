"""The log pane at the bottom of the window.

The session log is written from whichever thread is applying, so lines arrive
here through a signal - that is what marshals them onto the GUI thread.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..session_log import LogLine, SessionLog
from .theme import colors

MAX_BLOCKS = 2000


class LogPanel(QWidget):
    _lineArrived = Signal(object)

    def __init__(self, log: SessionLog, dark: bool = True, parent=None):
        super().__init__(parent)
        self.log = log
        self.dark = dark

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_BLOCKS)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.follow = QCheckBox("Follow")
        self.follow.setChecked(True)
        self.warnings_only = QCheckBox("Warnings and errors only")
        clear = QPushButton("Clear view")
        clear.clicked.connect(self.view.clear)

        self.file_label = QLabel(log.path.name if log.path else "(not writing to a file)")
        self.file_label.setObjectName("dim")
        self.file_label.setToolTip(str(log.path) if log.path else "")

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Log"))
        controls.addWidget(self.file_label, 1)
        controls.addWidget(self.warnings_only)
        controls.addWidget(self.follow)
        controls.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(self.view)

        self._lineArrived.connect(self._append)
        for line in log.lines:
            self._append(line)
        log.add_listener(self._lineArrived.emit)

    def set_dark(self, dark: bool) -> None:
        self.dark = dark

    def _append(self, line: LogLine) -> None:
        if self.warnings_only.isChecked() and line.level == "INFO":
            return
        palette = colors(self.dark)
        color = {
            "WARN": palette["pending"],
            "ERROR": palette["failed"],
        }.get(line.level, palette["dim"] if line.level == "INFO" else palette["text"])
        text = line.format().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.view.appendHtml(f'<span style="color:{color}; white-space:pre">{text}</span>')
        if self.follow.isChecked():
            self.view.moveCursor(QTextCursor.MoveOperation.End)
