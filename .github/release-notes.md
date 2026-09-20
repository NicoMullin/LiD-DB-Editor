Download the zip below, unzip it anywhere **writable** — not Program Files —
and run `LID DB Mod Manager.exe`.

**No Python needed.** Python, Qt and the C++ runtime are all inside the build.

Windows will warn that the publisher is unknown, because the build is unsigned.
Click **More info**, then **Run anyway**. Some antivirus flags PyInstaller
executables for the same reason — the SHA-256 of this zip is at the bottom of
these notes, so you can check the file you got is the one built here.

Keep the whole folder together: the `.exe` needs `_internal\` beside it, and
`mods\` is where your mods live.

### Updating from an earlier release

You do not need a new folder. Either copy this release's `.exe` and `_internal\`
over your old ones, or unzip this one somewhere new and copy `state.json`,
`snapshots\`, `backups\` and your own `mods\` across. Both keep your mod list,
your load order and the copies of the game files your mods replaced. Do not run
two folders against the same game — each keeps its own record of what it
applied. The README has the details.

### Before you start

Point it at your `masters.db`. It does not have to be untouched: on the first
run the manager compares it with a clean copy of the same game build, says which
mods it can already see in there, and offers to take them over. The moment you
pick a database it also keeps a permanent `masters.db.original` copy.

### What's new in Beta V0.7.0

**Mods made for TFC Installer install directly.** Drag in a mod folder built for
TFC Installer — GLaDOS, Tommy Gun and the like — and the manager rebuilds the
game's packages itself and installs the textures. No TFC Installer needed, and
unticking the mod puts the game's own files back. If a package was already
changed by TFC Installer or by an older install, it now rebuilds from an
untouched copy found in TFC Installer's own backups instead of refusing, and
texture files no longer pile up with a new copy on every reinstall.

**A loading bar while your mods are applied.** Save Mod List and Re-apply All
now show a small window naming each step — backing up, each mod, rebuilding each
game package, copying files — so a long save no longer looks like a freeze.

**Warning dots in the mod list.** A yellow dot means something worth a look,
like two mods writing the same table. A red dot means one mod overwrites the
exact values another one sets, or the mod failed. Rows no longer force
themselves open — open one when you want to read why. Hovering a mod's name
explains its dot.

**Game build 5.0.4.2 supported,** with a clean copy of it included. That update
changed only the Steam version number and left the in-game one reading 1.89,
which made the manager treat the patch as if you had edited the file yourself.

**After a game update the manager keeps its own clean copy.** The `masters.db`
Steam has just written is untouched by definition, so it is kept as the
reference copy for the new build — no waiting for a release that ships one. It
only does that when the file shows no sign of a mod, and when it cannot tell, it
asks you.

**Mods can say they need the game's file check switched off.** The game keeps a
hash for most of its own packages and refuses a replacement at startup. A mod
that replaces one of those now says so, and the manager explains it before
anything is written instead of letting you meet that error box. Colored
PlayStation Buttons uses this instead of changing a hash inside the game's
executable.

**Drop in a plain folder holding one `.sql` file** (plus an `assets` folder if it
has one) and it installs as a mod — no `mod.json` to write. SQL exported from DB
Browser for SQLite is understood, including its `"main".` table prefixes.

**Fixes**

- A rebuild that failed part way used to leave your database replaced by the
  clean copy. Your own file now comes back, along with your mod list and the
  saved rows that let each mod be switched off.
- Rebuilds keep the load order recorded inside your database — and read it from
  your backups when a game update has wiped it.
- The Crossover pack is marked as working on 1.89, so it no longer shows a
  version warning.

788 tests pass on Python 3.13 and 3.14.
