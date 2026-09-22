## VirusTotal scan of the current code of BETA V0.7.0
https://www.virustotal.com/gui/url/63368c8eb96033d99a7a4bc4788535a4810c7ddc300b740e84036b356da2371e

## Interactive Readme Site
https://nicomullin.github.io/LiD-DB-Editor/

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
- **Takes over a database that was already modded.** On the first run it
  compares your `masters.db` with a clean copy of the same game build, says
  which of your mods it recognises in there, and offers to keep the rest as a
  mod of your own — so you do not have to start from a fresh file to start
  using it.
- **Takes mods however they arrive.** Drag a `.sql`, a mod folder or a `.zip`
  onto the window. Hand it somebody else's already-modded `masters.db` and it
  works out the difference and turns that into a mod you can switch off again.
- **Handles game files too.** Mods that ship new models and artwork as `.upk`
  files are copied into the game while switched on and taken back out when
  switched off.
- **Comes with the Crossover Content pack, Colored PlayStation Buttons and the
  Tower Static radio station.** All by S3er0i9ng, included with their permission
  and ready to tick. Artwork
  and the database changes that make it reachable switch on and off together —
  no running an installer, no swapping `masters.db` by hand.
- **Knows which artwork the game checks.** The game keeps a hash for most of
  its own packages and refuses a replacement at startup, naming the file and
  nothing else. A mod that replaces one of those says so, and the manager
  refuses it with an explanation instead of letting you meet that error box.
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
mod can change the game's code. Nothing that ships with the manager writes to
`BrgGame-Steam.exe` at all: a mod that replaces artwork the game checks is
refused with an explanation instead. The machinery for changing one file hash
inside the executable is still in the code — twenty bytes in a data table, no
code, proven byte-for-byte and reversible, and only ever from a recording
committed here by hand, never at a mod's request — but no shipped mod uses it.
See
[Mods that replace artwork the game checks](#mods-that-replace-artwork-the-game-checks).

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
   **Use a clean one** — see [Point it at a clean masters.db](#point-it-at-a-clean-mastersdb).
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

## Already modded? It can take that over

If your `masters.db` has been modded before the manager ever saw it — by another
tool, by a pack's own installer, or by a copy of this one you have since
reinstalled — you do not have to throw it away and start clean.

On the first run it compares your database with a **clean copy of the same game
build**, which ships with the manager, and tells you what is in there:

```
This database is not stock.
1,204 changed value(s), 476 added row(s) across 17 table(s) in all;
2 mod(s) recognised; 63 change(s) belonging to no mod

Mods it found in there
  [x] LET IT DIE Crossover Content v3.79   1,141 of 1,141 database change(s), 287 of 287 game file(s)
  [x] Weapon Durability   x2               385 of 385 database change(s)
  [ ] Armor Durability    x5               120 of 979 database change(s)

Changes that match no mod
  [x] Keep these as a mod I can switch off      [ My existing changes        ]
```

- **Recognised mods** are matched on the values themselves, not on row counts,
  so a mod that only half-applied reads as *partly* there and is left unticked
  rather than quietly claimed.
- **Mods with a value are found at whatever value they have.** Weapon Durability
  at x7 is recognised as Weapon Durability at x7, not as a pile of changes
  nobody owns — and the rebuild keeps it at x7. The value comes from the
  database's own note when there is one (see *Saving your mod list*), otherwise
  from what you have chosen, otherwise it is worked out from the numbers in the
  database. Whichever it is, every value has to match before the mod counts. If
  no single setting explains them all, usually the mod plus a hand edit, the mod
  is listed but left unticked and those changes stay under *Changes that match
  no mod*, so nothing is lost.
- **A mod the database lists that you do not have installed** is named, and its
  changes are counted as belonging to no mod.
- **Changes no mod accounts for** become an ordinary mod with the name you give
  it, holding a switch per table — you can untick the parts you do not want,
  now or later, exactly like any other mod.
- **Then it rebuilds.** Your database is put back to the clean copy and the
  things you ticked are applied to it in the normal way. That is what makes
  everything afterwards honest: each mod has a snapshot, so each one can be
  switched off again. Your current file is backed up first, and
  `masters.db.original` becomes a genuinely stock copy instead of a copy of
  your modded one.
- **Content packs are put at the top of the load order.** Top applies first and
  bottom wins, so a pack that rewrites and adds rows across a great many tables
  has to go above the smaller mods — otherwise a tweak to a table the pack also
  writes is applied first, buried by the pack, and reads as if it simply had not
  worked. Anything that ships game files counts as a pack. You can reorder
  afterwards like any other mod.

Say no and nothing is written. It is offered once; **Tools ▸ Scan my database
for mods already in it...** runs it again whenever you like.

### The clean copies it compares against

They live in `LiD Vanilla DB/<build>/masters.db`, and the build number is read
out of each file rather than trusted from the folder name. The manager picks the
one matching your database. **Tools ▸ Clean database to compare against...**
shows what is available and lets you pin one.

If you run from source, anything you drop in that folder is picked up — so an
older build, or a newer one before the manager ships it, works with no code
change.

**If nothing matches your game build**, it does not diff against the nearest
build instead: after a game patch that would read the developers' own changes as
if they were a mod, and offer to bottle them up and re-apply them over a later
version.

#### After a game update it keeps one itself

A patch means a build nobody has a clean copy of yet — but the `masters.db`
Steam has just written *is* that clean copy, mods and all wiped out. So the
manager keeps it, and you do nothing. It only does that when all of this holds:

- **Its own note is not in the file.** Every save writes a list of the mods it
  applied into the database. If that note is there, the file is not stock.
- **The file is from the same write as the rest of the game.** Steam writes the
  whole update in one go, so the game's packages all share a timestamp. A
  database changed afterwards — by this manager, by another tool, by hand — no
  longer matches, and is not kept.
- **What changed looks like a patch, not like mods.** Compared with the newest
  clean copy it has, the file may only differ in tables real updates have been
  seen to touch. Anything else, and it is left alone.

The copy lands in `LiD Vanilla DB/<build>/masters.db` and is named after the
build, and a line in the log says so.

**If it cannot tell, it asks you.** You know whether you have modded that file
yet. Say yes and it is kept as the clean copy for that build; say no and nothing
happens, and you are not asked about that build again. When you are not sure,
say no — Steam's *Verify integrity of game files* puts an untouched copy back,
and then the answer is yes. A copy kept this way can always be deleted: it is
one folder, and removing it puts everything back as it was.

You can also do it by hand at any time: make a folder in `LiD Vanilla DB` and
put an untouched `masters.db` in it. The folder name is only a label — the
build is read out of the file.

## The mod list

Each mod is one row, with an arrow to fold it open when you want its
description, its warnings and its parts. They start folded — **Tools ▸ Expand
all mods** and **Collapse all mods** do the lot at once. A mod with a problem
opens itself, because a warning nobody can see is no warning.

**Right-click a mod** for the things that act on it: enable or disable it, edit
its details, open its folder, move it up or down the load order, revert it, or
delete it. Deleting one that is applied offers to put its rows back first.

Ticking a mod keeps your place in the list rather than jumping back to the top,
which matters once you have more mods than fit on screen.

### Choosing a mod's value

Select a mod and open the **Configuration** tab on the right. Every value the
mod lets you choose has its own box there, with its limits, its default and
what it does:

```
Weapon Durability
 Details | Configuration | In plain English | Diff preview | Readme

 Durability multiplier
 [ x2          ] [Default]
 x1 to x100  ·  default x2
 Whole numbers only - these are whole-number columns in the game.
```

Type a number or use the arrows, then press **Enter** or click away. **Save Mod
List** applies it, the same as ticking a box. **Default** puts one back, and a
mod with several values also has **Put all back to defaults**. Right-click a mod
and **Change values...** takes you straight there.

A mod that comes down to a single number shows it in its name in the list —
`Weapon Durability   x2` — so you can see what it is set to without opening
anything.

The number is checked against the mod's own limits before it goes anywhere, and
only ever reaches the database as a number. Switching the mod off still puts
the stock values back, whatever you had it set to. Your choices are kept in
`state.json`, not in the mod folder, so updating a mod does not reset them.

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

Fifteen, in `mods/`, each with its own `readme.md`: twelve tweaks of mine, one
that changes a number inside a game package, and two larger mods by S3er0i9ng
included with their permission ([below](#by-s3er0i9ng)).

Every one of mine comes down to a number, and **the number is yours to choose**
in the mod's **Configuration** tab — see
[Choosing a mod's value](#choosing-a-mods-value). The defaults below are what you get if you never
touch it.

### Costs and rewards

| Mod                   | What it does                                                          | Default   | You can choose    |
|-----------------------|-----------------------------------------------------------------------|-----------|-------------------|
| `revive-cost`         | Every grade's revive costs the same price                             | 1 KC      | 1 to 1,000,000 KC |
| `fighter-tier-prices` | Every fighter tier unlock costs the same price                        | 1 KC      | 1 to 1,000,000 KC |
| `decal-draw-price`    | A draw from the Mushroom Club decal pool costs this instead of 50,000 | 10,000 KC | 1 to 1,000,000 KC |
| `nitro-boost-exp`     | Nitro Boost and Turbo-charged Engine EXP bonus, descriptions to match | 100,000%  | 1 to 1,000,000%   |
| `tdm-rewards`         | Every Kill Coin and SP payout from Tokyo Death Metro, multiplied      | x2        | x1 to x100        |

### Space

| Mod                | What it does                                     | Default | You can choose  |
|--------------------|--------------------------------------------------|---------|-----------------|
| `bank-limit`       | Both banks hold more, all 99 levels              | x10     | x1 to x100      |
| `reward-box-limit` | The reward box holds this many instead of 50     | 250     | 50 to 9,999     |
| `storage-limit`    | The Coin Locker expands to this instead of 2,000 | 10,000  | 2,000 to 99,999 |

### Durability and ammo

| Mod                 | What it does                             | Default | You can choose | Rows |
|---------------------|------------------------------------------|---------|----------------|------|
| `weapon-durability` | Every weapon lasts longer                | x2      | x1 to x100     | 385  |
| `armor-durability`  | Every piece of armour lasts longer       | x2      | x1 to x100     | 979  |
| `weapon-ammo`       | Every gun carries more spare ammo        | x2      | x1 to x100     | 117  |
| `weapon-magazine`   | Every gun holds more rounds per magazine | x2      | x1 to x100     | 160  |

Multipliers are **whole numbers**. Those columns hold whole numbers in the
game, and writing x2.5 into them is how you get a crash instead of a mod.

They are also marked `"apply": "diff"` — measured against an untouched copy of
the database every time. Saving twice can never turn x2 into x4, and changing
x2 to x5 gives you x5, not x10.

Storage and the reward box cannot go **below** stock, so nothing you already
have stored is stranded.

Two weapon families have a magazine but no reserve ammo at all — rocket
launchers, flame wands, the Red Hot Iron line. Everything they will ever fire
sits in the magazine, so `weapon-ammo` does nothing for them and
`weapon-magazine` multiplies their entire supply.

Two caveats worth knowing before you enable them:

- `revive-cost` — you are charged the price you chose, but the price on the sign
  held up in-game is part of a **texture**, not the database, so it still shows
  the old number. Matching it means replacing that texture yourself.
- `nitro-boost-exp` — its description rewrite uses your number, in English,
  German, Spanish, French, Italian and Portuguese. Japanese, Chinese and Korean
  keep the stock wording. That half is a separate patch inside the mod, so you
  can untick it and keep the number change on its own.

**Coming from an older version?** These used to be separate mods for each
strength — `weapon-durability-2x` and `-5x`, `tdm-rewards-2x`, `-5x` and `-10x`,
`decal-cost-25k`, and so on. If you had one switched on, the new version opens
with its replacement switched on at the same value, in the same place in the
load order, and still able to be switched off. The old folders are moved into
`mods/_retired/`, not deleted.

### Not in the database

| Mod             | What it does                                            | Default | You can choose             |
|-----------------|---------------------------------------------------------|---------|----------------------------|
| `instant-drops` | Kills drop their reward as the enemy dies, not 2s later | 0       | 0 to 20 tenths of a second |

The wait is not a database value: it is compiled into the script bytecode
inside `BrgGame.upk`, so this mod changes that one byte instead. **The game's
file check has to be off for `BrgGame.upk`** - see
[Mods that replace artwork the game checks](#mods-that-replace-artwork-the-game-checks).
Switching the mod off puts the game's own package back, byte for byte.

The finding is from **Claudia-diva's LID-Patches** (MIT), where it is the
`dropdelay` patch.

### By S3er0i9ng

| Mod                                  | What it does                                                                                  |
|--------------------------------------|-----------------------------------------------------------------------------------------------|
| `LET IT DIE Crossover Content v3.79` | Restores cut crossover gear: Mushroom Club decals, blueprint quests, and 287 artwork packages |
| `Colored PlayStation Buttons v1.4`   | Colored PlayStation button prompts. Needs the game's file check off for that one package      |
| `Tower Static Radio`                 | A new radio station, Tower Static, on channel 501 with four tracks                            |

All three are included with their author's permission; the originals are at
<https://letitdiemods.pages.dev/>. See
[The Crossover Content pack](#the-crossover-content-pack) and
[Mods that replace artwork the game checks](#mods-that-replace-artwork-the-game-checks).

Ticking the Crossover pack alongside the durability, ammo or magazine mods, or
Nitro Boost, shows a "both write table" warning. The manager can only compare
the pack's SQL a whole table at a time; they share a table but no rows — the
pack switches hidden gear on and adds new rows, and those mods change other
columns and other text.

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
unticking a whole mod — see [Switching a mod off](#switching-a-mod-off).

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

## Mods made for TFC Installer

Many LET IT DIE mods — GLaDOS, Tommy Gun, weapon and character reskins — are
distributed for **TFC Installer** by FCH823. Those are not loose `.upk` files:
the folder holds *patches* to the game's own packages, and a texture pack the
game reads its artwork out of.

The manager installs them itself. **Drag the mod's top folder onto the window**
— the one with `GameProfile.xml` in it, exactly as it was downloaded:

```
Glados/                   <- drag THIS folder onto the window
├── GameProfile.xml
├── Game/BrgGame/CookedPCConsole/*.upk.PackagePatch
└── TexturePack/          <- Texture2D_0.tfc, LocalMips_0.tfc, a .TFCMapping
```

Nothing has to be taken out of the folder first, and TFC Installer itself is not
needed. Ticking the mod rebuilds each affected package from the game's own copy
and puts the textures in; unticking it puts the game's files back.

**The game's file check has to be off for the packages it rebuilds.** The
manager says which ones before it writes anything — see
[Mods that replace artwork the game checks](#mods-that-replace-artwork-the-game-checks).

**It is slower than a normal mod.** Packages are rebuilt from scratch on every
save, and some are tens of megabytes, so a save can take a minute. The progress
window names each package as it goes.

Only the parts of a package that actually change are rewritten: Glados changes
three of the sixty-five chunks `Cafe_KIS.upk` is stored in, and the other
sixty-two are copied across exactly as the game shipped them. So the packages
stay near their original size - for Glados and Tommy Gun together, 186 MB
instead of the 295 MB an all-at-once rebuild would write.

**Already installed that mod with TFC Installer?** That is fine. The manager
rebuilds from an untouched copy of each package — TFC Installer keeps one, and
the manager only trusts it if its checksum matches the one the game itself
lists. If no untouched copy can be found anywhere, it says so and stops rather
than build on top of somebody else's changes. Steam's *Verify integrity of game
files* is the way out of that.

**Texture packs do not pile up.** The pack's `.tfc` goes in under the first free
number, and a reinstall reuses the identical one already there instead of
adding another copy.

Mods that also carry loose game files, `.ini` patches or files for folders
outside the game are refused rather than half-installed — use TFC Installer for
those. The `.PackagePatch` format and TFC Installer are by **FCH823**
(with Wastelander121); the reading of that format here is used with their
permission.

## The Crossover Content pack

The community **LET IT DIE Crossover Content** pack restores cut crossover
content — decals in the Mushroom Club pool, blueprint quests, and the models
and artwork that go with them.

It has **two halves**, and both have to arrive or nothing shows up in game:

- **Artwork** — the 287 `.upk` packages in its `assets` folder.
- **Database changes** — the decal-pool entries and blueprint quests that make
  that artwork reachable. Without them the artwork sits in the game unused.

**It comes with the manager.** Tick `LET IT DIE Crossover Content v3.79` in
the mod list and click **Save Mod List**. That is the whole procedure.

You do not need to run the pack's own installer, and you should not. The
manager rebuilds `masters.db` from your mod list every time you save, so
anything written to the game from outside that list is replaced the next time
you tick something. That is why the two tools used to undo each other, and why
the content has to be a mod in the list to survive.

### Updates

The manager ships v3.79, made for game version 1.88. When the pack gets an
update, the manager has to be updated to include it — a new release of this
program will carry it.

If you had an earlier version applied, the first **Save Mod List** after
updating the manager takes the old version off in full and puts the new one on.
Nothing needs unticking first.

### Where the database changes come from

They ship with the manager, recorded from the pack's own installer: run against
a clean `masters.db`, the difference measured, and the recording replayed and
checked against the installer's own result before it was kept. For v3.74 that
is 476 added rows and 120 changed ones across 16 tables, and nothing deleted.

The 120 changed rows are not edits to the pack's content — they switch on
collab items the game already shipped but left hidden on PC.

The artwork is S3er0i9ng's own, copied unchanged from their v3.79 release.

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

The Crossover Content pack is **by S3er0i9ng**, not by me. It is included with
the manager — artwork and a recording of its database changes — with their
permission. The artwork and the pack remain theirs.

Their releases are at **<https://letitdiemods.pages.dev/>**.

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
py -3 tools/build_crossover_recipe.py --pack "C:\...\crossover" --vanilla "LiD Vanilla DB\5.0.4.0\masters.db"
```

Nothing is written if the verification fails. Players never run this.

## Mods that replace artwork the game checks

Some artwork cannot be replaced by copying a file over it, because the game
checks. `BrgGame-Steam.exe` carries a SHA-1 for most of its own data — on the
build this was written against, **7,678 of the 7,882 packages on disk**, plus
221 `.ini` and 139 `.usf` files. Replace a listed file and the game stops at
startup with an error box naming the package, before the intro plays.

Most artwork mods never run into this. The 251 files the list does *not* name
are mostly equipment added after the list was built, which is why the Crossover
Content pack installs with none of this — most of its files are unnamed there,
and the 45 that are named it ships byte-identical to the originals.

The **Colored PlayStation Buttons** mod, also **by S3er0i9ng**
(<https://letitdiemods.pages.dev/>), is the other case. It replaces
`UI_ButtonGuide_STM_SF.upk`, which *is* checked. Like the content pack, it is
included with the manager with their permission.

### What the manager does about it

It does not change your game's executable. A mod that replaces a checked file
says so in its `mod.json`:

```json
"requires_check_off": ["UI_ButtonGuide_STM_SF.upk"]
```

When you tick that mod, the manager reads the list of names out of the
executable in your game folder and looks for those files. If the game still
checks one, the mod does not validate and nothing is written:

> this mod replaces UI_ButtonGuide_STM_SF.upk, which your game still checks.
> Applied as it is, the game would refuse that file at startup with an error
> naming it, before the intro. Switch the game's file check off for it first,
> then apply this again.

That is the whole feature. It is a refusal with a reason, in place of an error
box in the game that names a package and tells you nothing else.

Once the check is off for that file the mod applies like any other: the package
is copied in while it is ticked, and the game's own copy is put back when you
untick it.

If the manager cannot read your executable — a loose copy of `masters.db`
somewhere, or a folder layout it does not recognise — it says nothing and lets
the mod apply. Refusing a mod on a guess would be worse than the error box.

### Switching the check off

That is a separate tool, not part of this one, and deliberately so: this manager
writes `masters.db` and copies game files, and it does not touch
`BrgGame-Steam.exe` at all.

Briefly, for context: the list inside the executable is a set of file names with
the hash expected for each, and the game only checks a file whose name it finds
there. Change one character of a name and the lookup misses, so that file is
treated like the 251 that were never listed. No code is changed and no hash is
changed.

### When the game updates

A game update replaces the executable, so the check comes back on for every file
and the manager will start refusing these mods again until the check is switched
off on the new build. Two more things follow, and the manager handles both:

- **Every game file is checked before it is copied.** A file the executable in
  your game folder would refuse is left out, the game's own copy is kept, and
  the log names the mod and the files.
- **Copies kept from before the update are never put back.** The packages the
  manager kept as the way back belong to the old build. When a mod is switched
  off or updated after a game update, the manager puts this build's own files
  back to stock instead of restoring the old ones.

There is one thing worth knowing that the manager cannot warn you about. With
the check off, a mod built for an older build of the game loads without
complaint. If an update changed a package that a mod replaces, ticking that mod
quietly puts the old version of that content back. Look for a release of the
mod built for your game version — see
[Mods built for a different game version](#mods-built-for-a-different-game-version).

### Writing a mod that needs this

`requires_check_off` takes a list of file names, matched on the name alone,
ignoring case and any folder in front of it:

```json
{
  "id": "my reskin",
  "requires_check_off": ["WP_AssaultRifle3102_SF.upk"],
  "patches": [
    { "type": "asset_file", "source": "assets", "target": "BrgGame/CookedPCConsole" }
  ]
}
```

Name only the files you actually replace that the game checks. A mod that names
nothing is never blocked, and a mod that names a file the game does not check is
not blocked either — so naming a file that turns out to be unlisted costs
nothing.

### The older way: changing one hash in the executable

Earlier releases handled the buttons mod differently. Rather than the game being
told to stop checking the file, the manager wrote the replacement's hash into
that one entry inside the executable — twenty bytes in a data table, no code,
proven byte-for-byte and fully reversible, and only ever from a recording
committed to this repository by hand.

That machinery is still here, as the `exe_checksum_entry` patch type and the
recordings in `lid_db_manager/recipes/`, and it is still the only way to make
such a mod work on an executable that has not been touched at all. No mod that
ships with the manager uses it now.

It is deliberately not a thing mods can ask for. A mod cannot name a package and
a pair of hashes; it can only name a recording that ships here, and an unknown
name does not load:

```json
{ "type": "exe_checksum_entry", "recipe": "exe-buttons-1.4" }
```

Each recording lives in `lid_db_manager/recipes/` as two committed files: the
recording itself, and the exact `mod.json` that gets installed. Neither is
generated at install time — what runs on a player's machine is a file in this
repository that can be read and diffed. `tools/build_buttons_recipe.py` writes
one, from a mod folder and a real game executable, opening the executable
read-only and recording nothing if any check fails. Players never run this.

## Changing a number inside a package

Not everything worth modding is in `masters.db`. Some numbers are compiled into
the UnrealScript bytecode inside a package - the wait before a dead enemy drops
its reward is one. The `package_bytes` patch type changes those:

```json
{
  "type": "package_bytes",
  "target": "BrgGame/CookedPCConsole/BrgGame.upk",
  "edits": [
    {
      "name": "Item Drop Delay Time",
      "find": [{"hex": "2c061f"}, {"text": "Item Drop Delay Time"}, {"hex": "00282c"}],
      "follows": [{"hex": "251ecdcccc3d16"}],
      "write": {"type": "u8", "value": 0}
    }
  ]
}
```

- **`find`** is the bytes immediately *before* the value, and **`follows`** the
  bytes immediately after it. Matching on what surrounds the value rather than
  on the value itself means the site is still found once it has been changed -
  which is what lets the mod be re-applied, or read back, without putting the
  file back first. A signature that appears twice in one chunk is refused
  rather than guessed at.
- **`write`** is `u8` (one byte) or `i32` (four, signed). Both are fixed width,
  so nothing in the package moves and no offset in its header changes.
- **`value`** can be a setting: `"value": "{{tenths}}"` writes whatever the
  player chose.

A package is stored as compressed chunks. Only the chunk holding the change is
decoded, rewritten and appended, and its table entry repointed - so a 179 MB
package grows by about a megabyte and everything else in it stays as the game
shipped it. Reverting copies the kept original back.

The game keeps a checksum for most packages, so a mod doing this has to say
`"requires_check_off": ["BrgGame.upk"]`, and the manager refuses to apply it
until that check is off.

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

Once a mod has been checked against a newer build and still does exactly the
same thing, list both. Any build in the list counts as a match:

```json
{ "game_version": ["5.0.3.0.0 - 1.87", "5.0.4.0.0 - 1.88"] }
```

Every mod that ships with the manager has been checked this way against 1.88:
each one changes exactly the same values on 1.88 as on 1.87.

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

## When a mod needs a second look

A mod with something worth knowing about gets a **coloured dot** next to its
name. The row stays folded — open it with the arrow when you want to read why,
and hover the name for a one-line version.

| Dot        | What it means                                                                                         |
|------------|-------------------------------------------------------------------------------------------------------|
| **Yellow** | Worth a look. Two mods write the same table, a mod it needs is not ticked, or a patch matches no rows |
| **Red**    | One mod overwrites the exact values another one sets, or this mod failed to apply                     |
| None       | Nothing to report                                                                                     |

Neither colour stops anything. A red dot on two mods that write the same values
is only telling you that the lower one in the load order wins — which may well
be what you wanted. The text inside the row names the mods, the table and the
column.

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

The same transaction leaves a small note inside `masters.db` itself: a table
named `_lid_mod_manager` listing the mods in it, their versions, the values they
were applied with and their load order. The game never reads it. It is there so
the database can say what is in it even when this manager's own records are not
around: after a reinstall, on another PC, or restored from a backup. The manager
never counts it as a change from stock, takes a mod off the list when you switch
that mod off, and removes the table when nothing is applied. It is only ever a
hint: every mod it names is checked against the actual values before it is
believed.

When a newer version of a mod you have applied comes with an update, the list
says **Update available**. The next **Save Mod List** takes the old version off
before putting the new one on, so nothing the old version changed is left behind.

The log reports how many rows each mod changed and how long the whole thing
took. Those row counts are worth a glance: a mod claiming far more rows than
you expected is usually a whole-table dump, and the note under *Making your own
mods* about `"apply": "diff"` explains what to do about it.

**Re-apply All** (Ctrl+R) runs the same list again without taking a fresh
backup — it is what the watchdog calls when the game replaces `masters.db`
after an update. Skipping the backup is deliberate: an automatic re-apply must
never quietly rotate your good backups away.

### While it is working

Saving shows a small window with a bar that fills, naming what it is doing:
backing up your database, each mod as it is applied, each game package as it is
rebuilt, then the files being copied. A save with a big content pack or a TFC
Installer mod in it can take a minute, and this is how you can tell it is
working rather than stuck. There is no cancel button — stopping half way through
writing a database is the one thing worth not allowing.

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

| Heading                             | What you can change                                                      |
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
python run.py enable revive-cost
python run.py preview revive-cost         # what it would change, no writes
python run.py apply
python run.py revert revive-cost
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
LiD Vanilla DB/<build>/masters.db   clean copies to compare against
```

All created on first run. Set `LID_DB_MANAGER_HOME` to put them somewhere else.

## Updating to a newer release

A new folder each time is not needed, and starting fresh loses what the manager
knows: which mods are on, the rows that let each one be switched off, and the
only copies of the game files your mods replaced.

Unzip the new release somewhere else first, then copy these across from the old
folder into the new one, replacing what is there:

| Copy across  | Why                                                                                          |
|--------------|----------------------------------------------------------------------------------------------|
| `state.json` | which mods are on, your load order, settings                                                 |
| `snapshots/` | the saved rows Revert puts back — without them, mods cannot be switched off cleanly          |
| `backups/`   | your database backups, and the only copies of the game files mods replaced                   |
| `mods/`      | mods you added yourself. Keep both: let the new release's copies of the shipped mods win     |
| `logs/`      | old session logs. Only worth keeping if you are chasing a problem                            |
| `cache/`     | which game package holds which texture. Optional — it is rebuilt in a few minutes if missing |

Then run the new `.exe`, and you carry on exactly where you left off.

The other way round works too: copy the new release's `.exe` and its
`_internal/` folder over the old ones and leave everything else alone. That is
fewer steps, but if a release ever drops a file from `_internal/`, the leftover
stays behind. Replacing the folder and carrying your files across avoids that.

**Do not keep two folders in use.** Each one keeps its own record of what it
applied, and a second folder does not know what the first one wrote — so it
cannot undo it, and may keep a modded file as its idea of stock.

If you would rather your files never moved, set `LID_DB_MANAGER_HOME` to a
folder of your own before running either release. Both then read and write the
same place, and updating is only ever replacing the program.

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

788 tests. A handful need real game files and skip without them - to run those,
point `LID_TFC_REFERENCE` at a folder holding `stock/`, `tfc-output/` and
`tommygun/` (the packages as the game ships them, the same packages after TFC
Installer has rebuilt them, and the mod folder itself).

They build a miniature `masters.db` from `tests/fixtures.py`, so no
game files are needed. The GUI tests run offscreen and skip themselves if
PySide6 is not installed.

## Credit

- **S3er0i9ng** - the Crossover Content pack, Colored PlayStation Buttons and
  Tower Static Radio, bundled with their permission.
- **FCH823** (with Wastelander121) - TFC Installer, whose `.PackagePatch` and
  texture-pack formats this reads, used with their permission.
- **Claudia-diva** - LID-Patches (MIT), where the drop-delay finding and the
  way a package chunk is rebuilt in place come from.

## License

MIT — see [LICENSE](LICENSE).
