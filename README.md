# LET IT DIE DB Mod Manager

A small desktop tool for modding LET IT DIE. It applies community-made patches
to the game's `masters.db` — the SQLite file holding prices, skill values, craft
costs and every other tuning number — and puts them back when the game replaces
that file.

Mods are plain JSON or SQL in a folder. Nothing is compiled, nothing is
injected, nothing is installed into the game.

> **Status: beta.** It has been used against a live install, but treat your
> `masters.db` as precious anyway — which is what most of the tool is about.

## What it does

- **Applies your mod list** to `masters.db`, in one transaction. If any mod
  fails, none of them are applied — you never end up half-modded.
- **Checks first.** Every mod is validated against your actual database on a
  read-only connection before a single byte is written: tables and columns must
  exist, SQL must compile. A mod written for a different game version is
  reported, not run.
- **Backs up before it does anything.** The moment you point it at a database
  it keeps `masters.db.original` and never touches that file again, so there is
  always a way back to stock without re-downloading.
- **Undoes individual mods.** Before each mod runs, the rows it is about to
  change are copied aside. Turning one mod off puts exactly those rows back and
  leaves your other mods alone.
- **Survives game updates.** It watches the database and, when the game replaces
  it, re-applies your enabled mods automatically.
- **Shows you the change.** A diff panel lists the actual rows a mod will
  rewrite, before and after, before you commit to it.
- **Takes mods however they arrive.** Drag a `.sql`, a mod folder or a `.zip`
  onto the window. Hand it somebody else's already-modded `masters.db` and it
  works out the difference and turns that into a mod you can switch off again.
- **Lets you decide who wins.** Mods apply in a load order you control, so a
  small tweak can override one value from a huge rework and leave the rest.
- **Keeps blunt mods from trampling careful ones.** A mod marked
  `"apply": "diff"` is run against an untouched copy of the database first, and
  only the values it genuinely changes are written — so a whole-table dump stops
  wiping out everything else, and re-applying it can never compound.

It is fully offline: no network calls of any kind, no telemetry, and no Steam
integration — it reads and writes exactly one file, the one you point it at.

## Requirements

- Python 3.11 or newer. Use a version PySide6 has wheels for — as of PySide6
  6.11 that means 3.11 to 3.13. (The command line works on any 3.11+.)
- Windows, macOS or Linux. Developed on Windows.

## Running it

Note there is a release with a prebuilt exe if you do not want to install Python
and the other requirements.

```bash
pip install -r requirements.txt
python run.py
```

Then:

1. Point it at your `masters.db` — usually under
   `...\steamapps\common\LET IT DIE\BrgGame\Content\masters.db`.
   **Use a clean one** — see below.
2. Tick the mods you want.
3. Click **Save Mod List**.

That last click is the one that does everything: backups, validation, then the
apply, all as one step you can undo.

## Point it at a clean `masters.db`

The moment you pick it, that file is copied to `masters.db.original` — the copy
the tool never overwrites and always restores from. If the database has
**already** been edited, by hand or by another tool, then that "original" is a
copy of the edited version and nothing can get you back to stock.

If you are not certain yours is untouched, replace it **before** pointing this
tool at it: delete `masters.db` and let Steam re-download it (Properties →
Installed Files → Verify integrity of game files). Doing that afterwards means
downloading it all over again, which is exactly what the `.original` exists to
spare you.

## Undoing things

Two independent levels:

- **Revert Selected**, the button at the bottom of the window — unwinds one mod
  from the rows it saved before applying, leaving everything else in place.
- **Tools → Restore a backup**, which replaces the whole database. It lists the
  `.original` first, then the rolling `masters.db.backup` from your most recent
  save, then the dated copies in `backups/` (newest five kept).

`.original` is taken when you choose the database; the rolling and dated
backups are taken by **Save Mod List**. Re-apply, automatic or manual, never
takes one — so an automatic re-apply can't quietly rotate your good backups
away.

## Mods included

Four, in `mods/`, each with its own `readme.md`.

| Mod                     | What it does                                             |
|-------------------------|----------------------------------------------------------|
| `revive-cost-1kc`       | Every grade's revive costs 1 Kill Coin                   |
| `body-prices-1kc`       | Every fighter tier unlock costs 1 Kill Coin              |
| `nitro-boost-100000pct` | Nitro Boost and Turbo-charged Engine give 100,000% EXP   |
| `nitro-boost-text`      | Makes those two skill descriptions say 100,000% to match |

Two caveats worth knowing before you enable them:

- `revive-cost-1kc` — you are charged 1 KC, but the price on the sign held up
  in-game is part of a **texture**, not the database, so it still shows the old
  number. Matching it means replacing that texture yourself.
- `nitro-boost-text` — covers English, German, Spanish, French, Italian and
  Portuguese. Japanese, Chinese and Korean keep the stock wording.

## Adding mods

You do not have to find the `mods/` folder any more. **Drop the file on the
window** — anywhere on it, while the manager is running — and it installs
itself. Four things are accepted:

| What you drop            | What happens                                                 |
|--------------------------|--------------------------------------------------------------|
| A `.sql` patch           | Becomes a new mod folder built around that file              |
| A folder with SQL in it  | Installed as-is, `mod.json` or not                           |
| A `.zip`                 | Unpacked and installed, refusing any path that escapes it    |
| An edited `masters.db`   | Compared against vanilla and turned into a mod (below)       |

A small dialog asks for a name — prefilled from the filename — and optionally a
description, author and version. That writes a real `mod.json`, so the entry
reads properly in the list instead of showing up as an unnamed `.sql`.

Drop several at once and you get one dialog each, in turn. `.sqlite` and
`.sqlite3` count as databases too.

**Whatever you drop arrives switched off.** Nothing is applied by the act of
adding it. Enable it when you have read its diff, then Save Mod List.

If you would rather use a menu, **Tools → Add a mod from a file** does exactly
the same thing. And putting a folder in `mods/` by hand and pressing **F5**
still works.

### Someone posted a whole reworked `masters.db`

Drop it on the window, or use **Tools → Create a mod from a modded
masters.db**. The manager compares it against your untouched `masters.db.original`
and writes out **just the differences as an ordinary mod** — one that sits in
your list with everything else and can be **switched on and off**.

That matters because the usual way to share a rework is the whole database
file, which you install by overwriting yours. Doing that throws away every
other mod you had, and the only way back is another file swap. As a mod it is
just another row in the list: toggle it, put things above or below it, revert
it.

The comparison is not limited to retuned numbers. Changed values, added rows,
removed rows and whole tables the game shipped without all come across, with
their indexes — modders keep finding more they can do to `masters.db`, and a
manager that quietly drops the parts it does not recognise is worse than one
that refuses them outright. Reverting such a mod drops the tables it added and
puts everything else back.

It refuses outright if the file came from a different game version — a database
missing a vanilla table, or with different columns in one. That difference
would read as "undo the developers' changes", and applying it would quietly
roll the game back.

## Load order

Enabled mods sit at the top of the list, **numbered**. That number is the order
they are applied in: **top applies first, bottom wins.** Move things with the
buttons under the list, or Ctrl+Up / Ctrl+Down.

So if a big rework sets a hundred values and you want one of them different,
put your small mod **below** it. It applies last, so its value is the one that
survives, and the other ninety-nine are untouched.

The order is yours. The manager never silently reorders it — if a mod ends up
above something it declares it `requires`, you get a warning telling you to
move it rather than having things shuffled behind your back. You also get a
warning when a required mod is not enabled at all.

Conflict warnings name the two mods and **the rows they share**, not just the
table. Two mods that both write `master_text` but never touch the same row are
not a conflict and are not reported — which is what makes the warnings worth
reading when they do appear.

Before this existed, mods applied in whatever order their checkboxes happened to
get ticked. That order was invisible, and unticking a mod and ticking it again
silently changed who won.

## Saving your mod list

**Save Mod List** (the button at the bottom, or Ctrl+S) is the only thing that
writes to your database. Everything else — enabling, reordering, dropping files
in — only changes the list. In order, it:

1. **Backs up.** A rolling `masters.db.backup`, plus a dated copy in
   `backups/` (newest five kept).
2. **Validates every enabled mod** against your actual database on a read-only
   connection. Tables and columns must exist, SQL must compile. A mod written
   for a different game version is reported, not run.
3. **Snapshots the rows each mod is about to change**, so that mod alone can be
   undone later.
4. **Applies the whole list in load order, in one transaction.** If any mod
   fails, *none* of them are applied — the database is left exactly as it was.
   You never end up half-modded.
5. **Records the result**, so the manager can tell later whether the game has
   replaced the file behind your back.

The log reports how many rows each mod changed and how long the whole thing
took. Those row counts are worth a glance: a mod claiming far more rows than
you expected is usually a whole-table dump, and the note under *Making your own
mods* about `"apply": "diff"` explains what to do about it.

**Re-apply All** (Ctrl+R) runs the same list again without taking a fresh
backup — it is what the watchdog calls when the game replaces `masters.db`
after an update. Skipping the backup is deliberate: an automatic re-apply must
never quietly rotate your good backups away.

## When a change doesn't show up in game

The manager tells you what it wrote to `masters.db`, and the Diff preview shows
you the rows. If the game still looks unchanged, the mod probably applied fine
and the game simply has not re-read it yet.

The clearest case is **shop and vending machine contents**. Those lineups are
settled by the game's daily reset, so newly added items will not appear until
the in-game day rolls over — no amount of re-applying will hurry it along.

Before assuming a mod is broken:

- Check the log and the Diff preview. If the rows are listed there, they are in
  the database.
- Restart the game. Some tables are read once at launch.
- For anything shop-related, wait for the daily reset.
- If you want to be certain, open `masters.db` in a SQLite browser and look at
  the rows directly.

## Making your own mods

A mod is either a `mod.json` or just a `.sql` file — both work, and there are
two templates to copy.

**[mods/README.md](mods/README.md) is the full guide**: every folder layout,
every patch type, how revert and conflict detection see your mod, and the traps
worth knowing about.

## Command line

Everything the window does also works headless — useful for testing a mod, or on
a machine without PySide6:

```bash
python run.py set-db "D:\...\Content\masters.db"
python run.py list
python run.py enable revive-cost-1kc
python run.py preview revive-cost-1kc     # what it would change, no writes
python run.py apply
python run.py revert revive-cost-1kc
python run.py backups
python run.py watch                       # poll and re-apply on change
```

`python run.py --help` lists the rest.

## Where files live

```
mods/          mod folders, yours and the shipped ones
snapshots/     per-mod row snapshots, used by Revert
backups/       dated database backups, newest five kept
logs/          one plain-text log per session, newest 30 kept
state.json     enabled mods, modpacks, settings, last-seen database hash
```

All created on first run. Set `LID_DB_MANAGER_HOME` to put them somewhere else.

## Building an .exe

There is a release with an exe already compiled. If you want to build it
yourself for whatever reason, keep reading.

```bash
pip install pyinstaller
python build.py              # -> dist/LID DB Mod Manager/
python build.py --onefile    # -> dist/LID DB Mod Manager.exe
```

Use the interpreter PySide6 is installed in. `build.py` runs PyInstaller and
then copies `mods/` next to the finished executable, which is the part a bare
`pyinstaller run.py` gets wrong: a frozen build treats the folder the `.exe`
sits in as its home, so `mods/`, `logs/`, `snapshots/`, `backups/` and
`state.json` all live there. **Put it somewhere writable — not Program Files.**

|                | Folder (default) | `--onefile`             |
|----------------|------------------|-------------------------|
| On disk        | 117 MB           | 47 MB                   |
| Startup        | 0.2 s            | 0.9 s (unpacks on load) |
| Zipped release | 60 MB            | 46 MB                   |

The folder build starts four times faster and is far easier to diagnose when
someone reports it not launching, which is why it is the default. `--onefile`
is a 14 MB smaller download. Either way you ship a zip, because `mods/` has to
travel alongside the executable.

**Do not run the app out of `dist/`.** That folder is build output — rebuilding
wipes it, and the app keeps its `state.json`, `snapshots/` and database backups
next to itself. Copy the build somewhere permanent and run it from there.

Other flags: `--icon path.ico`, and `--console` if you want the command line to
work from the `.exe` (a windowed build still writes to a redirected pipe, but
prints nothing into a `cmd` window).

### What the people you send it to need

Nothing. Python, PySide6, Qt and the Visual C++ runtime are all inside the
bundle — verified by running it on a path with no Python anywhere on `PATH`.

What they do need to know:

- **It is 64-bit Windows only.** Building on Windows produces a Windows binary;
  macOS and Linux users run it from source, which works fine.
- **Ship the whole folder.** The `.exe` from a folder build will not start
  without `_internal/` beside it, and neither build has any mods without
  `mods/`. Zip the lot.
- **Unzip somewhere writable** — not Program Files. The app keeps its settings,
  logs and backups next to itself.
- **SmartScreen will warn them.** The build is unsigned, so the first people to
  run it get "Windows protected your PC" and have to click More info → Run
  anyway. Some antivirus also flags PyInstaller executables. Both are normal for
  an unsigned hobby release; avoiding them needs a paid code-signing
  certificate.

## Running the tests

```bash
python -m unittest discover -s tests -t tests
```

112 tests. They build a miniature `masters.db` from `tests/fixtures.py`, so no
game files are needed. The GUI tests run offscreen and skip themselves if
PySide6 is not installed.

## License

MIT — see [LICENSE](LICENSE).
