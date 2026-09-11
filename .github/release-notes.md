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

### What's new in 0.3.0

**Mods that replace game files.** New models, outfits and artwork shipped as
`.upk` files now work too. Drag a folder with an `assets` folder of `.upk`
inside it onto the window; the files are copied into the game while the mod is
on and taken back out when it is off. Only a vanilla file a mod actually
replaces is ever backed up, one copy, and only while that mod is on.

**Crossover Content pack support.** The README walks through installing the
community Crossover Content pack, including its own installer, as two mods you
can switch on and off.

**Take only part of a modded `masters.db`.** Importing one now shows every
change it found, table by table, expandable to single edits. Untick what you do
not want.

**One mod, switchable parts.** An imported rework stays a single mod with one
name, holding a switch for each table. Expand it in the list and turn parts on
or off whenever you like.

**Unticking a mod now undoes it.** Untick and Save Mod List, and its values go
back to what they would be without it — vanilla, or whatever a mod underneath
sets. Switching off one part of a mod works the same way.

**Edit a mod in the program.** Tools → Edit mod details (or F2): rename it,
change its description, write its readme. No text editor, no hunting for the
folder.

Safety: a mod can never copy program files (`.exe`, `.dll` and the like) or
replace `masters.db` itself. Such a mod shows in the list as broken, with the
reason.
