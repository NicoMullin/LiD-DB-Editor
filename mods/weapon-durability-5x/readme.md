# Weapon Durability x5

Every weapon lasts five times as long before it breaks.

The same change as **Weapon Durability x2**, turned up. At this point most
weapons will outlast the run you are using them on, which is either the point
or too much, depending on why you are here.

## What it changes

`master_part.dur`, multiplied by five, on the **385** rows whose `type` is
`PTTP_ARM`. Armour is untouched.

Enable this **or** the x2 mod, not both - they are declared as conflicting, so
the manager will warn you.

## Applying it twice does nothing extra

This mod multiplies a number, so applying it on top of itself would compound -
x2 twice would be x4. It is marked `"apply": "diff"`, which means the manager
runs it against an untouched copy of the database and writes only the
difference. However many times you save, the result is measured from vanilla,
so it stays exactly 5 times the original.

## Not yet confirmed in game

Verified against the database - the right rows change, by the right amount, and
unticking puts them back exactly - but nobody has played with it yet. Values
this size could still meet a cap somewhere the database does not know about.

## Reverting

Automatic. Before it runs, the manager copies every row it is about to change
into `snapshots/weapon-durability-5x.json`, so **Revert Selected** - or just unticking it
and saving - puts the original numbers back.

## Built for game 5.0.3.0.0 - 1.87

Recorded against that build. If your database is a different one the manager
says so when you save; it will still apply, because these columns are addressed
by part type rather than by position.
