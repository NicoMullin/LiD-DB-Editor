Download the zip below, unzip it anywhere **writable** — not Program Files —
and run `LID DB Mod Manager.exe`.

**No Python needed.** Python, Qt and the C++ runtime are all inside the build.

Windows will warn that the publisher is unknown, because the build is unsigned.
Click **More info**, then **Run anyway**. Some antivirus flags PyInstaller
executables for the same reason — the SHA-256 of this zip is at the bottom of
these notes, so you can check the file you got is the one built here.

Keep the whole folder together: the `.exe` needs `_internal\` beside it, and
`mods\` is where your mods live.

### Updating from 0.10.1 or older

Unzip this release into a folder of its own and close your old copy. On its
first start this one asks whether you are new or updating: click **I'm
updating** and pick your old copy's folder. It brings over your mod list, load
order, settings, the record of what each mod changed, your backups and your own
mods — plus the clean databases older versions carried — so nothing has to be
switched off and on again. Then click **Save Mod List** once. The old folder is
only read; delete it when you are happy.

This is the last time you need to do that: from this version on, updates are
one click (see below).

### Before you start

Point it at your `masters.db`. It does not have to be untouched: the manager
compares it with a clean copy of the same game build, says which mods it can
already see in there, and offers to take them over. The moment you pick a
database it also keeps a permanent `masters.db.original` copy.

### What's new in Beta V0.11.0

**Updates in one click.** **Help → Check for updates...** asks the manager's
GitHub page whether there is a newer version, and **Update now** downloads it,
checks it against the SHA-256 GitHub lists for it, and restarts as the new
version. Only the program is replaced — your mods, settings, snapshots and
backups stay exactly where they are, nothing has to be switched off first, and
the folder keeps its name so your shortcuts keep working. If anything goes wrong
partway, the old version is put back. Tick **Help → Check for updates when it
starts** to be told without asking; it is off until you switch it on.

**A much smaller download.** The clean copies of `masters.db` the manager
compares against no longer ship inside the program — that was 276 MB of the
game's own data. The one for your game build is downloaded from the manager's
GitHub page when you need it (about 57 MB), and only when you click. It is kept
only if it matches the size and checksum GitHub lists for it and says it is your
build. **Tools → Download a clean database...** fetches one any time.

**A friendlier first start.** It asks whether you are new or updating. New
players then choose where the clean database comes from: **Download from
GitHub** (recommended, and right even if your game already has mods in it) or
**Use my game files** (if you have never modded the game, or have just verified
its files in Steam).

**Bring over from your old version.** Also on **Tools → Bring over from your old
version...**, or drop the old folder or its `.exe` on the window.

**A new mod: Stew Multi Pull.** Adds **Purchase x5 / x10 / x15 / x20 / x25** to
the mushroom stew — pick the count in the mod's settings. The cards come one
after another, it works with the decal pull cost mod, and if you run out of Kill
Coins partway it stops and says so. The stew animation plays once rather than for
every pull.

**Each release's folder carries its version**, like `LID DB Mod Manager
0.11.0`, so you can tell copies apart. An in-app update keeps the folder's name
(so shortcuts keep working); rename it yourself if you like — the program does
not mind what its folder is called.

The manager now goes online in exactly three cases, each one your own click or
setting: downloading a clean database, checking for updates, and updating. It
only ever talks to GitHub.
