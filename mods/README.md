# Making mods

A mod is **a folder in here**. Drop one in, press F5 in the manager (or restart
it), and it appears in the list. Nothing registers anything anywhere.

You do not have to find this folder by hand: dragging a `.sql`, a mod folder or
a `.zip` onto the manager's window does the same thing, and asks you to name the
mod as it goes.

Three starting points are already here — copy one and rename the copy:

| Copy this         | If you want                                                    |
|-------------------|----------------------------------------------------------------|
| `_example/`       | The full form: metadata, dependencies, all four DB patch types |
| `_example-sql/`   | Just SQL, no metadata                                          |
| `_example-asset/` | A mod that replaces game files (`.upk`), not database rows     |

Folders whose name starts with `_` or `.` are skipped, which is why those three
never show up in the mod list. Your copy must not start with `_`.

---

## The four layouts

The manager works out what kind of mod a folder is by what is inside it, in this
order:

| Files in the folder             | What happens                                                                 |
|---------------------------------|------------------------------------------------------------------------------|
| `mod.json`                      | Parsed as a full mod. Other files are used only if `mod.json` points at them |
| `mod.sql` **and** `inverse.sql` | SQL mod where **Revert** runs your `inverse.sql`                             |
| exactly one `*.sql`             | SQL mod, name taken from the folder, revert via snapshot                     |
| anything else                   | Skipped, with the reason shown in the mod list and the log                   |

`mod.json` always wins. If a folder has both `mod.json` and `.sql` files, the
`.sql` files are only used if a patch names them.

Two or more `.sql` files with no `mod.json` is an error — the manager will not
guess which to run. Add a `mod.json` listing them in order.

---

## Layout 1: just SQL

The quickest thing that works. One file:

```
my-cool-mod/
└── mod.sql
```

```sql
UPDATE master_shop_product_price SET price = 1 WHERE price > 1;
```

The manager derives the name from the folder (`my-cool-mod` → "My Cool Mod"),
shows the author as `(unknown - .sql only)`, and applies the whole file.

**Do not wrap it in `BEGIN` / `COMMIT`.** You can — those statements are ignored
with a warning — but there is no point: the manager already runs every enabled
mod inside one transaction. Honouring your `COMMIT` would end that transaction
early and break the all-or-nothing guarantee, so it is dropped instead.

## Layout 2: SQL with your own undo

```
my-cool-mod/
├── mod.sql
└── inverse.sql
```

When both are present, **Revert Selected** runs `inverse.sql` instead of
replaying the snapshot. Use this when you know how to undo your change more
precisely than "put the old rows back" — or when your SQL touches so much of a
table that snapshotting it is wasteful.

If you have no `inverse.sql`, revert still works: see [Revert](#revert) below.

## Layout 3: `mod.json`

The full form. Everything the mod list shows comes from here:

```json
{
  "id": "my-cool-mod",
  "name": "My Cool Mod",
  "description": "One line, shown under the mod in the list.",
  "version": "1.0.0",
  "author": "your-name",
  "homepage": "https://github.com/you/your-repo",

  "requires": [],
  "conflicts_with": [],
  "raw_sql_files_do_not_touch": [],

  "patches": [
    { "type": "update_set", "table": "master_skill",
      "set": { "buy_money": 1 }, "where": "buy_money > 1" }
  ]
}
```

| Field                        | Required | Notes                                                                                              |
|------------------------------|----------|----------------------------------------------------------------------------------------------------|
| `id`                         | yes      | Should match the folder name — the **folder name** is what the manager uses; a mismatch only warns |
| `name`                       | yes      | Title in the list                                                                                  |
| `description`                | yes      | One line under it. Long is fine; it wraps                                                          |
| `version`                    | yes      | SemVer suggested, not enforced                                                                     |
| `author`                     | yes      |                                                                                                    |
| `homepage`                   | no       | Clickable in the Details panel                                                                     |
| `requires`                   | no       | Mod ids that must also be enabled. Applied before yours                                            |
| `conflicts_with`             | no       | Mod ids that must not be enabled with yours                                                        |
| `raw_sql_files_do_not_touch` | no       | Tables your raw SQL leaves alone; see [Conflicts](#conflicts)                                      |
| `patches`                    | yes      | One or more, applied in order                                                                      |

## Layout 4: `mod.json` plus files

```
my-cool-mod/
├── mod.json          ← metadata, and a raw_sql_file patch pointing at the .sql
├── patch.sql         ← the SQL
├── readme.md         ← shown in the Readme tab
└── screenshot.png
```

This is the best of both: your SQL stays exactly as written, and the manager
still knows the name, the author and the dependencies.
`mods/nitro-boost-100000pct/` is a working example - it mixes two `update_set`
patches with a `raw_sql_file` one.

The `.sql` file can be called anything — the `path` in the patch decides. It
must be inside the mod folder; `../` is rejected.

---

## Patches as switchable parts

A mod with more than one patch shows each of them in the list with its own
checkbox, so a player can take the half of your mod they want. Give every patch
an `"id"` and a `"description"` and those switches are labelled and stable:

```json
"patches": [
  { "type": "update_set", "id": "prices", "description": "Shop prices",
    "table": "master_shop_product_price", "set": { "price": 1 }, "where": "1=1" },
  { "type": "update_set", "id": "revive", "description": "Revive costs",
    "table": "master_shop_product_price", "set": { "medal": 0 },
    "where": "id LIKE 'PRD_CONTINUE%'" }
]
```

Without an `"id"` a patch is remembered by position — `#1`, `#2` — so inserting
a patch at the top of the list silently moves everyone's choices onto the wrong
parts. Set one on anything you expect people to switch off. Ids only have to be
unique inside the mod.

A single-patch mod gets no extra rows; there is nothing to choose between.

## The patch types

Four write the database — `update_set`, `text_replace`, `raw_sql`,
`raw_sql_file` — and `asset_file` copies game files. A single mod can mix them:
a content pack that ships `.upk` files *and* the `master_*` rows that make the
game use them is one folder with both kinds of patch.

### `update_set` — set columns on matching rows

```json
{
  "type": "update_set",
  "table": "master_skill",
  "set": { "buy_money": 1 },
  "where": "buy_money > 1",
  "expected_rows": 320
}
```

- `where` is raw SQL and optional. Leave it out to hit every row.
- Prefer `where` clauses that exclude rows already at the target value
  (`buy_money > 1`, not no clause). It keeps the diff preview honest and makes
  re-applying a no-op.
- `expected_rows` is a sanity check. A mismatch is a **warning, never a
  failure** — row counts drift between game versions.

This is the type to reach for. The manager understands exactly which columns it
writes, so conflict detection is precise and revert is minimal.

### `text_replace` — find/replace inside a text row

```json
{
  "type": "text_replace",
  "table": "master_text",
  "match": { "sct": "SKILL_DESCRIPTION", "id": "TXT_SKL_EXPUP_02", "lang": "int" },
  "column": "txt",
  "replace": [
    { "find": "40%", "with": "100,000%" }
  ],
  "require_find": false
}
```

- `match` is an exact-equality filter — every key must match.
- `column` defaults to `txt`.
- Replacements run in order on each matching row.
- If the `find` text is not there, you get a **warning** and the row is left
  alone. Set `"require_find": true` to make that a hard failure instead.

Use this rather than `raw_sql` for text: it is safe to run twice, and the diff
preview shows the whole before/after string.

### `raw_sql` — inline SQL

```json
{
  "type": "raw_sql",
  "sql": "UPDATE master_skillgacha_odds SET odds = 1.0",
  "description": "Flatten gacha odds"
}
```

Anything SQLite accepts. Add a `description` — without one the mod list shows a
truncated copy of the SQL.

### `raw_sql_file` — SQL from a file

```json
{
  "type": "raw_sql_file",
  "path": "patch.sql",
  "description": "What this file does"
}
```

Read **at apply time**, so editing the `.sql` takes effect without a rescan.

### `asset_file` — copy whole game files into the game folder

For texture/model swaps and content packs that ship replacement `.upk`
packages. This patch does **not** touch the database — it copies files.

```
my-asset-mod/
├── mod.json
└── assets/
    ├── CH_Equip_NF_SPE_Head0063_SF.upk
    └── CH_Equip_NM_SPE_Head0063_SF.upk
```

**You usually don't write this by hand.** Drop a folder of `.upk` files — or a
whole content pack that has an `assets/` folder (a `catalog.json` next to it is
fine, it's ignored) — onto the window, or use **Tools ▸ Add a mod from a
folder**. The manager writes the `mod.json` below for you and asks for a name.

**A pack that also changes `masters.db`** (items, quests, drop pools — the
Crossover Content pack is one) needs a second step: those changes are the
pack's own installer logic, not data the manager can read. Such installers
usually work on the game folder and edit the real `masters.db` in place, so:
enable and save the files mod first, back up the vanilla `masters.db`, let the
pack's installer edit the real one, copy the edited file out, and put the
vanilla one back. Then drag the edited copy onto the window. The manager diffs
it against your vanilla copy and writes the database changes as a **companion
mod** you enable next to the files mod. Two mods, two checkboxes — so you can
see each half loaded. The main README walks through this step by step for the
Crossover Content pack.

```json
{
  "type": "asset_file",
  "source": "assets",
  "target": "BrgGame/CookedPCConsole"
}
```

- `source` is a **file or a folder** inside the mod folder (default `assets`).
  A folder mirrors every file under it into `target/<relative path>`; a single
  file goes straight to `target`.
- `target` is relative to the **game folder** — the one that holds `BrgGame`.
  It may not be absolute or contain `..`; the manager also checks every
  resolved path stays inside the game folder before writing.
- One `asset_file` patch is one on/off unit, however many files it carries.
  Split into several patches if you want them toggled separately.
- Hashes are computed from the bundled files — there is nothing to keep in
  sync in `mod.json`.

**The game folder.** The manager finds it automatically when you have pointed
it at the real `BrgGame/Content/masters.db`. If you are working against a loose
copy, set it with **Tools ▸ Set game folder** (`python run.py game-root <path>`).

**Backups and revert.** The first time any enabled mod claims a game file, the
file that is there is copied into `backups/game_files/` and never touched
again — the file-level `masters.db.original`. Reverting the mod restores that
copy, or deletes the file if the mod created it. **Tools ▸ Restore game files**
(`python run.py restore-game-files`) puts every mod-changed file back at once.

**All-or-nothing.** If any file copy fails partway through, every file that run
already changed is put back — and if the mod also changed the database, that is
rolled back too, so the whole Save is undone.

**The game must be closed.** A running game holds its `.upk` files and its
database open; any Save or Revert that touches game files is refused until you
close it.

**What a mod can never copy.** Some files are refused outright, wherever they
would go, and the mod shows in the list as broken with the reason:

| Refused                                                                   | Why                                                                                                                     |
|---------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Program files — `.exe` `.dll` `.asi` `.bat` `.ps1` and similar            | Windows *runs* these. The game loads DLLs from its own folder at startup, so one dropped there would run on next launch |
| `masters.db`, its `-wal`/`-journal`, and its `.original`/`.backup` copies | Database changes go through database patches, which snapshot and can be reverted. Replacing the file skips all of that  |
| Any path containing `:`                                                   | Never a real game file, and on NTFS it writes a hidden stream                                                           |

Game content — `.upk`, `.tfc`, config `.ini` — is unaffected. Files other than
`.upk` still get a note in the log, since they are unusual, but they are
copied. The check runs when the mod loads, again at validation, and once more
just before anything is copied, so a file slipped into a mod folder afterwards
is caught too.

---

## What happens when you press Save Mod List

1. **Validation**, on a read-only connection. Tables and columns must exist,
   `where` clauses must parse, raw SQL must compile (via `EXPLAIN`, which never
   runs it), `text_replace` targets are checked for their `find` text.
   If any enabled mod fails, **nothing at all is applied.**
2. **Backups.** First time only, `masters.db.original` — never overwritten.
   Then the rolling `masters.db.backup` and a dated copy in `backups/`.
3. **Snapshot**, per mod, of exactly the rows that mod is about to change.
4. **Apply**, all mods in one transaction. Any error rolls back everything.
5. **Game files**, if any enabled mod has an `asset_file` patch: after the
   database commit, the `.upk` files are copied in, hash-checked and swapped
   atomically. A failure here rolls the database back too.

Writing a mod that fails validation is cheap and safe. Test freely.

---

## Revert

Every mod can be undone, whether or not you wrote an undo.

- **Snapshot (default).** Before your patches run, the rows they will touch are
  copied into `snapshots/<mod-id>.json`. Revert writes them back by rowid, so
  one mod can be peeled off without disturbing others layered on the same table.
- **`inverse.sql`.** If your folder has one, that runs instead.

For `update_set` and `text_replace` the snapshot is exactly the affected rows.
**For raw SQL the manager cannot tell what you touched**, so it snapshots every
table your SQL writes to, in full. That is correct but heavy — another reason to
prefer `update_set` where it fits. Past 200,000 rows it gives up and warns that
revert will need the `.db` backup instead.

---

## Conflicts

The manager works out what each enabled mod writes and warns about overlaps.
Warnings never block an apply - the later mod in apply order simply wins.

| Situation                                           | Result                          |
|-----------------------------------------------------|---------------------------------|
| Two `update_set` mods on the same `(table, column)` | Warn, if they hit the same rows |
| Two `text_replace` mods on the same matched row     | Warn                            |
| `update_set` + `text_replace` on the same table     | No conflict - different layers  |
| Raw SQL vs anything writing the same table          | Warn, if they hit the same rows |
| Two `asset_file` mods copying the same game file    | Warn; the lower mod's copy wins |

**Overlap is measured in rows, not tables.** Every `UPDATE` and `DELETE` in your
SQL is resolved against the live database to work out exactly which rows it
touches, and two mods are only in conflict if those sets intersect. So a mod
renaming `master_text` rows where `sct = 'AREA_NAME'` and one rewriting rows
where `sct = 'SKILL_DESCRIPTION'` are left alone, even though both write
`master_text`. When they do overlap, the warning says how many rows they share.

Three things make a mod fall back to "assume the whole table", which conflicts
with anything else writing it:

- an `INSERT` (the rows do not exist yet, so they cannot be compared)
- an `UPDATE`/`DELETE` with no `WHERE` clause
- a statement shaped in a way the manager cannot read, or one whose `WHERE`
  the database rejects

That fallback is also what you get with no database selected. It is deliberately
the cautious direction: a missed warning is worse than an extra one.

If you know your raw SQL leaves a table alone entirely, say so:

```json
"raw_sql_files_do_not_touch": ["master_text"]
```

## `"apply": "diff"` - only change what you really change

By default a mod's SQL runs against the player's database exactly as written.
That is fine for a precise mod, but a blanket `UPDATE master_text SET ...` also
writes over rows it does not care about - wiping whatever another mod put
there.

Setting `"apply": "diff"` changes that. The mod runs against a throwaway copy
of the player's untouched `masters.db.original`, the result is compared with it,
and only the values that actually differ reach the real database:

```json
{ "id": "big-rework", "name": "Big Rework", "apply": "diff", ... }
```

Two things follow. A mod that rewrites a whole table stops clobbering other
mods, because a row it rewrites to the same value it already had is not a
change. And the mod becomes idempotent - `SET price = price / 2` applied twice
halves once, because the mod is always "vanilla plus these exact values" rather
than "run this again".

It needs `masters.db.original` to exist, which it does from the moment the
player picks their database. Without it the manager says so and runs the SQL
directly instead. Genuine collisions are still resolved by load order.

Worth turning on for: whole-table dumps, anything exported from a modded
database, and any SQL that reads a value to compute the new one.

## Adding tables of your own

A mod is not limited to changing values that already exist. It can add rows to a
table, and it can add a whole table the game shipped without:

```sql
CREATE TABLE IF NOT EXISTS master_custom_vending (
    id TEXT PRIMARY KEY,
    product TEXT NOT NULL,
    price INTEGER NOT NULL DEFAULT 0
);
INSERT INTO master_custom_vending VALUES ('CV_0001', 'PRD_MUSHROOM', 100);
```

Reverting the mod drops the table again — the manager records that it was not
there beforehand, so undoing means taking it away, not restoring an empty copy.
Nothing else in the database is touched.

Write `IF NOT EXISTS` if you can, because it says what you mean. If you forget,
the manager reads your `CREATE` as though you had written it and says so in the
log: every save re-runs the whole enabled list, and a bare `CREATE TABLE` would
fail the second time round on the table the mod itself made.

This works through the import flow too. Hand the manager a modded `masters.db`
that has a table vanilla does not, and the generated mod recreates it — schema,
indexes and rows. The one thing that is still refused is a database *missing* a
vanilla table or with different columns in one: that is a different game
version, and diffing against it would read as "undo the developers' changes".

Worth knowing before you build on this: the game only reads tables and columns
its own code knows about, so a brand-new table does nothing on its own. It is
useful as data your mod's other SQL draws on. Adding **rows** to a table the
game already reads is the thing that visibly works in-game.

## Load order

Enabled mods are numbered in the list. **Top applies first, bottom wins.** Two
mods writing the same value are resolved by that order and nothing else, so the
way to override one thing from a big mod is to sit below it and change only
that thing.

The order is yours - the manager never silently reorders it. `requires` is a
declaration, not a constraint: if a mod ends up above something it requires you
get a warning telling you to move it, rather than the manager quietly shuffling
things behind your back.

You also get a warning when a required mod is not enabled at all. Use
`requires` for a mod that only makes sense on top of another one - a text mod
that rewrites a description to match a number some other mod changes, say.

---

## Practical notes

**Encoding.** Save `.json` and `.sql` as UTF-8 (a BOM is tolerated). If you are
patching Japanese, Chinese or Korean text, check the file after any copy-paste
round trip — mojibake like `ErhÃ¶ht` means it went through Latin-1 somewhere,
and the CJK characters may be unrecoverable rather than merely ugly.

**Trailing whitespace matters.** Skill descriptions in `master_text` end with a
newline and a space. In SQL that is `|| x'0a20'` on the end of the string;
dropping it changes the tooltip layout.

**Some tables will not take effect immediately.** Shop and vending machine
lineups are settled by the game's daily reset, so rows you add there do not
appear until the in-game day rolls over. Others are read once at launch. If your
mod applies cleanly and the diff preview shows the rows, the database is right -
say so in your readme so nobody reports it as broken.

**Test against a copy first.** Point the manager at a duplicate of `masters.db`
while you iterate. `python run.py preview <mod-id>` shows the exact rows your
mod would rewrite without touching anything.

**Iterating.** F5 (or File > Rescan mods folder) reloads everything from disk.
`raw_sql_file` contents are re-read on every apply, so SQL edits need no rescan
at all.

**Debugging a mod that will not load.** The reason appears in the mod list where
the mod would have been, and in `logs/`. The commonest causes: a trailing comma
in `mod.json`, a patch `type` that is not one of the four, or two `.sql` files in
a folder with no `mod.json`.

**Sharing.** Zip the folder. There is no registry, no manifest, no install step —
whoever gets it drops it in their own `mods/`.
