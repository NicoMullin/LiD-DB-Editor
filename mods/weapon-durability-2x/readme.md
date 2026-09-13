# Weapon Durability x2

Every weapon lasts twice as long before it breaks.

Durability in this game is a countdown to the weapon shattering, so this is
straightforwardly twice as many swings, shots or hits out of everything you
pick up - from Fists and a Jungle Machete all the way to Iron Hammer EXRG.

## What it changes

`master_part.dur`, doubled, on the **385** rows whose `type` is `PTTP_ARM` -
every weapon and every upgrade step of one. Armour is untouched; there is a
separate mod for that.

Growth per level (`dur_c`) is deliberately left alone. It is a rate, not an
amount - doubling the base doubles the result at every level already.

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
into `snapshots/weapon-durability-2x.json`, so **Revert Selected** - or just unticking it
and saving - puts the original numbers back.

## Built for game 5.0.3.0.0 - 1.87

Recorded against that build. If your database is a different one the manager
says so when you save; it will still apply, because these columns are addressed
by part type rather than by position.
