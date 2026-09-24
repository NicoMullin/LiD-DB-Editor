"""Dark and light palettes.

Fusion is used in both modes so the two themes differ only by colour, and the
status colours are defined once here so the mod list, the diff panel and the log
all agree on what "applied" and "failed" look like.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..textsize import DEFAULT_SCALE, clamp_scale

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


# Everything the window sizes itself against, at 100%. One number so that
# "bigger text" is one multiplication rather than a hunt through the stylesheet.
BASE_FONT_PX = 13
PANEL_TITLE_EXTRA_PX = 2  # the heading over the details panel


def scaled(pixels: int, scale: int = DEFAULT_SCALE) -> int:
    """``pixels`` at this text scale, never rounding away to nothing."""
    return max(1, int(round(pixels * clamp_scale(scale) / 100)))


def font_px(scale: int = DEFAULT_SCALE) -> int:
    return scaled(BASE_FONT_PX, scale)


def stylesheet(dark: bool, scale: int = DEFAULT_SCALE) -> str:
    """The whole style sheet at this colour scheme and text size.

    Apart from ``apply_theme`` this is the only thing that knows what the window
    looks like, and it touches nothing - which is why it is separate. Handing a
    new sheet to the application re-styles and re-lays-out every live widget, so
    it is the expensive half and not something to call to find out what a size
    works out to.
    """
    palette_colors = colors(dark)
    scale = clamp_scale(scale)
    font = font_px(scale)
    # Padding grows with the text, or larger type sits cramped in boxes sized
    # for the old font and rows start clipping their own descenders.
    pad = lambda pixels: scaled(pixels, scale)  # noqa: E731 - reads better inline
    return (
        f"""
        QWidget {{ font-size: {font}px; }}
        QTreeWidget, QPlainTextEdit, QTextBrowser, QListWidget {{
            border: 1px solid {palette_colors['border']};
            border-radius: 4px;
        }}
        QTreeWidget::item {{ padding: {pad(3)}px {pad(2)}px; }}
        QHeaderView::section {{
            background: {palette_colors['button']};
            border: 0px;
            border-bottom: 1px solid {palette_colors['border']};
            padding: {pad(4)}px {pad(6)}px;
        }}
        QPushButton {{
            padding: {pad(5)}px {pad(12)}px;
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
        QLabel#settingName {{ font-weight: 600; }}
        QLabel#panelTitle {{
            font-size: {font_px(scale) + scaled(PANEL_TITLE_EXTRA_PX, scale)}px;
            font-weight: 600;
        }}
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


def apply_theme(app: QApplication, dark: bool, scale: int = DEFAULT_SCALE) -> None:
    """Put the colours, the palette and the text size onto the application."""
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
    app.setStyleSheet(stylesheet(dark, scale))


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
