# Example: SQL-only mod (copy this folder)

The second template. Where `_example/` shows the full `mod.json` form, this one
shows the "paste it from a forum post" form: **one `.sql` file, nothing else.**

Both templates start with `_`, so the manager skips them — they never appear in
the mod list. Copy the folder, rename the copy without the underscore, and it
becomes a real mod.

## What you get

With no `mod.json` there is no metadata, so the manager fills it in:

- **Name** — derived from the folder name (`my-cool-mod` becomes "My Cool Mod")
- **Author** — `(unknown - .sql only)`
- **Description** — "Raw SQL mod (no metadata)"
- **Patches** — one `raw_sql_file`, the whole file

## Revert

The manager cannot read intent out of raw SQL, so before running it, it
snapshots **every table your SQL writes to**, in full. Revert Selected replays
that snapshot.

If you would rather undo your own way, drop an `inverse.sql` next to `mod.sql`.
When both are present the manager treats them as a pair and Revert runs your
`inverse.sql` instead of the snapshot.

## Conflicts

Raw SQL is opaque, so the manager warns whenever another enabled mod writes any
table your SQL touches — even if you are writing different columns. That is
deliberately cautious. There is no way to opt out from a bare `.sql` mod; if you
need to, add a `mod.json` with a `raw_sql_file` patch and list the safe tables
in `raw_sql_files_do_not_touch`. See `_example/readme.md` for that form.
