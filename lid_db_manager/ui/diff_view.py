"""The side panel: what the selected mod is, and row-by-row what it will change."""

from __future__ import annotations

import html

from PySide6.QtWidgets import QLabel, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from ..manager import Manager
from ..mod import Mod
from .theme import colors

MAX_TEXT_CHARS = 400


class DiffView(QWidget):
    """Details / Diff / Readme for one mod."""

    def __init__(self, manager: Manager, dark: bool = True, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.dark = dark
        self.mod_id = ""

        self.title = QLabel("Select a mod")
        self.title.setWordWrap(True)
        font = self.title.font()
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        self.title.setFont(font)

        self.tabs = QTabWidget()
        self.details = QTextBrowser()
        self.diff = QTextBrowser()
        self.readme = QTextBrowser()
        for browser in (self.details, self.diff, self.readme):
            browser.setOpenExternalLinks(True)
        self.tabs.addTab(self.details, "Details")
        self.tabs.addTab(self.diff, "Diff preview")
        self.tabs.addTab(self.readme, "Readme")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title)
        layout.addWidget(self.tabs)

        self.tabs.currentChanged.connect(self._on_tab_changed)

    # -- public ------------------------------------------------------------

    def show_mod(self, mod_id: str) -> None:
        self.mod_id = mod_id
        mod = self.manager.scan.get(mod_id)
        if mod is None:
            self.title.setText("Select a mod")
            for browser in (self.details, self.diff, self.readme):
                browser.setHtml("")
            return
        self.title.setText(mod.name)
        self.details.setHtml(self._details_html(mod))
        self.readme.setHtml(self._readme_html(mod))
        self.diff.setHtml("<p><i>Loading preview...</i></p>")
        if self.tabs.currentWidget() is self.diff:
            self._load_diff(mod)

    def refresh(self) -> None:
        if self.mod_id:
            self.show_mod(self.mod_id)

    # -- rendering ---------------------------------------------------------

    def _style(self) -> str:
        palette = colors(self.dark)
        return (
            f"<style>"
            f"body {{ color: {palette['text']}; }}"
            f"h3 {{ margin: 10px 0 4px 0; font-size: 13px; }}"
            f"table {{ border-collapse: collapse; width: 100%; }}"
            f"td, th {{ padding: 3px 6px; text-align: left; vertical-align: top;"
            f" border-bottom: 1px solid {palette['border']}; }}"
            f"th {{ color: {palette['dim']}; font-weight: normal; }}"
            f".dim {{ color: {palette['dim']}; }}"
            f".before {{ color: {palette['failed']}; font-family: Consolas, monospace; }}"
            f".after {{ color: {palette['ok']}; font-family: Consolas, monospace; }}"
            f".warn {{ color: {palette['pending']}; }}"
            f".fail {{ color: {palette['failed']}; }}"
            f"code {{ font-family: Consolas, monospace; }}"
            f"</style>"
        )

    def _details_html(self, mod: Mod) -> str:
        escape = html.escape
        rows = [
            ("Id", escape(mod.id)),
            ("Version", escape(mod.version)),
            ("Author", escape(mod.author)),
            ("Source", escape(mod.source)),
            ("Affects", escape(mod.affects_label())),
        ]
        if mod.homepage:
            rows.append(("Homepage", f'<a href="{escape(mod.homepage)}">{escape(mod.homepage)}</a>'))
        if mod.requires:
            rows.append(("Requires", escape(", ".join(mod.requires))))
        applied = self.manager.state.applied.get(mod.id)
        if applied:
            rows.append(
                ("Last applied", f"{escape(applied.applied_at)} - {applied.rows_changed} row(s)")
            )
        rows.append(("Revert", "inverse.sql" if mod.has_inverse_sql else "pre-apply snapshot"))

        parts = [self._style(), f"<p>{escape(mod.description)}</p>", "<table>"]
        parts += [f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in rows]
        parts.append("</table>")

        parts.append("<h3>Patches</h3><table>")
        for index, patch in enumerate(mod.patches, start=1):
            parts.append(
                f"<tr><th>{index}. {escape(patch.type)}</th>"
                f"<td><code>{escape(patch.summary())}</code></td></tr>"
            )
        parts.append("</table>")

        problems = self.manager.conflicts().for_mod(mod.id)
        validation = self.manager.last_validation
        if validation is not None:
            result = validation.for_mod(mod.id)
            if result is not None:
                parts.append("<h3>Validation</h3>")
                for message in result.errors:
                    parts.append(f'<p class="fail">FAILED: {escape(message)}</p>')
                for message in result.warnings:
                    parts.append(f'<p class="warn">{escape(message)}</p>')
                if not result.errors and not result.warnings:
                    parts.append('<p class="dim">No problems found.</p>')
        if problems:
            parts.append("<h3>Conflicts</h3>")
            parts += [f'<p class="warn">{escape(message)}</p>' for message in problems]
        return "".join(parts)

    def _readme_html(self, mod: Mod) -> str:
        readme = mod.readme
        if readme is None:
            return self._style() + '<p class="dim">This mod has no readme.md.</p>'
        try:
            text = readme.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return self._style() + f'<p class="fail">Could not read {html.escape(str(exc))}</p>'
        return self._style() + f"<pre>{html.escape(text)}</pre>"

    def _load_diff(self, mod: Mod) -> None:
        if not self.manager.db_path or not self.manager.db_path.is_file():
            self.diff.setHtml(self._style() + '<p class="warn">No database selected.</p>')
            return
        try:
            previews = self.manager.previews([mod]).get(mod.id, [])
        except Exception as exc:  # a broken mod must not take the panel down
            self.diff.setHtml(
                self._style() + f'<p class="fail">Preview failed: {html.escape(str(exc))}</p>'
            )
            return

        escape = html.escape
        parts = [self._style()]
        for preview in previews:
            parts.append(f"<h3>{escape(preview.patch_summary)}</h3>")
            parts.append(f'<p class="dim">{preview.total_rows} row(s) in {escape(preview.table)}</p>')
            if preview.rows:
                parts.append("<table><tr><th>Row</th><th>Before</th><th>After</th></tr>")
                for row in preview.rows:
                    parts.append(
                        f"<tr><td>{escape(row.key)}</td>"
                        f'<td class="before">{escape(_clip(row.before))}</td>'
                        f'<td class="after">{escape(_clip(row.after))}</td></tr>'
                    )
                parts.append("</table>")
            if preview.note:
                parts.append(f'<p class="dim"><pre>{escape(preview.note)}</pre></p>')
        if not previews:
            parts.append('<p class="dim">Nothing to preview.</p>')
        self.diff.setHtml("".join(parts))

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is not self.diff:
            return
        mod = self.manager.scan.get(self.mod_id)
        if mod is not None:
            self.diff.setHtml(self._style() + "<p><i>Loading preview...</i></p>")
            # Let the "loading" line paint before the (possibly slow) query runs.
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, lambda: self._load_diff(mod))


def _clip(text: str) -> str:
    single_line = text.replace("\n", " / ")
    if len(single_line) <= MAX_TEXT_CHARS:
        return single_line
    return single_line[:MAX_TEXT_CHARS] + " ..."
