Download the zip below, unzip it anywhere **writable** — not Program Files —
and run `LID DB Mod Manager.exe`.

**No Python needed.** Python, Qt and the C++ runtime are all inside the build.

Windows will warn that the publisher is unknown, because the build is unsigned.
Click **More info**, then **Run anyway**. Some antivirus flags PyInstaller
executables for the same reason.

Keep the whole folder together: the `.exe` needs `_internal\` beside it, and
`mods\` is where your mods live.

### Before you start

Point it at a **clean, unmodified** `masters.db`. The moment you pick it, the
manager keeps a permanent `masters.db.original` copy and never overwrites it —
but that is only a real way back to stock if the file was untouched when you
started. If yours has already been edited, delete it and let Steam re-download
it first (Properties → Installed Files → Verify integrity of game files).

### What's new

**Load order.** Enabled mods are numbered. Top applies first, bottom wins, and
the order is yours — the manager never silently reorders it. Move things with
the buttons under the list or Ctrl+Up / Ctrl+Down.

**Drag a mod onto the window.** A `.sql` file, a mod folder or a `.zip`. You get
a small dialog to name it, and it arrives switched off so you can read its diff
first. Same thing under Tools → Add a mod from a file.

**Turn a modded `masters.db` into a mod.** Tools → Create a mod from a modded
masters.db compares someone's reworked database against your untouched copy and
writes the difference out as an ordinary, toggleable mod. Changed values, added
rows, removed rows and whole tables the game shipped without all come across.
It refuses a file from a different game version rather than quietly undoing the
developers' own changes.

**`"apply": "diff"` for mod authors.** A mod set to this runs against a copy of
vanilla and contributes only the values it genuinely alters — so a whole-table
dump stops wiping out rows another mod set and never meant to fight over. See
`mods/README.md`.

**Smaller, faster revert.** Snapshots now record only the rows a mod actually
touches instead of copying whole tables, which took one real mod's snapshot
from 20 MB to 170 KB.

Fixed: re-applying a mod onto a database it was already applied to used to
overwrite its snapshot with the modded values, quietly destroying revert. Also
fixed a false conflict warning between two mods that write the same table in
rows that never overlap.
