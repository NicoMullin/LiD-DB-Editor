# Nitro Boost EXP +100,000%

Nitro Boost and Turbo-charged Engine give **100,000% EXP** instead of 40% and
10% - and the descriptions in game are rewritten to say so, rather than leaving
you looking at a tooltip that disagrees with what is happening.

## What it changes

Three patches, each switchable on its own:

| Part | What it does |
|------|--------------|
| #1 | `master_skill.val0` = 100000 for `SKL_EXPUP_02` (Nitro Boost) |
| #2 | `master_skill.val0` = 100000 for `SKL_EXPUP_01` (Turbo-charged Engine) |
| `text` | Rewrites both skill descriptions to read 100,000% |

Fourteen rows in total: the two values, plus twelve description rows.

## Languages

The description rewrite covers **English, German, Spanish, French, Italian and
Portuguese**, each with that language own thousands separator. Japanese,
Chinese and Korean keep the stock wording - the numbers still change, only the
text does not.

If you would rather have the boost without touching any text, expand the mod in
the list and untick the **text** part.

## Reverting

Automatic. The manager copies every row it is about to change into
`snapshots/nitro-boost-100000pct.json` first, so unticking it and saving puts
the original values and wording back.

## Built for game 5.0.3.0.0 - 1.87
