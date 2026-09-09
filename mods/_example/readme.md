# Example Mod

A template. The manager skips folders whose name starts with `_`, so this one
never shows up in the mod list.

## Making your own

1. Copy this folder and rename it, e.g. `my-cool-mod/`.
2. Edit `mod.json`:
   - `id` should match the folder name (the folder name is what the manager uses).
   - `name` is the title in the mod list.
   - `description` is the one-liner under it.
   - `author`, `version` - yours.
   - `patches` - one or more of the four types below.
3. Optionally keep a `readme.md` (this file) and add a `screenshot.png`.

## Patch types

### `update_set` - set columns on matching rows

```json
{
  "type": "update_set",
  "table": "master_skill",
  "set": { "buy_money": 1 },
  "where": "buy_money > 1",
  "expected_rows": 320
}
```

`where` is raw SQL and optional (leave it out to hit every row).
`expected_rows` is a sanity check: a mismatch is a warning, never a failure,
because row counts drift between game versions.

### `text_replace` - find/replace inside a text row

```json
{
  "type": "text_replace",
  "table": "master_text",
  "match": { "id": "TXT_BIBLE_19_NOTE_G", "lang": "int" },
  "column": "txt",
  "replace": [
    { "find": "5000 Kill Coins", "with": "1 Kill Coin" }
  ]
}
```

`column` defaults to `txt`. Add `"require_find": true` to make a missing
`find` string a hard failure instead of a warning.

### `raw_sql` - inline SQL

```json
{
  "type": "raw_sql",
  "sql": "UPDATE master_skillgacha_odds SET odds = 1.0",
  "description": "Flatten skill gacha odds"
}
```

### `raw_sql_file` - SQL from a file in this folder

```json
{
  "type": "raw_sql_file",
  "path": "patch.sql",
  "description": "Applies patch.sql"
}
```

The file is read at apply time and runs inside the same transaction as
everything else, so editing it does not need a rescan.

## Raw SQL and conflicts

The manager cannot see which columns a raw SQL statement writes, so it treats
raw SQL as touching whole tables - and warns whenever another enabled mod
writes any of them. If you know your SQL leaves a table alone, list it:

```json
"raw_sql_files_do_not_touch": ["master_text"]
```

## Or skip JSON entirely

Drop a single `mod.sql` into your folder and the manager will pick it up with
metadata derived from the folder name. Add an `inverse.sql` next to it and
that is what "Revert" runs; without one, revert replays the automatic
pre-apply snapshot.
