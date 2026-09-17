# Storage Limit

The Coin Locker expands to *your chosen number,* instead of 2,000.

## Your value

**Coin Locker slots** - default **10,000 slots**, anything from 2,000 slots to 99,999 slots.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Stock is 2,000. It cannot go lower, so nothing already stored is stranded.

## What it changes

`master_const_int` `COINLOCKER_EXPAND_LIMIT_COUNT`.

It cannot be set below stock, so nothing already in the Coin Locker is stranded.

Not yet confirmed in game at the largest values - a cap somewhere outside the database could still apply.

Replaces `storage-10000`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
