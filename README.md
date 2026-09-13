# LET IT DIE DB Mod Manager

A small desktop tool for modding LET IT DIE. It applies community-made patches
to the game's `masters.db` — the SQLite file holding prices, skill values, craft
costs and every other tuning number — and puts them back when the game replaces
that file.

Mods are plain JSON or SQL in a folder, plus `.upk` game files for mods that
add models and artwork. Nothing is compiled and nothing is injected — no
program file is ever added to the game.

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
  rewrite, before and after, before you commit to it — and an **In plain
  English** tab says what that means, using the game's own names and wording.
- **Lets you build your own, without SQL.** An editor over a clean copy of the
  database, grouped into the parts people actually mod, with every column
  labelled by what it means. Change the numbers, press save, get a mod.
- **Takes mods however they arrive.** Drag a `.sql`, a mod folder or a `.zip`
  onto the window. Hand it somebody else's already-modded `masters.db` and it
  works out the difference and turns that into a mod you can switch off again.
- **Handles game files too.** Mods that ship new models and artwork as `.upk`
  files are copied into the game while switched on and taken back out when
  switched off.
- **Installs the Crossover Content pack in one drop.** Artwork and the database
  changes that make it reachable go in together as a single mod you can switch
  off again — no running its installer, no swapping `masters.db` by hand.
- **Handles artwork the game checks.** Most packages carry a checksum inside the
  game executable, so replacing one normally fails. For a short, hand-vetted list
  of mods the manager updates that one value too — twenty bytes of data, no code,
  fully reversible — and refuses to do it for anything not on the list.
- **Notices when a mod was built for another game version.** A mod can record
  the build it was made against; if your database is a different one, you are
  told before you save. It still applies — most do work across a patch — but a
  mismatch that would otherwise only show up in game stops being invisible.
- **Lets you decide who wins.** Mods apply in a load order you control, so a
  small tweak can override one value from a huge rework and leave the rest.
- **Keeps blunt mods from trampling careful ones.** A mod marked
  `"apply": "diff"` is run against an untouched copy of the database first, and
  only the values it genuinely changes are written — so a whole-table dump stops
  wiping out everything else, and re-applying it can never compound.

It is fully offline: no network calls of any kind, no telemetry, and no Steam
integration. It writes the `masters.db` you point it at, plus any game files a
mod ships — normally `.upk` files in `BrgGame\CookedPCConsole\`.

**No mod can add a program file** (`.exe`, `.dll` and the like) anywhere, and no
mod can change the game's code. There is exactly one thing it will change inside
`BrgGame-Steam.exe`: a single file checksum, twenty bytes in a data table, for
mods that replace artwork the game verifies. It is checked before and after that
the executable's code section is byte-for-byte unchanged, it is backed up and
fully reversible, and it only ever happens for changes recorded in this
repository by hand — never at a mod's request. See
[Mods that need a change to the game executable](#mods-that-need-a-change-to-the-game-executable).

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

## Switching a mod off

Untick it and press **Save Mod List**. The values it wrote go back — but not
blindly to vanilla. They go back to *whatever they would be without it*:

| The value                              | What it becomes               |
|----------------------------------------|-------------------------------|
| Only the switched-off mod wrote it     | Vanilla                       |
| Another enabled mod writes it too      | That mod's value              |
| The switched-off mod was overriding it | The mod underneath takes over |

That falls out of how snapshots work rather than any clever reasoning: before a
mod runs, the rows it is about to change are copied aside, and what was there
at that moment already *was* the other mod's value. Switching one part of a mod
off works the same way.

Nothing happens to the database until you save — unticking on its own only
changes the list.

The one time it declines: if the game has replaced `masters.db` since those
mods were applied, the saved rows describe values that are no longer there, and
writing them back would put an old version's numbers over a new one's. The
manager says so in the log and leaves them alone.

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

Eighteen, in `mods/`, each with its own `readme.md`.

### Costs and rewards

| Mod                     | What it does                                            |
|-------------------------|---------------------------------------------------------|
| `revive-cost-1kc`       | Every grade's revive costs 1 Kill Coin                  |
| `body-prices-1kc`       | Every fighter tier unlock costs 1 Kill Coin             |
| `nitro-boost-100000pct` | Nitro Boost and Turbo-charged Engine give 100,000% EXP, and their descriptions say so |
| `decal-cost-25k` / `-10k` / `-5k` | The Mushroom Club's 50,000 KC decals cost that instead |
| `tdm-rewards-2x` / `-5x` / `-10x` | Every Kill Coin and SP payout from Tokyo Death Metro, multiplied |

### Space

| Mod              | What it does                                       |
|------------------|----------------------------------------------------|
| `bank-limit-10x` | Both banks hold ten times as much, all 99 levels   |
| `reward-box-250` | The reward box holds 250 instead of 50             |
| `storage-10000`  | The Coin Locker expands to 10,000 instead of 2,000 |

### Durability and ammo

| Mod                    | What it does                                       | Rows |
|------------------------|----------------------------------------------------|------|
| `weapon-durability-2x` | Every weapon lasts twice as long                   |  385 |
| `weapon-durability-5x` | Every weapon lasts five times as long              |  385 |
| `armor-durability-2x`  | Every piece of armour lasts twice as long          |  979 |
| `armor-durability-5x`  | Every piece of armour lasts five times as long     |  979 |
| `weapon-ammo-2x`       | Every gun carries twice as much spare ammo         |  117 |
| `weapon-magazine-2x`   | Every gun holds twice as many rounds per magazine  |  160 |

The x2 and x5 versions of the same thing are **alternatives** — pick one. Each
names the other in `conflicts_with`, so the manager warns if you tick both.
Mixing across the groups is fine: weapon durability, armour durability, ammo
and magazine all write different columns and the manager knows it.

These multiply rather than set a number, so they are marked `"apply": "diff"` —
the manager measures them against an untouched copy of the database every time,
and saving twice can never turn x2 into x4.

Two weapon families have a magazine but no reserve ammo at all — rocket
launchers, flame wands, the Red Hot Iron line. Everything they will ever fire
sits in the magazine, so `weapon-ammo-2x` does nothing for them and
`weapon-magazine-2x` doubles their entire supply.

Where a mod comes in several strengths — decals, TDM rewards, and the
durability pairs above — they are **alternatives**. Each names the others in
`conflicts_with`, so the manager warns if you tick more than one.

Two caveats worth knowing before you enable them:

- `revive-cost-1kc` — you are charged 1 KC, but the price on the sign held up
  in-game is part of a **texture**, not the database, so it still shows the old
  number. Matching it means replacing that texture yourself.
- `nitro-boost-100000pct` — its description rewrite covers English, German,
  Spanish, French, Italian and Portuguese. Japanese, Chinese and Korean keep
  the stock wording. That half is a separate patch inside the mod, so you can
  untick it and keep the number change on its own.

## Adding mods

You do not have to find the `mods/` folder any more. **Drop the file on the
window** — anywhere on it, while the manager is running — and it installs
itself. Five things are accepted:

| What you drop                              | What happens                                                                                                         |
|--------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| A `.sql` patch                             | Becomes a new mod folder built around that file                                                                      |
| A folder with SQL in it                    | Installed as-is, `mod.json` or not                                                                                   |
| A folder with an `assets` folder of `.upk` | Becomes a mod that copies them into the game — see [Mods that replace game files](#mods-that-replace-game-files-upk) |
| A `.zip` with a `mod.json` or `.sql` in it | Unpacked and installed, refusing any path that escapes it                                                            |
| An edited `masters.db`                     | Compared against vanilla; you pick which changes to keep (below)                                                     |

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
and writes out **just the differences as ordinary mods** that sit in your list
with everything else and can be **switched on and off**.

That matters because the usual way to share a rework is the whole database
file, which you install by overwriting yours. Doing that throws away every
other mod you had, and the only way back is another file swap.

**You choose what to take.** The dialog shows every change it found, grouped by
table:

```
  [x] master_shop_product_price     130 changed
  [ ] master_automaticshop_lineup   315 changed
  [x] master_tdm_rank                14 changed
        [x] TDM_RANK_01     point_min: 1200 → 0
        [ ] TDM_RANK_02     point_min: 2400 → 0
        [x] TDM_RANK_03     point_min: 3600 → 0

  Taking 113 of 661 change(s), across 3 table(s).
```

Untick a table to leave that part of the rework out entirely, or expand it and
untick individual edits. There is a filter box for finding things in a long
list, and All / None buttons.

**The choice is not a one-off.** What you install is a single mod with one name
and one readme, holding a switch for each table. Expand it in the list and tick
the parts you want — the ones you turn off simply are not applied next time you
save, and you can change your mind whenever you like:

```
  [x]  1   Big Rework
             master_shop_product_price   130 changed
       [x]  master_tdm_rank              14 changed
       [ ]  master_automaticshop_lineup  315 changed
```

Tick *Install each table as a separate mod* in the import dialog if you would
rather have one mod per table — useful when you want to slot other mods
*between* parts of a rework in the load order, since a single mod's parts all
apply together.

The catch is worth knowing: taking part of a rework creates a combination its
author never tested. Keep an added vending item but drop the product it refers
to and you get a mod that half-works. Each part's `.sql` records where it came
from.

Switching a part off and saving puts that part's values back, the same as
unticking a whole mod — see below.

## Mods that replace game files (`.upk`)

Some mods are not database edits at all — they are new models, outfits and
artwork, shipped as `.upk` files. The manager handles those too. The files
live in their mod folder, and are copied into the game only while the mod is
switched on.

### How the folder has to look

A folder, with a folder called **`assets`** inside it, holding the `.upk` files:

```
My Outfit Mod/            <- drag THIS folder onto the window
└── assets/               <- must be called exactly "assets"
    ├── CH_Kat_Head_SF.upk
    └── CH_Kat_Body_SF.upk
```

**Drag the outer folder onto the DB editor window.** The info window opens:

| Field           | What to put                                                      |
|-----------------|------------------------------------------------------------------|
| **Name**        | Filled in from the folder name — change it to something readable |
| **Description** | Optional. One line saying what the mod does                      |
| **Author**      | Optional. Whoever made the mod                                   |
| **Version**     | Optional. The mod's version, if it has one                       |

Click **Add mod**. It arrives switched off; tick it and **Save Mod List**, and
the files are copied into `BrgGame\CookedPCConsole\`. Untick it and save again
and they are taken back out.

If it says there are no game files to copy, the folder is not quite right:

| What you dragged                                                     | Fix                                               |
|----------------------------------------------------------------------|---------------------------------------------------|
| The inner folder is called something other than `assets`             | Rename it to `assets`                             |
| A folder inside a folder inside a folder — unzipping often does this | Drag the one that has `assets` directly inside it |
| A `.zip`                                                             | Unzip it first, then drag the folder              |

(Loose `.upk` files sitting straight in the folder, with no `assets` folder,
also work.)

**Close the game first.** A running game holds its files open; the manager
refuses to copy anything until LET IT DIE is closed.

**Backups stay small.** Only a vanilla file a mod actually replaces is backed
up — one copy, kept only while that mod is on. A mod that only *adds* new
files needs no backup at all.

**Program files are refused.** A mod may never copy `.exe`, `.dll` or similar
into the game, since Windows would run them. Real content packs never contain
them; if one does, it shows in the list as broken, with the reason.

## The Crossover Content pack

The community **LET IT DIE Crossover Content** pack restores cut crossover
content — decals in the Mushroom Club pool, blueprint quests, and the models
and artwork that go with them.

It has **two halves**, and both have to arrive or nothing shows up in game:

- **Artwork** — the 234 `.upk` packages in its `assets` folder.
- **Database changes** — the decal-pool entries and blueprint quests that make
  that artwork reachable. Without them the artwork sits in the game unused.

**Drag the pack's folder onto the window.** The manager recognises it and adds
both halves as one mod. That is the whole procedure.

You do not need to run the pack's own installer, and you should not. The
manager rebuilds `masters.db` from your mod list every time you save, so
anything written to the game from outside that list is replaced the next time
you tick something. That is why the two tools used to undo each other, and why
the content has to be a mod in the list to survive.

### Getting it

1. Go to **<https://letitdiemods.pages.dev/>**
2. Download **LET IT DIE Mod Manager v1.2**. That one download carries both
   the Crossover Content pack *and* the Colored PlayStation Buttons mod.
3. Open the ZIP and pull out the two folders inside it: **`crossover`** and
   **`buttons`**.
4. Drag each one onto the DB editor window **separately**. Each installs as its
   own mod.

You do not need Python, and you do not need to run anything in that download.
Only the artwork is taken out of it, so once both mods are installed you can
delete the ZIP and the extracted folders if you want the space back.

`buttons` is a different mod with different rules — it replaces artwork the
game checksums, so it also updates one value inside the executable. See
[Mods that need a change to the game executable](#mods-that-need-a-change-to-the-game-executable).
That is why the two are dragged in separately rather than as one drop.

**If you drag the whole extracted folder instead**, the manager looks one level
down, finds the crossover pack and installs that — then tells you the buttons
mod was in there too and to drop it on its own. Nothing is taken silently.

### Where the database changes come from

They ship with the manager, recorded from the pack's own installer: run against
a clean `masters.db`, the difference measured, and the recording replayed and
checked against the installer's own result before it was kept. For v3.74 that
is 476 added rows and 120 changed ones across 16 tables, and nothing deleted.

The 120 changed rows are not edits to the pack's content — they switch on
collab items the game already shipped but left hidden on PC.

The artwork itself is **not** redistributed here. It comes from the download
above, from its author.

### Version checking

A recording only fits the release it was made from, so the manager fingerprints
the pack before using one. If the pack's `catalog.json` does not match what was
recorded — a newer release, or a repackaged copy — it says so and installs the
artwork on its own instead of guessing. Writing rows that do not match the
artwork would not produce an error; it would produce invisible or
wrong-textured gear, which is worse.

If that happens, either wait for a manager update, or use
[the long way round](#the-long-way-round) below.

There is a second version involved: **the game's**. A recording is a set of
changes measured against one build of `masters.db`, and the game does get
patched. Each recording notes the build it came from, the mod carries that
forward, and validation compares it against your database — see
[Mods built for a different game version](#mods-built-for-a-different-game-version).

### Once it is in

Tick it and click **Save Mod List**. From there it behaves like any other mod:
it layers with the rest, it appears in the **In plain English** tab, it
conflict-checks against your other mods, and unticking it puts the database
back and takes the artwork out of the game.

Start the game. Decals come through the **Mushroom Club** draw pool — they are
not handed to you — and the blueprint quests appear at the normal quest
interface.

### Credit

The Crossover Content pack is **by S3er0i9ng**, not by me, and is not part of
this manager. The artwork is theirs and is not redistributed here — only a
recording of the database changes, with their permission.

Get the pack itself from **<https://letitdiemods.pages.dev/>**.

### The long way round

Only needed when the manager does not recognise your version of the pack. It
produces two mods instead of one — you let the pack's installer make the
database changes once, keep the result, and turn *that* into a mod.

The pack comes as two downloads, and for this route you may need both:

| You have                 | Download                                                                                                                               |
|--------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| **Python 3.11 or newer** | Just the **No-EXE** version (its folder ends in `Source-NoEXE`). It does both jobs.                                                    |
| **No Python**            | **Both.** The No-EXE version, for its files — dragging them in needs no Python — and the **EXE** version, to run the pack's installer. |

Unzip whichever you downloaded before going on.

#### Part 1 — the model files

1. Drag the **No-EXE folder** onto the DB editor window — the folder that has
   `assets` and `catalog.json` directly inside it.
2. In the info window:

   | Field           | What to put                                                  |
   |-----------------|--------------------------------------------------------------|
   | **Name**        | `Crossover Content Files` — it fills in the long folder name |
   | **Description** | `Models and artwork for the Crossover Content pack`          |
   | **Author**      | Optional — whoever made the pack                             |
   | **Version**     | The pack's version, e.g. `3.74`                              |

3. Click **Add mod**. A message pops up saying this pack also changes the
   database — that is Part 2, below.
4. **Tick `Crossover Content Files` and click Save Mod List.**

Do step 4 before Part 2. The pack's installer copies these same model files
into the game, and if it gets there first, the manager finds them already in
place and never keeps track of them — so turning the mod off later would leave
them behind. Saving first means the manager put them there, so it can take
them away again.

#### Part 2 — the database changes

The pack's installer does not work on a loose file: you point it at the game
folder, and it edits the real `masters.db` in place. So the trick is to back
up the vanilla database, let the installer edit the real one, keep a copy of
the edited file, and then put the vanilla one back.

**Close LET IT DIE** before you start.

1. **Make sure `masters.db` is vanilla.** In the DB editor, untick every
   *database* mod you have switched on — leave `Crossover Content Files`
   ticked — and click **Save Mod List**. Any mod left applied here would get
   baked into the Crossover mod you are about to make.
2. **Close the DB editor**, so it does not react while you swap files.
3. **Back up the vanilla database.** Open
   `...\steamapps\common\LET IT DIE\BrgGame\Content\`, copy `masters.db`,
   paste it on your Desktop and rename it `masters-vanilla.db`.
4. **Run the pack's installer.**
   - No-EXE: double-click `Launch-Source.cmd` in the No-EXE folder.
   - EXE: run the EXE download.

   It looks for the game on its own. If it cannot find it, pick the
   `LET IT DIE` folder — the one containing `BrgGame`, not `CookedPCConsole`.
   Click **Install / update**, then **Check installation**, then close it.
5. **Keep the edited database.** Back in `BrgGame\Content\`, copy the
   now-edited `masters.db`, paste it on your Desktop and rename it
   `masters-crossover.db`.
6. **Put the vanilla one back.** Copy `masters-vanilla.db` from your Desktop
   into `BrgGame\Content\`, rename it to `masters.db`, and say yes to replacing
   the file that is there.
7. **Open the DB editor** and drag `masters-crossover.db` onto its window.
8. In the info window:

   | Field                                    | What to put                                                         |
   |------------------------------------------|---------------------------------------------------------------------|
   | **Name**                                 | `Crossover Content Database` — it fills in "Masters Crossover"      |
   | **Description**                          | `Decals, blueprint quests and items for the Crossover Content pack` |
   | **Author**                               | Optional — whoever made the pack                                    |
   | **Version**                              | The pack's version, e.g. `3.74`                                     |
   | **Companion to game-files mod**          | Pick **`Crossover Content Files`**                                  |
   | **What to take from it**                 | Leave everything ticked                                             |
   | **Install each table as a separate mod** | Leave unticked                                                      |

   Picking the companion makes the database mod require the files mod, so you
   get a warning if you ever switch the database half on without the models it
   points at.
9. Click **Add mods**, tick **`Crossover Content Database`**, and click
   **Save Mod List**.

You can delete the two copies on your Desktop afterwards; the manager keeps its
own untouched copy as `masters.db.original`.

**From now on, switch it on and off in the DB editor**, not with the pack's
own installer. Untick both mods and save, and the database goes back to how it
was and the model files are taken out of the game.

### Recording a new release (maintainers)

When the pack puts out a new version, `tools/build_crossover_recipe.py` records
it — it runs the pack's own installer against the vanilla database, diffs the
result, verifies the recording reproduces that result exactly, and writes the
recipe plus its fingerprint into `lid_db_manager/recipes/`:

```
py -3 tools/build_crossover_recipe.py --pack "C:\...\LetItDieCrossoverContent-v3.75-Source-NoEXE"
```

Nothing is written if the verification fails. Players never run this.

## Mods that need a change to the game executable

Some artwork cannot be replaced by copying a file over it, because the game
checks. `BrgGame-Steam.exe` carries a SHA-1 for most of its own data — on the
build this was written against, **7,678 of the 7,882 packages on disk**, plus
221 `.ini` and 139 `.usf` files. Replace a listed file and the game refuses it.

Most artwork mods never run into this. The 204 packages the table does *not*
list are equipment added after the table was built, which is why the Crossover
Content pack installs without any of this — 201 of its files are unlisted, and
the 33 that are listed it ships byte-identical to the originals.

The **Colored PlayStation Buttons** mod, also **by S3er0i9ng**
(<https://letitdiemods.pages.dev/>), is the other case. It replaces
`UI_ButtonGuide_STM_SF.upk`, which *is* listed, so the game has to be told the
new hash or the replacement does nothing. As with the content pack, the mod
itself is not redistributed here — download it from them.

### Getting it

It is in the same download as the Crossover Content pack: **LET IT DIE Mod
Manager v1.2** from <https://letitdiemods.pages.dev/>. Open the ZIP and pull out
the **`buttons`** folder.

### How the manager handles it

Drop that folder on the window. It is recognised, and goes in as one mod
holding both halves: the replacement package, and the one hash the game keeps
for it.

Unticking it puts the executable back byte for byte.

### What actually changes

Twenty bytes, inside a table of file hashes. No program code.

That is not a promise, it is a check. The executable has a code section with a
fingerprint of its own, and every edit is verified before it is allowed to
count:

- the file's length is unchanged,
- every byte outside those twenty is identical,
- the code section hashes to exactly what it did before,
- and the entry now holds the replacement package's real hash.

If any of those fail, nothing is written. The same code refuses a package the
table does not list, a package listed twice (228 names really are), a hash that
is not what the recording expected — which means a different game build, or an
executable something else already changed — and a malformed recording.

### Only recordings that ship with the manager

This is deliberately not a thing mods can do. A mod cannot name a package and a
pair of hashes; it can only name a recording that ships here:

```json
{ "type": "exe_checksum_entry", "recipe": "exe-buttons-1.2" }
```

An unknown name does not load:

> `'exe-made-up' is not a recording this manager ships, so it will not be
> carried out. Changes to the game executable are limited to recordings that
> ship with the manager.`

A mod that tries to supply its own `package`, `checksum_before`,
`checksum_after` or `target` alongside the name is refused too. Everything
about the change comes from the recording, which is the point of it.

Each recording lives in `lid_db_manager/recipes/` as two committed files: the
recording itself, and the exact `mod.json` that gets installed. Neither is
generated at install time — what runs on a player's machine is a file in this
repository that can be read and diffed.

### Adding a recording (maintainers)

Recording one is deliberately a change to this repository. `tools/build_buttons_recipe.py`
reads the mod folder and a real game executable, checks the mod's own manifest
against what the executable actually holds, performs the edit in memory to prove
it works and is reversible, and only then writes the two files:

```
py -3 tools/build_buttons_recipe.py --mod "C:\...\buttons" --game "E:\...\LET IT DIE"
```

The executable is opened read-only and never written. Nothing is recorded if
any check fails. Then look both files over and commit them — that is what makes
the recording vetted, and it is the only step that cannot be automated.

Players never run this.

## Editing a mod

**Tools → Edit mod details**, or F2 with a mod selected. Name, description,
author, version and the readme, all in the window — no hunting for the folder
and no text editor.

Renaming moves the mod's folder, and takes its place in the load order and its
undo history with it, so nothing breaks. A mod that is only a bare `.sql` file
gets a proper `mod.json` written the first time you give it a name, which is
how it stops showing as *(unknown — .sql only)*.

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

## Mods built for a different game version

A mod made by diffing a database is a set of changes measured against **one
build of the game**. LET IT DIE gets patched, and when it does, most of those
changes still land exactly as intended — rows are addressed by name, not by
position, and a patch rarely touches the same ones.

But when one *doesn't* fit, nothing fails. The mod applies, the save succeeds,
and something is quietly wrong in game. That is the worst shape a problem can
take, so the manager says something instead of leaving you to find out.

### How it knows

`masters.db` carries its own build number:

```
master_const_str.TITLE_VERSION  ->  "5.0.3.0.0 - 1.87"
```

A mod can record the build it was made against in its `mod.json`:

```json
{ "game_version": "5.0.3.0.0 - 1.87" }
```

When both are present and they differ, validation adds a warning naming each:

> built for game 5.0.3.0.0 - 1.87, but your database is 5.0.2.0.0 - 1.86. It
> will still apply, and usually that is fine — but if the update changed
> anything this mod touches, the result will be wrong in game rather than
> reported here.

### A warning, not a refusal

Deliberately. A mod built on the previous build usually works perfectly, and
refusing would block mods that are fine. The point is that the mismatch stops
being invisible, not that it stops being allowed.

Two things stay quiet rather than guessing: a mod that records no build, and a
database that does not report one. Neither is treated as a mismatch.

### If you write mods

Add `game_version` to your `mod.json` when you build one by diffing — the value
is whatever your database's `TITLE_VERSION` says. It costs nothing, and it means
anyone on a later build gets told rather than surprised.

Mods that ship with the manager and are recorded from a content pack get it
filled in automatically, from the baseline the recording was taken against.

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

## Seeing what a mod does, in plain English

Select a mod and open the **In plain English** tab. Instead of rows and column
names, it says what the mod changes:

```
Buffalo Bank (Kill Bank) upgrades
The store in your Waiting Room that holds your Kill Coins.
 • Doubles the most Kill Coins it can hold, for levels 1-99.
     for example, level 1: 50,000 KC → 100,000 KC
```

Most of it comes from the game itself. Skills, quests and fighter types are
named from the game's own text, so a mod touching `SKL_EXPUP_02` reads as
**Nitro Boost**. Where the game writes its own description — *"Increases EXP
gained by 40%"* — the tab shows that description with the mod's new number
filled in, which is the only honest way to describe a value that means
something different on every row.

Long lists are summarised rather than printed: 99 changed levels read as
*"levels 1-99"*, and a mod that renames every floor reads as *"191 lines of
text, in 8 languages"* rather than 1,528 rows.

**It never guesses.** A column nobody has described yet is shown under its real
name, with a note saying so. Some columns are described as *unused* — vanilla
leaves them at zero on every single row — and ten whole tables are marked as
left over from when the game was online, so the tab tells you editing them will
not do anything rather than explaining them.

It is careful about the difference between those ten and a table that is merely
*empty*. The ten are named nowhere in the game's own executable, which is good
evidence they are dead. Seven others simply have no rows in vanilla, which
proves nothing about whether the game would read them — so those say exactly
that, and leave the question open, rather than telling you not to bother.

**All 221 tables are now described.** Every table in `masters.db` has a title,
an explanation of what it is for, and plain-English meanings for the columns a
mod is likely to touch. 155 are covered in full; the other 66 have their main
columns described and the rarer ones still shown under their real names.

| Area         | What is covered                                                                                      |
|--------------|------------------------------------------------------------------------------------------------------|
| Waiting Room | Buffalo Bank, SPLithium Tank, Freezer, Jail, decorations                                             |
| Quests       | quests, rewards, categories, what each one checks for and the limits it enforces                     |
| Gear         | weapons and armour, crafting and upgrading, ranks, rage moves, damage and knockback per attack       |
| Items        | items, mushrooms and what eating them does, beasts, ammunition                                       |
| Decals       | decals, the draw pool and its odds, caps on stacking, mushrooms that unlock them                     |
| Fighters     | tiers, level EXP, stats by level, uncapping, weapon mastery, bodies, names, expert points            |
| Death Metro  | ranks and payouts, the players you raid, team wars, base alarms, abduction                           |
| The Hunter   | what a hunter brings back, by floor, by hour and by luck                                             |
| Enemies      | Haters, Screamers, mid-bosses, stage bosses, the Four Forcemen, Jackals, small enemies, beasts       |
| Stages       | the seven areas, their rooms and every spot things can be placed in                                  |
| Floors       | what drops and at what level, materials, hazards, how floors join the elevator, the top of the Tower |
| Pools        | mushrooms by season, beasts, items, treasure boxes, Mystery Bags, Death Boxes                        |
| Rewards      | daily login bonuses, quest rewards, stamps, magazines, vouchers, Steam DLC                           |
| Shops        | prices, where shops appear, the vending machine and its schedule                                     |
| Presentation | game text, subtitles, the radio's tracks and channels, posters, the credits                          |
| Left over    | ten tables the offline game never reads — it says so instead of describing them                      |

### Describing more of the database

The descriptions live in `lid_db_manager/explain_data.py`, one entry per table.
You can also add your own without touching the program: put a
`table-notes.json` next to your `mods` folder, in the same shape:

```json
{
  "master_tdm_rank": {
    "title": "Death Metro ranks",
    "word": "rank",
    "columns": { "point_min": ["the points needed to reach it", "pts"] }
  }
}
```

It is merged over the built-in descriptions when the manager starts. A broken
file is ignored rather than fatal. If you are unsure what a column does, write
"(not confirmed)" into the wording — saying so is better than a confident guess.

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

## Building a mod without writing any SQL

**Tools → Build a mod** (Ctrl+B) opens an editor over a clean copy of the
database. Pick a heading, pick a table, change the numbers, press **Save as
mod**. No SQL, no table names to memorise, no text editor.

The headings are the parts people actually ask about:

|-------------------------------------|--------------------------------------------------------------------------|
|-------------------------------------|--------------------------------------------------------------------------|
| Weapons & Armour                    | craft and upgrade costs, stats, rank requirements                        |
| Fighters                            | tier prices, level caps, Death Bag size, decal slots                     |
| Items · Mushrooms · Beasts · Decals | prices, effects, where they appear, draw odds                            |
| Vending machine                     | what it sells, for how much, on which day                                |
| Quests · Rewards                    | what quests ask, what they pay, login bonuses, Mystery Bags              |
| Enemies                             | Screamers, small enemies, mid-bosses, the Four Forcemen, Haters, Jackals |
| Tokyo Death Metro                   | ranks and payouts, the players you raid, team wars                       |
| Everything else                     | the other 170-odd tables, for when you know what you want                |

### Two ways to look at anything

Because one shape doesn't fit both kinds of table:

- **As a list** — pick a weapon from the list, see everything about it on a
  form. Right for tables where each row is a *thing*, and the only readable
  option for the wide ones: a weapon has 90 columns, and no grid that wide is
  worth looking at.
- **As a table** — a grid, for the tables where each row is a *number in a
  series*, like the bank's 99 levels, where you want to see the whole curve.

It picks for you based on the table's shape, and the dropdown switches.

Every column is labelled with what it *means* rather than its database name —
"the cost to unlock it (KC)", not `price` — and the described ones are shown
first, so the handful worth touching aren't buried behind ninety that aren't.
Rows are named the same way: **All-rounder, grade 2**, not `BAL / 2 / 0`.

### Item artwork

The builder can show a picture beside each weapon, decal, material and
blueprint. **None of it ships with this program.** The game's artwork belongs to
Grasshopper Manufacture, not to this project and not to whoever extracted it, so
there is none in this repository and none in any release.

What it does instead: **Tools -> Set item artwork folder...** points it at a
copy you already have on your own machine. If the folder carries an
`icon_map.json` keyed by the game's own ids it uses that; otherwise it matches
filenames, either the id (`pt_arm_wp001_002.png`) or the name
(`battle_machete.png`). Blueprints borrow the picture of the gear they make.

Leave it unset and everything works exactly as before, with no pictures.

Pictures are scaled to 40px once and kept in `icon-cache/` so lists stay quick -
about 2 KB each. That folder is yours, is never uploaded anywhere, and can be
deleted at any time.

### Stocking the vending machine

Open the vending machine and there's an **Add things to the machine...** button.
It opens a catalogue of everything the machine can sell — search it, tick what
you want, choose a day, and optionally set a price.

Three things it knows that you'd otherwise have to work out yourself:

- **Blueprints have no names.** All 1,899 of them are called "RMAP" in the
  game's text, so each is shown as *the weapon or armour it makes* — "Battle
  Machete — blueprint" — which is what you'd actually search for. The
  unidentified variants are marked as such.
- **The day of the week is the tab.** `MON` through `SUN`, plus an always-in-
  stock tab and the recycle tab, spelled out in words.
- **The machine carries no price of its own.** Every price and discount column
  is zero on all 315 vanilla rows, so what it charges is the *item's* price —
  which is what setting a price here changes, everywhere that item is sold. The
  window says so before you do it.

It also copies the settings of whatever tab you're adding to, rather than asking
you to pick a "currency type" whose meaning isn't recorded anywhere.

Two things make bulk edits painless. **Set every shown row to...** applies one
value to everything currently listed, and **Multiply by...** scales it — so
"every revive costs 1 KC" or "double the bank at every level" is one action, not
ninety-nine edits. Search first to narrow what "shown" means.

### What it will not let you do

- **Change a key column.** That would move the row rather than edit it.
- **Type words into a number.** It says so, using the column's real meaning.
- **Turn a whole number into a decimal.** Multiplying an integer column rounds,
  because writing `75000.5` where the game expects a whole number is how you get
  a crash instead of a mod.
- **Wreck a list column.** `skill_slots` holds `1,2,3` — three open decal slots,
  not the number three. Those columns are edited as text and say so.

### It cannot produce a broken mod

The editor never writes a mod file. It edits a scratch copy of your
`masters.db.original`, and when you save, that copy goes through **exactly the
same import path** as a modded database someone sends you. So it inherits
validation, snapshots, load order, switchable parts and untick-to-undo — there
is no second code path that could get any of it wrong.

Your real database is never touched while you edit, and the scratch copy is
deleted when you close the window. What you get is an ordinary mod, sitting in
the list **switched off**, with its own diff and plain-English tab to check
before you tick it.

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
