# Making mods

A mod is **a folder in here**. Drop one in, press F5 in the manager (or restart
it), and it appears in the list. Nothing registers anything anywhere.

You do not have to find this folder by hand: dragging a `.sql`, a mod folder or
a `.zip` onto the manager's window does the same thing, and asks you to name the
mod as it goes.

Two starting points are already here — copy either and rename the copy:

| Copy this       | If you want                                                 |
|-----------------|-------------------------------------------------------------|
| `_example/`     | The full form: metadata, dependencies, all four patch types |
| `_example-sql/` | Just SQL, no metadata                                       |

Folders whose name starts with `_` or `.` are skipped, which is why those two
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
still knows the name, the author and the dependencies. `mods/nitro-boost-text/`
is a working example.

The `.sql` file can be called anything — the `path` in the patch decides. It
must be inside the mod folder; `../` is rejected.

---

## The four patch types

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

You also get a warning when a required mod is not enabled at all. Use `requires`
for companion mods — `nitro-boost-text` requires `nitro-boost-100000pct` so the
tooltip cannot claim something the skill does not do.

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
