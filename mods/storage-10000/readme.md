# Storage Holds 10,000

## What it changes

`master_const_int.COINLOCKER_EXPAND_LIMIT_COUNT`, from **2,000** to **10,000**.
One row. That is the ceiling the Coin Locker can be expanded to.

`COINLOCKER_EXPAND_COUNT` - 10 - is left alone. That is how many slots one
expansion buys, not the maximum.

## Not yet confirmed in game

Verified against the database - the right rows change, by the right amount, and
unticking puts them back exactly - but nobody has played with it yet. Values
this size could still meet a cap somewhere the database does not know about.

## Reverting

Automatic. The manager copies every row it is about to change into
`snapshots/storage-10000.json` first, so unticking it and saving puts the original
numbers back.

## Built for game 5.0.3.0.0 - 1.87
