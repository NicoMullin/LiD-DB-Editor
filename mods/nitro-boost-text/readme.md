# Nitro Boost Text Says 100,000%

The cosmetic companion to `nitro-boost-100000pct`. That mod changes what the
skill *does*; this one changes what the tooltip *says*, so the description reads
"Increases EXP gained by 100,000%." instead of the stock 40% / 10%.

It `requires` `nitro-boost-100000pct` — the manager warns if you enable this one
without it, because you would end up with a tooltip that lies in the other
direction.

## What it changes

`master_text`, rows where `sct = 'SKILL_DESCRIPTION'` and the id is
`TXT_SKL_EXPUP_01` or `TXT_SKL_EXPUP_02`. It does not touch `master_skill`, so
it stacks with the EXP mod without conflicting.

Each description ends with `x'0a20'` — a newline and a space. That is how the
game stores every skill description, and dropping it changes the layout of the
tooltip, so the SQL appends it explicitly.

## Languages

Covered: `int` (English), `deu`, `esn`, `fra`, `ita`, `ptb`.

**Not covered: `jpn`, `chn`, `kan`, `kor`.** The original patch had them, but the
text was corrupted before it reached this repo — the UTF-8 continuation bytes
were stripped somewhere in transfer, leaving unrecoverable characters. Guessing
at Japanese, Chinese and Korean game text is worse than leaving it alone, so
those four languages keep the stock wording. The EXP multiplier still works for
them; only the tooltip text is stale.

`nitro-boost-text.sql` has a commented-out block showing exactly where to paste
them if you have the intact original. Save the file as UTF-8 and press F5.

## Why this is a `mod.json` + `.sql` mod

A bare `mod.sql` would work, but then the manager could not know the mod's name,
author, or that it depends on `nitro-boost-100000pct`. The `mod.json` supplies
that metadata and points at the SQL file, which is used verbatim.

## Revert

Automatic snapshot. The manager cannot see inside raw SQL, so before applying it
snapshots the whole of `master_text` — **Revert Selected** puts every original
string back.
