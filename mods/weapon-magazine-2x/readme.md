# Weapon Magazine x2

Every gun holds twice as many rounds before reloading.

A Bronson Magnum goes from 6 shots to 12, a Cerberus Shotgun from 5 to 10, an
M-404 Rocket Launcher from 4 to 8.

## What it changes

`master_part.capacity`, doubled, on the **160** `PTTP_ARM` rows that have a
magazine. Your spare ammo is not touched - there is a separate mod for that -
so with this alone you reload half as often but run dry at the same point.

## The weapons this helps most

Rocket launchers, flame wands and the Red Hot Iron line have no reserve ammo at
all: their magazine is everything they will ever fire. For those, this mod is
the only one that gives them more shots, and it doubles their whole supply.

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
into `snapshots/weapon-magazine-2x.json`, so **Revert Selected** - or just unticking it
and saving - puts the original numbers back.

## Built for game 5.0.3.0.0 - 1.87

Recorded against that build. If your database is a different one the manager
says so when you save; it will still apply, because these columns are addressed
by part type rather than by position.
