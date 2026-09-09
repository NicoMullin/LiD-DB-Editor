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
- **Backs up.** The first save writes `masters.db.original` and never touches it
  again, so there is always a way back to stock without re-downloading.
- **Undoes individual mods.** Before each mod runs, the rows it is about to
  change are copied aside. Turning one mod off puts exactly those rows back and
  leaves your other mods alone.
- **Survives game updates.** It watches the database and, when the game replaces
  it, re-applies your enabled mods automatically.
- **Shows you the change.** A diff panel lists the actual rows a mod will
  rewrite, before and after, before you commit to it.

It is fully offline: no network calls of any kind, no telemetry, and no Steam
integration — it reads and writes exactly one file, the one you point it at.

## Requirements

- Python 3.11 or newer. Use a version PySide6 has wheels for — as of PySide6
  6.11 that means 3.11 to 3.13. (The command line works on any 3.11+.)
- Windows, macOS or Linux. Developed on Windows.

## Running it

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

The first time you save, whatever is in that file becomes `masters.db.original`
— the copy the tool never overwrites and always restores from. If the database
has **already** been edited, by hand or by another tool, then that "original" is
a copy of the edited version and nothing can get you back to stock.

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

Only **Save Mod List** takes backups. Re-apply, automatic or manual, never does
— so an automatic re-apply can't quietly rotate your good backups away.

## Mods included

Six, in `mods/`. The JSON ones carry their own `readme.md`.

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

## Making your own mods

Drop a folder into `mods/` and press F5. A mod is either a `mod.json` or just a
`.sql` file — both work, and there are two templates to copy.

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

I have inculded a release with an exe compiled, if you are wanting to do it yourself for whatever reason, keep reading.

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
| On disk        | 116 MB           | 46 MB                   |
| Startup        | 0.2 s            | 0.9 s (unpacks on load) |
| Zipped release | 47 MB            | 46 MB                   |

Either way you are shipping a zip, because `mods/` has to travel with the exe —
so the single-file build buys tidiness rather than size. The folder build starts
faster and is far easier to debug when someone reports it not launching. Pick
`--onefile` if you would rather hand people one file.

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
