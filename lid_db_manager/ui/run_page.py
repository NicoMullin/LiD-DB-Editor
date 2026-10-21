"""The save editor's Run page (saverun.py and saveheaven.py do the work): the run a fighter is in.

In the Waiting Room there is nothing to change. In a run: End the run, or, for a
run that crashed on a normal Heaven floor, go back to its last boss floor. A
Heaven run can also move to another boss floor, lock the boss floors ahead, hide
blueprints in them ("I'm feeling lucky") and change what the Jackals drop.
Every random draw is kept as a seed, so what the page shows is what is written.
"""

from __future__ import annotations

import html
import random
import time
import uuid

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import saveedit, saveheaven, saverun
from .decal_pages import _fixed, _table

ROLL_HISTORY = 10


def _seed() -> int:
    return random.SystemRandom().randrange(1, 2 ** 31)


def _dim(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("dim")
    label.setWordWrap(True)
    return label


class RunPage(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", limits: "saveedit.Limits",
                 heaven: "saveheaven.Heaven", parent=None):
        super().__init__(parent)
        self.document, self.limits, self.heaven = document, limits, heaven
        self.floors = heaven
        self.info = saverun.run(document, limits)
        self.target = saverun.rewind_target(document, heaven)
        self.in_heaven = saveheaven.paused(document)
        # what is waiting to be written
        self.move_to, self.move_seed, self.move_past, self.reroll_rewards = "", 0, [], True
        self.lock_mode, self.lock_which, self.lock_extend, self.lock_rewards = "", "", True, "roll"
        self.lock_redraw, self.lock_seed, self.lock_past, self.clear_extra = False, _seed(), [], False
        self.lucky_seed, self.lucky_masks = 0, False
        self.jackal_edits: dict = {}                # type -> (Loot, eid, now)
        self._owned = None
        self._rng = random.Random()

        self.where = QLabel()
        self.where.setWordWrap(True)
        self.spoilers = QCheckBox("Show rewards (spoilers)")
        self.spoilers.setToolTip("Rolled rewards are only named while this is ticked")
        self.spoilers.toggled.connect(lambda _on: self._refresh())

        self.end = QCheckBox("End the run")
        self.end.toggled.connect(self._toggled)
        self.end_note = _dim()
        self.back = QCheckBox("Go back to the last boss floor")
        self.back.toggled.connect(self._toggled)
        self.back_note = _dim()
        leave = QGroupBox("Leave the run")
        box = QVBoxLayout(leave)
        for widget in (self.end, self.end_note, self.back, self.back_note):
            box.addWidget(widget)

        self.parts_off = _dim()
        self.move_box = self._move_box()
        self.lock_box = self._lock_box()
        self.lucky_box = self._lucky_box()
        self.jackal_box = self._jackal_box()

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.addWidget(self.where)
        layout.addWidget(self.spoilers)
        layout.addWidget(leave)
        layout.addWidget(self.parts_off)
        for widget in (self.move_box, self.lock_box, self.lucky_box, self.jackal_box):
            layout.addWidget(widget)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.leave_box = leave
        self._show()
        self._refresh()

    # -- building the sections --

    def _move_box(self) -> QGroupBox:
        group = QGroupBox("Move to another boss floor")
        self.move_note = _dim()
        self.move_pick = QComboBox()
        self.move_pick.currentIndexChanged.connect(self._move_picked)
        self.move_reroll = QCheckBox("Roll this floor's rewards again from the new floor's tables")
        self.move_reroll.setChecked(True)
        self.move_reroll.toggled.connect(self._move_reroll)
        self.move_again = QPushButton("Reroll again")
        self.move_again.clicked.connect(self._move_again)
        self.move_prev = QPushButton("Previous roll")
        self.move_prev.clicked.connect(self._move_previous)
        self.move_rolled = _dim()
        self.move_table = _table(("Reward", "Now", "After saving"))
        self.move_table.setMaximumHeight(220)
        buttons = QHBoxLayout()
        buttons.addWidget(self.move_again)
        buttons.addWidget(self.move_prev)
        buttons.addStretch(1)
        box = QVBoxLayout(group)
        box.addWidget(self.move_note)
        box.addWidget(self.move_pick)
        box.addWidget(self.move_reroll)
        box.addLayout(buttons)
        box.addWidget(self.move_rolled)
        box.addWidget(self.move_table)
        return group

    def _lock_box(self) -> QGroupBox:
        group = QGroupBox("Lock the boss floors ahead")
        self.lock_note = _dim("Makes the run's boss floors ahead all Dons, all mid-bosses or all Screamer Pits, for "
                              "the rest of the run (confirmed in game with oberlinx's editor).")
        self.lock_pick = QComboBox()
        self.lock_pick.addItem("Leave as rolled", "")
        for key, (_kinds, _back, label, _noun) in saveheaven.LOCK_MODES.items():
            self.lock_pick.addItem(label, key)
        self.lock_pick.currentIndexChanged.connect(self._lock_changed)
        self.lock_one = QComboBox()
        self.lock_one.currentIndexChanged.connect(self._lock_changed)
        self.lock_all = QCheckBox()
        self.lock_all.setChecked(True)
        self.lock_all.toggled.connect(self._lock_changed)
        self.lock_reward = QComboBox()
        for key, label in saveheaven.LOCK_REWARDS.items():
            self.lock_reward.addItem(label, key)
        self.lock_reward.currentIndexChanged.connect(self._lock_changed)
        self.lock_again = QPushButton("Reroll again")
        self.lock_again.setToolTip("Draw these boss floors again, the ones that already fit too")
        self.lock_again.clicked.connect(self._lock_again)
        self.lock_prev = QPushButton("Previous roll")
        self.lock_prev.clicked.connect(self._lock_previous)
        self.lock_clear = QCheckBox()
        self.lock_clear.toggled.connect(self._clear_toggled)
        self.lock_state = _dim()
        self.lock_table = _table(("Floor", "Now", "After saving", "Rewards"))
        self.lock_table.setMaximumHeight(260)
        picks = QHBoxLayout()
        picks.addWidget(self.lock_pick, 2)
        picks.addWidget(self.lock_one, 1)
        self.lock_reward_row = QWidget()
        rewards = QHBoxLayout(self.lock_reward_row)
        rewards.setContentsMargins(0, 0, 0, 0)
        rewards.addWidget(QLabel("Rewards on the floors it writes:"))
        rewards.addWidget(self.lock_reward, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.lock_again)
        buttons.addWidget(self.lock_prev)
        buttons.addStretch(1)
        box = QVBoxLayout(group)
        box.addWidget(self.lock_note)
        box.addLayout(picks)
        box.addWidget(self.lock_all)
        box.addWidget(self.lock_reward_row)
        box.addWidget(self.lock_clear)
        box.addLayout(buttons)
        box.addWidget(self.lock_state)
        box.addWidget(self.lock_table)
        return group

    def _lucky_box(self) -> QGroupBox:
        group = QGroupBox("I'm feeling lucky")
        self.lucky_note = _dim("Plans the rest of this run's boss floors and hides some blueprints you don't have "
                               "in their large boxes and treasure enemies, at most one a floor. You aren't told how "
                               "many or where; this page never shows them. Press again to roll it again. "
                               "(Confirmed in game with oberlinx's editor.)")
        self.lucky_go = QPushButton("I'm feeling lucky")
        self.lucky_go.clicked.connect(self._lucky_pressed)
        self.lucky_undo = QPushButton("Undo")
        self.lucky_undo.clicked.connect(self._lucky_undone)
        self.lucky_mask = QCheckBox("Include masks")
        self.lucky_mask.setToolTip("One of the blueprints it hides can be a mask you don't have. Masks don't drop on "
                                   "NEO floors in the normal game, so it goes in a box outside the usual loot.")
        self.lucky_mask.toggled.connect(self._lucky_masks)
        self.lucky_state = _dim()
        buttons = QHBoxLayout()
        buttons.addWidget(self.lucky_go)
        buttons.addWidget(self.lucky_undo)
        buttons.addWidget(self.lucky_mask)
        buttons.addStretch(1)
        box = QVBoxLayout(group)
        box.addWidget(self.lucky_note)
        box.addLayout(buttons)
        box.addWidget(self.lucky_state)
        return group

    def _jackal_box(self) -> QGroupBox:
        group = QGroupBox("Jackal loot")
        note = _dim("Each Jackal carries one reward. It holds only on the floor the run is on: the game rolls every "
                    "Jackal again when it builds a new floor, so defeat the Jackal before taking the stairs or the "
                    "elevator. Weapons and armor drop at +0 with full durability and ammo. Unrevealed blueprints "
                    "can be picked, but a roll never gives one (how often the game does isn't in its data). "
                    "(Confirmed in game with oberlinx's editor.)")
        self.jackal_table = _table(("Jackal", "Carries now", "Drops after saving", ""))
        self.jackal_table.setMinimumHeight(220)
        roll_all = QPushButton("Reroll all")
        roll_all.clicked.connect(self._jackals_rolled)
        undo_all = QPushButton("Undo all")
        undo_all.clicked.connect(self._jackals_undone)
        self.jackal_state = _dim()
        buttons = QHBoxLayout()
        buttons.addWidget(roll_all)
        buttons.addWidget(undo_all)
        buttons.addWidget(self.jackal_state, 1)
        box = QVBoxLayout(group)
        box.addWidget(note)
        box.addWidget(self.jackal_table)
        box.addLayout(buttons)
        return group

    # -- part 1: where the fighter is --

    def _show(self) -> None:
        info = self.info
        if info is None:
            self.where.setText("The fighter is in the Waiting Room, not in a run, so there is nothing to change "
                               "here. (A save quit in the middle of a run starts back where it was left; this page "
                               "can then end the run, move it to another boss floor, lock the boss floors ahead "
                               "and change what the Jackals drop.)")
            for widget in (self.spoilers, self.leave_box, self.parts_off, self.move_box, self.lock_box,
                           self.lucky_box, self.jackal_box):
                widget.setVisible(False)
            return
        where = self.heaven.full_label(info.flrid) if info.flrid in self.heaven.floors else self.heaven.label(info.flrid)
        crash = (" <b>The game closed without pausing</b> (" + html.escape(info.pause) + "): if the save crashes "
                 "when it loads, ending the run (or going back to the last boss floor) lets it load.") \
            if info.crashed else ""
        self.where.setText(f"<b>{html.escape(info.name)}</b> is in a run, on <b>{html.escape(where)}</b>.{crash}")
        lost = (f" <b>The Bank holds {info.cap:,} at its level, so {info.lost:,} are lost</b> (raise the Bank "
                "level on the Account tab first to keep them).") if info.lost else ""
        self.end_note.setText(
            f"Back to the Waiting Room, the way the game leaves a save when a run ends (confirmed in game). The "
            f"Death Bag stays; the {info.money:,} Kill Coins carried go to the Bank and the {info.bloodnium:,} "
            f"Bloodnium to the account.{lost} The run's floors ({info.floors}) and the {info.made:,} thing(s) it "
            "left on floors, in chests and on enemies are cleared. The run's Exploration Bonus is not paid.")
        target = self.target
        self.back.setVisible(target is not None)
        self.back_note.setVisible(target is not None)
        if target is not None:
            name = self.heaven.label(target.flrid)
            if target.arrival is None:
                self.back.setEnabled(False)
                self.back_note.setText(f"The run's last boss floor is {html.escape(name)}, but the editor does not "
                                       "know where the game puts a fighter on that map, so end the run instead.")
            else:
                self.back.setText(f"Go back to the last boss floor ({name})")
                self.back_note.setText(
                    f"Keeps the run (confirmed in game): the fighter is put back on {html.escape(name)}, paused the "
                    f"way the game pauses there, its boss beaten. The {target.dropped} floor(s) after it are "
                    "dropped and played again. The Death Bag, Kill Coins and Bloodnium stay. If the game still "
                    "crashes, end the run instead.")

    def _toggled(self, on: bool) -> None:
        other = self.back if self.sender() is self.end else self.end
        if on and other.isChecked():
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
        self._refresh()
        self.changed.emit()

    # -- what the run looks like after each step --

    def _leaving(self) -> bool:
        return self.end.isChecked() or self.back.isChecked()

    def _here(self) -> str:
        return str(self.document.doc["soul"].get("flrid") or "")

    def _from(self) -> str:
        return self.move_to or self._here()

    def _roll(self) -> "saveheaven.MoveRoll | None":
        if not self.move_to:
            return None
        return saveheaven.roll_move(self.document, self.heaven, self.move_to, self.move_seed)

    def _moved(self, roll) -> list:
        if roll is None:
            return saveheaven.plan(self.document)
        return saveheaven.moved_plan(self.document, self.heaven, self.move_to, roll, self.reroll_rewards)

    def _lock_rows(self, entries: list) -> list:
        if not self.lock_mode:
            return []
        return saveheaven.lock_plan(self.heaven, self._from(), entries, self.lock_mode, self.lock_which,
                                    self.lock_extend, self.lock_rewards, self.lock_redraw, self.lock_seed)

    def _owned_blueprints(self) -> set:
        if self._owned is None:
            self._owned = saveheaven.owned_blueprints(self.document)
        return self._owned

    def _name(self, itemid) -> str:
        return self.heaven.catalog.item_name(str(itemid or "")) if itemid else "-"

    def _refresh(self) -> None:
        if self.info is None:
            return
        leaving = self._leaving()
        heaven_parts = self.in_heaven and self._here() in self.heaven.floors
        self.parts_off.setVisible(leaving and self.in_heaven)
        self.parts_off.setText("Moving, locking boss floors, I'm feeling lucky and Jackal loot are off while the "
                               "run is set to " + ("end." if self.end.isChecked() else "go back to its last boss floor."))
        for widget in (self.move_box, self.lock_box, self.lucky_box):
            widget.setVisible(heaven_parts and not leaving)
        self.jackal_box.setVisible(bool(saveheaven.jackals(self.document)) and not leaving)
        self.spoilers.setVisible(heaven_parts and not leaving)
        if not heaven_parts or leaving:
            self._show_jackals()
            return
        roll = self._roll()
        self._show_move(roll)
        entries = self._moved(roll)
        rows = self._lock_rows(entries)
        self._show_lock(entries, rows)
        after = saveheaven.locked_plan(entries, rows, self.lock_extend) if rows else entries
        self._show_lucky(after)
        self._show_jackals()

    # -- floor move --

    def _show_move(self, roll) -> None:
        heaven, here = self.heaven, self._here()
        spoil = self.spoilers.isChecked()
        targets = saveheaven.move_targets(self.document, heaven)
        self.move_pick.blockSignals(True)
        self.move_pick.clear()
        self.move_pick.addItem(f"Stay on {heaven.label(here)}", "")
        for f in targets:
            lower = " (lower)" if heaven.no(f) < heaven.no(here) else ""
            pit = " - pit converted" if saveheaven.pit_swap(self.document, heaven, f) else ""
            self.move_pick.addItem(f"{heaven.full_label(f)}, boss Lv {heaven.levels.get(f, '?')}{lower}{pit}", f)
        self.move_pick.setCurrentIndex(max(0, self.move_pick.findData(self.move_to)))
        self.move_pick.blockSignals(False)
        ref = saveheaven.current_arena(self.document)
        if not heaven.boss(here):
            note = (f"Floor moves need a boss floor: the fighter is on {heaven.label(here)}, a normal floor, and "
                    "normal floors are built at random. Quit while standing on a boss floor (55F, 60F, 65F ...) to "
                    "move. Locking the boss floors ahead works from here.")
        elif not targets:
            note = "No other boss floor of this route uses this map" + (
                f" and can hold this floor's arena ({heaven.kind_label(ref)})." if ref else ".")
        else:
            note = (f"Only boss floors of {heaven.route_name(here)} that use the same map are listed, so the "
                    "spot the fighter stands on still fits. The boss waiting here gets the new floor's boss level, "
                    "and the planned boss floors move with you. Records (deepest floor, clear times) are kept. The "
                    "Exploration Bonus follows the floor the run ends on. (Confirmed in game with oberlinx's "
                    "editor.)")
            left_out = saveheaven.same_map_floors(self.document, heaven) - len(targets)
            if ref and left_out > 0:
                note += (f" This floor is a {heaven.kind_label(ref)} floor and it's already built, so the "
                         f"{left_out} floor(s) where that arena can't appear are left out")
                note += " (regular Heaven has no Dons below 100F)." if heaven.kind(ref)[0] == "boss" \
                    and heaven.route(here) == "main" else "."
            if heaven.kind(ref)[0] == "pit" and any(saveheaven.pit_swap(self.document, heaven, f) for f in targets):
                note += (" Regular Heaven uses Screamer Pits 1-5 only below 100F and 6-10 only from 100F, so across "
                         "that line the pit turns into its twin: same map and spawn points, the other floors' "
                         "enemy set (confirmed in game).")
        self.move_note.setText(note)
        self.move_pick.setVisible(bool(targets))
        on = roll is not None
        for widget in (self.move_reroll, self.move_again, self.move_prev, self.move_rolled, self.move_table):
            widget.setVisible(on)
        if not on:
            return
        self.move_again.setVisible(self.reroll_rewards and roll.count > 0)
        self.move_prev.setVisible(self.reroll_rewards and bool(self.move_past))
        self.move_prev.setEnabled(bool(self.move_past))
        upcoming = [heaven.label(u["flrid"]) for u in roll.upcoming]
        text = (f"After saving, the fighter is on <b>{html.escape(heaven.full_label(self.move_to))}</b>. The map's "
                f"planned boss floors move with you: {', '.join(upcoming) or 'none left on this route'}.")
        if roll.refix:
            text += " Re-picked for their new floor (their arena can't appear there): " + ", ".join(
                f"{heaven.label(f)} {heaven.kind_label(x['from'])} -> {heaven.kind_label(x['to'])}"
                for f, x in roll.refix.items()) + "."
        if not self.reroll_rewards:
            text += f" The rewards rolled for {heaven.label(self._here())} are kept."
        elif not roll.count:
            text += " There are no unopened rewards to roll again."
        elif not spoil:
            text += (f" {roll.count} reward(s) are rolled again from {heaven.label(self.move_to)}'s own tables; "
                     "tick Show rewards to see them.")
        self.move_rolled.setText(text)
        listed = self.reroll_rewards and spoil and roll.count > 0
        self.move_table.setVisible(listed)
        if not listed:
            return
        lines = []
        for n, box in enumerate(roll.boxes, 1):
            what = {"PTGENTP_TRZAKO": "Treasure enemy", "featured": "Legendary Chest (extra large)"}.get(
                box["gentype"], f"Treasure box {n}")
            lines.append((what, self._name(box["from"]), self._name(box["to"])))
        if roll.boss:
            lines.append(("Boss drop", self._name(roll.boss["from"]), self._name(roll.boss["to"])))
        if roll.featured:
            lines.append(("Legendary Chest (map)", self._name(roll.featured["from"]), self._name(roll.featured["to"])))
        locked = {r.flrid for r in self._lock_rows(self._moved(roll)) if r.entry is not None}
        for up in roll.upcoming:
            if up["flrid"] in locked:
                lines.append((f"Planned {heaven.label(up['flrid'])}", "", "replaced by the lock below"))
                continue
            got = self._rewards_text(up["featured"]["to"] if up["featured"] else "",
                                     [w for w in up["rwds"] if not w["placeholder"]], key="to")
            lines.append((f"Planned {heaven.label(up['flrid'])}" + (" (rare)" if up["rare"] else ""), "on the map", got))
        self._fill(self.move_table, lines)

    def _rewards_text(self, featured: str, rwds: list, key: str = "rwdid") -> str:
        spoil = self.spoilers.isChecked()
        parts = []
        if featured:
            parts.append("Legendary Chest" + (f": {self._name(featured)}" if spoil else ""))
        for label, test in (("treasure enemy", lambda g: g == "PTGENTP_TRZAKO"),
                            ("large box", lambda g: g == "PTGENTP_TRBOX_L"),
                            ("boss drop", lambda g: str(g).startswith("PTGENTP_MBOSS"))):
            names = [self._name(w.get(key)) for w in rwds if test(w.get("gentype"))]
            if names:
                parts.append(f"{len(names)} {label}(s)" + (f": {', '.join(names)}" if spoil else ""))
        return "; ".join(parts) or "-"

    @staticmethod
    def _fill(table, lines: list) -> None:
        table.setRowCount(len(lines))
        for row, cells in enumerate(lines):
            for column, text in enumerate(cells):
                table.setItem(row, column, _fixed(str(text)))

    def _move_picked(self, _index: int) -> None:
        self.move_to = self.move_pick.currentData() or ""
        self.move_seed, self.move_past = (_seed() if self.move_to else 0), []
        self._changed()

    def _move_reroll(self, on: bool) -> None:
        self.reroll_rewards = on
        self._changed()

    def _move_again(self) -> None:
        self.move_past = (self.move_past + [self.move_seed])[-ROLL_HISTORY:]
        self.move_seed = _seed()
        self._changed()

    def _move_previous(self) -> None:
        if self.move_past:
            self.move_seed = self.move_past.pop()
            self._changed()

    # -- boss floor lock --

    def _show_lock(self, entries: list, rows: list) -> None:
        heaven, start = self.heaven, self._from()
        spoil = self.spoilers.isChecked() and self.lock_rewards != "hidden"
        choices = saveheaven.lock_choices(self.lock_mode)
        self.lock_one.blockSignals(True)
        self.lock_one.clear()
        if choices:
            self.lock_one.addItem(f"Any {saveheaven.LOCK_MODES[self.lock_mode][3]}", "")
            for key, label in choices:
                self.lock_one.addItem(f"Only {label}", key)
        self.lock_one.setCurrentIndex(max(0, self.lock_one.findData(self.lock_which)))
        self.lock_one.blockSignals(False)
        self.lock_one.setVisible(bool(choices))
        on = bool(self.lock_mode)
        left = len(heaven.ahead(start))
        self.lock_all.setText(f"Also write every remaining boss floor of the route ({left} after "
                              f"{heaven.label(start)}), not only the ones the game has planned, so the lock lasts "
                              "the whole run")
        for widget in (self.lock_all, self.lock_reward_row, self.lock_again, self.lock_table):
            widget.setVisible(on)
        self.lock_prev.setVisible(on and bool(self.lock_past))
        extra = saveheaven.extra_planned(heaven, start, entries)
        self.lock_clear.setVisible(not on and bool(extra))
        if extra:
            span = heaven.label(extra[0]) + (f"-{heaven.label(extra[-1])}" if len(extra) > 1 else "")
            self.lock_clear.setText(f"Take out the {len(extra)} boss floor(s) planned past the game's own ({span}), "
                                    "so the game plans them itself as you go (not yet tried in game)")
        if not on:
            self.lock_state.setText(f"The game plans the next {saveheaven.GAME_PLAN_AHEAD} boss floors as you go; "
                                    "nothing is changed here." if not self.clear_extra else
                                    "The floors past the game's own plan are taken out when you save.")
            return
        written = [r for r in rows if r.entry is not None]
        rewards = {"none": " These floors get no Legendary Chest: the game rolls their large boxes and boss drop, "
                           "but it doesn't make a Legendary Chest for a floor without one (tested in game).",
                   "chest": " Only their Legendary Chest is drawn now; the game rolls the large boxes and boss drop "
                            "when it builds the floor.",
                   "hidden": " Their rewards are drawn but never shown here, whatever Show rewards says.",
                   "roll": " Their rewards are drawn now from each floor's own tables."}[self.lock_rewards]
        text = f"{len(written)} floor(s) will be written.{rewards}"
        text += (" Not changed: the floor you're on (it's already built), NEO map B floors (one fixed arena), and"
                 + ("" if self.lock_redraw else " (until you press Reroll again)")
                 + f" planned floors that are already {saveheaven.LOCK_MODES[self.lock_mode][3]} floors.")
        if self.lock_mode == "don":
            text += (" Regular Heaven has no Dons below 100F, so those floors become mid-boss floors."
                     " Dons have no boss drop: their floors get a Legendary Chest and three large boxes.")
        if self.move_to:
            text += " It applies after the floor move above, so it replaces the moved floors' rerolled rewards."
        self.lock_state.setText(text)
        beyond = set(saveheaven.beyond_game(heaven, start))
        lines = []
        for row in rows:
            now = heaven.kind_label(row.old.get("refareaid")) if row.old else "not planned yet"
            if row.old and row.flrid in beyond:
                now += " (pre-planned)"
            if row.entry is None:
                after = "unchanged"
            else:
                after = heaven.kind_label(row.entry["refareaid"]) + (
                    " (rerolled)" if row.redrawn else "" if row.old else " (added)")
            if row.fallback:
                after += " - not on this floor, nearest kind"
            shown = row.entry or row.old
            if self.lock_rewards == "hidden" and row.entry:
                reward = "hidden (spoiler free)"
            elif shown and not shown.get("rwdid") and not saveheaven._list(shown.get("rwds")):
                reward = "no Legendary Chest; the game rolls the boxes and boss drop"
            elif shown and not saveheaven._list(shown.get("rwds")):
                reward = ("Legendary Chest" + (f": {self._name(shown['rwdid'])}" if spoil else "")
                          + "; the game rolls the boxes and boss drop")
            else:
                placed = [w for i, w in enumerate(saveheaven._list((shown or {}).get("rwds")))
                          if isinstance(w, dict) and i not in heaven.unplaced(shown)]
                reward = self._rewards_text((shown or {}).get("rwdid"), placed)
            if shown and saveheaven._int(shown.get("is_rare")) and self.lock_rewards != "hidden":
                reward += " (rare)"
            lines.append((heaven.full_label(row.flrid), now, after, reward))
        self._fill(self.lock_table, lines)

    def _lock_changed(self, *_args) -> None:
        self.lock_mode = self.lock_pick.currentData() or ""
        if self.sender() is self.lock_pick:
            self.lock_which = ""
        elif self.sender() is self.lock_one:
            self.lock_which = self.lock_one.currentData() or ""
        self.lock_extend = self.lock_all.isChecked()
        self.lock_rewards = self.lock_reward.currentData() or "roll"
        self.lock_redraw, self.lock_past = False, []
        self._changed()

    def _lock_again(self) -> None:
        self.lock_past = (self.lock_past + [(self.lock_seed, self.lock_redraw)])[-ROLL_HISTORY:] \
            if self.lock_redraw else []
        self.lock_redraw, self.lock_seed = True, _seed()
        self._changed()

    def _lock_previous(self) -> None:
        if self.lock_past:
            self.lock_seed, self.lock_redraw = self.lock_past.pop()
            self._changed()

    def _clear_toggled(self, on: bool) -> None:
        self.clear_extra = on
        self._changed()

    # -- I'm feeling lucky --

    def _lock_settings(self) -> tuple | None:
        return (self.lock_mode, self.lock_which) if self.lock_mode else None

    def _show_lucky(self, entries: list) -> None:
        check = saveheaven.lucky_check(self.heaven, self._from(), entries, self._owned_blueprints(),
                                       saveheaven.researched_parts(self.document), self._lock_settings())
        self.lucky_box.setVisible(check.ok or bool(self.lucky_seed))
        self.lucky_mask.setVisible(check.masks > 0)
        self.lucky_undo.setVisible(bool(self.lucky_seed))
        if self.lucky_seed and not check.ok:
            self.lucky_state.setText("Nothing left to hide in this run: the press is dropped when you save.")
        elif self.lucky_seed:
            self.lucky_state.setText("<b>Lucky run planned.</b> It's written when you save. Press again to roll it "
                                     "again. If you open the saved file here with Show rewards ticked, the planned "
                                     "floors' rewards are named.")
        else:
            self.lucky_state.setText("")

    def _lucky_pressed(self) -> None:
        self.lucky_seed = _seed()
        self._changed()

    def _lucky_undone(self) -> None:
        self.lucky_seed = 0
        self._changed()

    def _lucky_masks(self, on: bool) -> None:
        self.lucky_masks = on
        self._changed()

    # -- Jackal loot --

    def _show_jackals(self) -> None:
        heaven = self.heaven
        found = saveheaven.jackals(self.document)
        table = self.jackal_table
        table.setRowCount(len(found))
        for row, jackal in enumerate(found):
            jtype = jackal["type"]
            killed = saveheaven._int(jackal.get("killed"))
            now = saveheaven.jackal_loot(self.document, jackal)
            name = jtype.replace("JACKAL_", "Jackal ") + (" (defeated)" if killed else "")
            table.setItem(row, 0, _fixed(name))
            table.setItem(row, 1, _fixed(saveheaven.loot_label(heaven, now)))
            options = saveheaven.jackal_options(heaven, jtype)
            edit = self.jackal_edits.get(jtype)
            shown = edit[0] if edit else now
            pick = QComboBox()
            total = sum(o.weight for o in options) or 1
            for option in options:
                label = option.label
                if option.key == "money" and shown.kind == "money":
                    label = saveheaven.loot_label(heaven, shown)
                odds = f"{option.weight / total * 100:.{1 if option.weight / total < 0.1 else 0}f}%" \
                    if option.weight else "pick only"
                pick.addItem(f"{label} - {odds}", option.key)
            at = pick.findData(saveheaven.loot_key(shown))
            if at < 0:
                pick.addItem(saveheaven.loot_label(heaven, shown), "")
                at = pick.count() - 1
            pick.setCurrentIndex(at)
            pick.setEnabled(bool(options) and not killed)
            pick.currentIndexChanged.connect(lambda _i, t=jtype, box=pick: self._jackal_picked(t, box))
            table.setCellWidget(row, 2, pick)
            buttons = QWidget()
            line = QHBoxLayout(buttons)
            line.setContentsMargins(0, 0, 0, 0)
            roll = QPushButton("Reroll")
            roll.setEnabled(bool(options) and not killed)
            roll.clicked.connect(lambda _c=False, t=jtype: self._jackal_rolled(t))
            line.addWidget(roll)
            if edit:
                undo = QPushButton("Undo")
                undo.clicked.connect(lambda _c=False, t=jtype: self._jackal_undone(t))
                line.addWidget(undo)
            table.setCellWidget(row, 3, buttons)
        self.jackal_state.setText(f"{len(self.jackal_edits)} changed" if self.jackal_edits else "")

    def _jackal_set(self, jtype: str, loot) -> None:
        if loot is not None:
            self.jackal_edits[jtype] = (loot, str(uuid.uuid4()), int(time.time()))

    def _jackal_picked(self, jtype: str, box: QComboBox) -> None:
        option = next((o for o in saveheaven.jackal_options(self.heaven, jtype) if o.key == box.currentData()), None)
        if option is not None:
            self._jackal_set(jtype, option.take(self._rng))
            self._changed()

    def _jackal_rolled(self, jtype: str) -> None:
        self._jackal_set(jtype, saveheaven.roll_jackal(self.heaven, jtype, self._rng))
        self._changed()

    def _jackal_undone(self, jtype: str) -> None:
        self.jackal_edits.pop(jtype, None)
        self._changed()

    def _jackals_rolled(self) -> None:
        for jackal in saveheaven.jackals(self.document):
            if not saveheaven._int(jackal.get("killed")):
                self._jackal_set(jackal["type"], saveheaven.roll_jackal(self.heaven, jackal["type"], self._rng))
        self._changed()

    def _jackals_undone(self) -> None:
        self.jackal_edits.clear()
        self._changed()

    # -- the page as a whole --

    def _changed(self) -> None:
        self._refresh()
        self.changed.emit()

    @property
    def change_count(self) -> int:
        if self._leaving():
            return 1
        count = bool(self.move_to) + bool(self.lucky_seed) + len(self.jackal_edits)
        if self.lock_mode:
            roll = self._roll()
            count += any(r.entry is not None for r in self._lock_rows(self._moved(roll)))
        elif self.clear_extra:
            count += 1
        return count

    def apply(self, save: "saveedit.SaveDocument") -> None:
        if self.end.isChecked():
            saverun.end_run(save, self.limits, self.heaven)
            return
        if self.back.isChecked():
            saverun.rewind(save, self.heaven)
            return
        if self.move_to:
            saveheaven.move_floor(save, self.heaven, self.move_to, self.move_seed, self.reroll_rewards)
        if self.lock_mode:
            saveheaven.lock_floors(save, self.heaven, self.lock_mode, self.lock_which, self.lock_extend,
                                   self.lock_rewards, self.lock_redraw, self.lock_seed)
        elif self.clear_extra:
            saveheaven.clear_extra(save, self.heaven)
        if self.lucky_seed:
            saveheaven.feeling_lucky(save, self.heaven, self.lucky_seed, self.lucky_masks, self._lock_settings())
        for jtype, (loot, eid, now) in self.jackal_edits.items():
            saveheaven.set_jackal_loot(save, self.heaven, jtype, loot, eid, now)
