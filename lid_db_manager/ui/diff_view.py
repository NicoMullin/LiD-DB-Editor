"""The side panel: what the selected mod is, and row-by-row what it will change."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import explain
from ..conflict import SERIOUS
from ..manager import Manager
from ..mod import Mod
from .setting_editor import SettingEditor
from ..textsize import DEFAULT_SCALE, clamp_scale
from .theme import colors, font_px, scaled

MAX_TEXT_CHARS = 400


class DiffView(QWidget):
    """Details / Configuration / Diff / Readme for one mod."""

    # mod id, setting id, value - handed on from a timer, see _on_setting_committed
    settingChanged = Signal(str, str, object)

    def __init__(self, manager: Manager, dark: bool = True, parent=None,
                 text_scale: int = DEFAULT_SCALE):
        super().__init__(parent)
        self.manager = manager
        self.dark = dark
        # This panel writes its own HTML, so the stylesheet's font size does not
        # reach the headings inside it. It is told the scale instead.
        self.text_scale = clamp_scale(text_scale)
        self.mod_id = ""
        # The value boxes for the mod on show, keyed by setting id.
        self._editors: dict[str, SettingEditor] = {}
        self._config_mod_id = ""
        self._pending_settings: list[tuple[str, str, object]] = []

        self.title = QLabel("Select a mod")
        self.title.setWordWrap(True)
        # Sized in the stylesheet rather than by adding to the font here: a
        # point size set on the widget and a pixel size set in the stylesheet
        # fight, and the stylesheet wins - so the heading never grew.
        self.title.setObjectName("panelTitle")

        self.tabs = QTabWidget()
        self.details = QTextBrowser()
        self.plain = QTextBrowser()
        self.diff = QTextBrowser()
        self.readme = QTextBrowser()
        for browser in (self.details, self.plain, self.diff, self.readme):
            browser.setOpenExternalLinks(True)
        # A panel rather than rows under the mod in the list, because a mod
        # can have several values, and a list row is no place for a form.
        self.configuration = QScrollArea()
        self.configuration.setWidgetResizable(True)
        self.tabs.addTab(self.details, "Details")
        self.tabs.addTab(self.configuration, "Configuration")
        self.tabs.addTab(self.plain, "In plain English")
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
        mod = self.manager.configured_mod(mod_id)
        if mod is None:
            self.title.setText("Select a mod")
            for browser in (self.details, self.plain, self.diff, self.readme):
                browser.setHtml("")
            self._build_configuration(None)
            return
        self.title.setText(mod.name)
        self._build_configuration(mod)
        self.details.setHtml(self._details_html(mod))
        self.readme.setHtml(self._readme_html(mod))
        self.diff.setHtml("<p><i>Loading preview...</i></p>")
        self.plain.setHtml("<p><i>Working it out...</i></p>")
        if self.tabs.currentWidget() is self.diff:
            self._load_diff(mod)
        elif self.tabs.currentWidget() is self.plain:
            self._load_plain(mod)

    def refresh(self) -> None:
        if self.mod_id:
            self.show_mod(self.mod_id)

    # -- configuration -----------------------------------------------------

    def _editing(self) -> bool:
        return any(editor.spin.hasFocus() for editor in self._editors.values())

    def _build_configuration(self, mod: Mod | None) -> None:
        """A box for every value the mod on show lets the player choose."""
        mod_id = mod.id if mod is not None else ""
        if mod_id and mod_id == self._config_mod_id and self._editing():
            # Someone is typing in one of these boxes. Rebuilding would destroy
            # it under their cursor, and the window asks for a rebuild after
            # every toggle - so leave it until they are done.
            return
        # Silence the old boxes: one losing focus as it is destroyed reports
        # "editing finished", and that must not count as a choice.
        for editor in self._editors.values():
            editor.blockSignals(True)
            editor.spin.blockSignals(True)
        self._editors = {}
        self._config_mod_id = mod_id

        dim = colors(self.dark).get("dim", "")
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        def note(text: str) -> None:
            label = QLabel(text)
            label.setWordWrap(True)
            if dim:
                label.setStyleSheet(f"color: {dim};")
            layout.addWidget(label)

        if mod is None:
            note("Select a mod to see what you can change about it.")
        elif not mod.settings:
            note("This mod has nothing to configure - it has no values to choose.")
        else:
            note(
                "Changes take effect when you click <b>Save Mod List</b>. Switching the "
                "mod off still puts the stock values back, whatever these are set to."
            )
            for setting in mod.settings:
                editor = SettingEditor(
                    setting, mod.values.get(setting.id, setting.default), dim
                )
                editor.committed.connect(
                    lambda value, m=mod.id, s=setting.id: self._on_setting_committed(m, s, value)
                )
                layout.addWidget(editor)
                self._editors[setting.id] = editor
            if len(mod.settings) > 1:
                reset_all = QPushButton("Put all back to defaults")
                reset_all.clicked.connect(self._reset_all)
                layout.addWidget(reset_all, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

        previous = self.configuration.takeWidget()
        self.configuration.setWidget(body)
        if previous is not None:
            previous.deleteLater()

        # Only mods with something to choose get the tab at all. Tabs are found
        # by widget everywhere, never by position, so the others shifting along
        # when it is hidden changes nothing but where they are drawn.
        configurable = mod is not None and bool(mod.settings)
        if not configurable and self.tabs.currentWidget() is self.configuration:
            self.tabs.setCurrentWidget(self.details)
        self.tabs.setTabVisible(self.tabs.indexOf(self.configuration), configurable)

    def _reset_all(self) -> None:
        for editor in list(self._editors.values()):
            editor.spin.setValue(editor.setting.default)
            editor._commit()

    def _on_setting_committed(self, mod_id: str, setting_id: str, value) -> None:
        # Deferred, like the list's toggles: whoever handles this rebuilds the
        # panel, which would destroy the box that is still emitting.
        self._pending_settings.append((mod_id, setting_id, value))
        QTimer.singleShot(0, self._flush_settings)

    def _flush_settings(self) -> None:
        pending, self._pending_settings = self._pending_settings, []
        for mod_id, setting_id, value in pending:
            self.settingChanged.emit(mod_id, setting_id, value)

    def pending_setting_edits(self) -> list[tuple[str, str, object]]:
        """Values typed into a box but not yet handed on - taken as they stand.

        Asked before a save, so a number someone typed and then went straight to
        Save Mod List with is the number that gets applied.
        """
        found = []
        for setting_id, editor in self._editors.items():
            if editor.pending():
                editor.mark_committed()
                found.append((self._config_mod_id, setting_id, editor.value()))
        return found

    def show_configuration(self) -> bool:
        """Bring the Configuration tab forward, cursor in its first box."""
        self.tabs.setCurrentWidget(self.configuration)
        for editor in self._editors.values():
            editor.spin.setFocus()
            editor.spin.selectAll()
            return True
        return False

    def editor_for(self, setting_id: str) -> SettingEditor | None:
        return self._editors.get(setting_id)

    def set_text_scale(self, scale: int) -> None:
        """Take a new text size. The window refreshes the panel afterwards."""
        self.text_scale = clamp_scale(scale)

    # -- rendering ---------------------------------------------------------

    def _style(self) -> str:
        palette = colors(self.dark)
        return (
            f"<style>"
            f"body {{ color: {palette['text']}; }}"
            f"h3 {{ margin: {scaled(10, self.text_scale)}px 0 "
            f"{scaled(4, self.text_scale)}px 0; font-size: {font_px(self.text_scale)}px; }}"
            f"table {{ border-collapse: collapse; width: 100%; }}"
            f"td, th {{ padding: 3px 6px; text-align: left; vertical-align: top;"
            f" border-bottom: 1px solid {palette['border']}; }}"
            f"th {{ color: {palette['dim']}; font-weight: normal; }}"
            f".dim {{ color: {palette['dim']}; }}"
            f".before {{ color: {palette['failed']}; font-family: Consolas, monospace; }}"
            f".after {{ color: {palette['ok']}; font-family: Consolas, monospace; }}"
            f".warn {{ color: {palette['pending']}; }}"
            f".quote {{ color: {palette['dim']}; font-style: italic; }}"
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

        problems = self.manager.conflicts().messages_for(mod.id)
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
            parts += [
                f'<p class="{"fail" if severity == SERIOUS else "warn"}">'
                f"{escape(message)}</p>"
                for severity, message in problems
            ]
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

    def _load_plain(self, mod: Mod) -> None:
        """What this mod changes, said in words rather than rows."""
        escape = html.escape
        try:
            delta = self.manager.mod_delta(mod)
            explanation = explain.explain_delta(
                delta, db_path=self.manager.db_path, home=self.manager.paths.root
            )
        except Exception as exc:  # a display must never take the panel down
            self.plain.setHtml(
                self._style() + f'<p class="fail">Could not work it out: {escape(str(exc))}</p>'
            )
            return

        parts = [self._style()]
        if delta is None:
            parts.append(
                '<p class="warn">This needs an untouched copy of the database to compare '
                "against, and there is not one yet. Pick your database, or save your mod "
                "list once.</p>"
            )
        elif explanation.empty:
            parts.append('<p class="dim">This mod changes nothing at all.</p>')
        for table in explanation.tables:
            parts.append(f"<h3>{escape(table.title)}</h3>")
            if table.about:
                parts.append(f'<p class="dim">{escape(table.about)}</p>')
            parts.append("<ul>")
            for change in table.changes:
                parts.append(f"<li>{escape(change.sentence)}")
                if change.example:
                    parts.append(f'<br><span class="dim">for example, {escape(change.example)}</span>')
                if change.quotes:
                    parts.append('<br><span class="quote">')
                    parts.append("<br>".join(escape(q) for q in change.quotes))
                    parts.append("</span>")
                parts.append("</li>")
            parts.append("</ul>")
        if explanation.note:
            parts.append(f'<p class="dim">{escape(explanation.note)}</p>')
        parts.append(
            '<p class="dim">This describes what the mod does to the database. '
            "Some things the game draws as pictures - a price on a sign, for instance - "
            "do not follow the numbers.</p>"
        )
        self.plain.setHtml("".join(parts))

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
        widget = self.tabs.widget(index)
        if widget not in (self.diff, self.plain):
            return
        mod = self.manager.configured_mod(self.mod_id)
        if mod is None:
            return
        # Both of these read the database, so show something before they run.
        from PySide6.QtCore import QTimer

        if widget is self.diff:
            self.diff.setHtml(self._style() + "<p><i>Loading preview...</i></p>")
            QTimer.singleShot(0, lambda: self._load_diff(mod))
        else:
            self.plain.setHtml(self._style() + "<p><i>Working it out...</i></p>")
            QTimer.singleShot(0, lambda: self._load_plain(mod))


def _clip(text: str) -> str:
    single_line = text.replace("\n", " / ")
    if len(single_line) <= MAX_TEXT_CHARS:
        return single_line
    return single_line[:MAX_TEXT_CHARS] + " ..."
