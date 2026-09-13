# Armor Durability x2

Every piece of armour lasts twice as long before it breaks.

## What it changes

`master_part.dur`, doubled, on the **979** rows whose `type` is `PTTP_HEAD`,
`PTTP_BODY` or `PTTP_LEGS` - every helmet, top and pair of legs, including each
upgrade step. Weapons are untouched; there is a separate mod for those.

The six unnamed `PTTP_MASK` and `PTTP_PANTS` rows are left alone. They have no
name, no defence and no attack - they are the game's "wearing nothing" slots,
not equipment.

## Applying it twice does nothing extra

This mod multiplies a number, so applying it on top of itself would compound -
x2 twice would be x4. It is marked `"apply": "diff"`, which means the manager
runs it against an untouched copy of the database and writes only the
difference. However many times you save, the result is measured from vanilla,
so it stays exactly 2 times the original.

## Not yet confirmed in game

Verified against the database - the right rows change, by the right amount, and
unticking puts them back exactly - but nobody has played with it yet. Values
this size could still meet a cap somewhere the database does not know about.

## Reverting

Automatic. Before it runs, the manager copies every row it is about to change
into `snapshots/armor-durability-2x.json`, so **Revert Selected** - or just unticking it
and saving - puts the original numbers back.

## Built for game 5.0.3.0.0 - 1.87

Recorded against that build. If your database is a different one the manager
says so when you save; it will still apply, because these columns are addressed
by part type rather than by position.
