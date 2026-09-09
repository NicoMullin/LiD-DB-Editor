"""Dark and light palettes.

Fusion is used in both modes so the two themes differ only by colour, and the
status colours are defined once here so the mod list, the diff panel and the log
all agree on what "applied" and "failed" look like.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

DARK = {
    "window": "#1e1f22",
    "base": "#141517",
    "alt_base": "#26282c",
    "text": "#e4e6eb",
    "dim": "#9aa0a6",
    "button": "#2b2d31",
    "highlight": "#3b6ea5",
    "border": "#3a3d42",
    "ok": "#5fbf72",
    "pending": "#e0b341",
    "failed": "#e06c6c",
    "off": "#6b7078",
}

LIGHT = {
    "window": "#f3f3f3",
    "base": "#ffffff",
    "alt_base": "#f7f7f7",
    "text": "#1c1c1c",
    "dim": "#5f6368",
    "button": "#e8e8e8",
    "highlight": "#2f6fb5",
    "border": "#c8c8c8",
    "ok": "#1e8e3e",
    "pending": "#b07000",
    "failed": "#c5221f",
    "off": "#8a8f98",
}


def colors(dark: bool) -> dict[str, str]:
    return DARK if dark else LIGHT


def apply_theme(app: QApplication, dark: bool) -> None:
    palette_colors = colors(dark)
    app.setStyle("Fusion")

    palette = QPalette()
    window = QColor(palette_colors["window"])
    base = QColor(palette_colors["base"])
    text = QColor(palette_colors["text"])
    button = QColor(palette_colors["button"])
    highlight = QColor(palette_colors["highlight"])

    palette.setColor(QPalette.Window, window)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, base)
    palette.setColor(QPalette.AlternateBase, QColor(palette_colors["alt_base"]))
    palette.setColor(QPalette.ToolTipBase, base)
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, button)
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.BrightText, QColor(palette_colors["failed"]))
    palette.setColor(QPalette.Link, highlight)
    palette.setColor(QPalette.Highlight, highlight)
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.PlaceholderText, QColor(palette_colors["dim"]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(palette_colors["dim"]))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(palette_colors["dim"]))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(palette_colors["dim"]))
    app.setPalette(palette)

    app.setStyleSheet(
        f"""
        QWidget {{ font-size: 13px; }}
        QTreeWidget, QPlainTextEdit, QTextBrowser, QListWidget {{
            border: 1px solid {palette_colors['border']};
            border-radius: 4px;
        }}
        QTreeWidget::item {{ padding: 3px 2px; }}
        QHeaderView::section {{
            background: {palette_colors['button']};
            border: 0px;
            border-bottom: 1px solid {palette_colors['border']};
            padding: 4px 6px;
        }}
        QPushButton {{
            padding: 5px 12px;
            border: 1px solid {palette_colors['border']};
            border-radius: 4px;
            background: {palette_colors['button']};
        }}
        QPushButton:hover:!disabled {{ border-color: {palette_colors['highlight']}; }}
        QPushButton:disabled {{ color: {palette_colors['dim']}; }}
        QPushButton#primary {{
            background: {palette_colors['highlight']};
            border-color: {palette_colors['highlight']};
            color: #ffffff;
            font-weight: 600;
        }}
        QLabel#dim {{ color: {palette_colors['dim']}; }}
        QLabel#path {{ font-family: Consolas, 'Courier New', monospace; }}
        QGroupBox {{
            border: 1px solid {palette_colors['border']};
            border-radius: 4px;
            margin-top: 10px;
            padding-top: 8px;
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
        """
    )


def status_color(dark: bool, key: str) -> QColor:
    return QColor(colors(dark).get(key, colors(dark)["text"]))


# Text markers, so the list still reads correctly without colour.
STATUS_GLYPH = {
    "applied": "OK",
    "pending": "!",
    "failed": "X",
    "disabled": "-",
}

STATUS_TEXT = {
    "applied": "Applied",
    "pending": "Pending",
    "failed": "Failed",
    "disabled": "Off",
}

STATUS_KEY = {
    "applied": "ok",
    "pending": "pending",
    "failed": "failed",
    "disabled": "off",
}

ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter
