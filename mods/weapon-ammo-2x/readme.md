# Weapon Spare Ammo x2

Every gun carries twice as much spare ammo.

This is the reserve you reload *from* - the pool behind the magazine. A Bronson
Magnum goes from 36 spare rounds to 72, a DUKE-02 Sniper Rifle from 16 to 32,
a Fireworks Launcher from 45 to 90.

## What it changes

`master_part.spare`, doubled, on the **117** `PTTP_ARM` rows that have any. The
magazine itself is not touched - there is a separate mod for that.

## Weapons this does not help

Some weapons have a magazine but no reserve at all: rocket launchers, flame
wands, the Red Hot Iron line. Everything they will ever fire is in the
magazine, so their spare is zero and doubling zero is zero.

For those, **Weapon Magazine x2** is the mod that gives you more shots.

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
into `snapshots/weapon-ammo-2x.json`, so **Revert Selected** - or just unticking it
and saving - puts the original numbers back.

## Built for game 5.0.3.0.0 - 1.87

Recorded against that build. If your database is a different one the manager
says so when you save; it will still apply, because these columns are addressed
by part type rather than by position.
