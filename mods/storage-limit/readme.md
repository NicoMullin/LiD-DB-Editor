# Storage Limit

The Coin Locker expands to *your chosen number,* instead of 2,000.

## Your value

**Coin Locker slots** - default **10,000 slots**, anything from 2,000 slots to 20,000 slots.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Stock is 2,000. It cannot go lower, so nothing already stored is stranded.

## What it changes

`master_const_int` `COINLOCKER_EXPAND_LIMIT_COUNT`.

It cannot be set below stock, so nothing already in the Coin Locker is stranded.

The ceiling was 99,999 (v2.0.0), never confirmed in game at the largest
values. Lowered to 20,000 in v2.0.1 after a related mod (Decal Draw Price)
was reported to crash the game at a very high value - no count/slot field
anywhere in the stock game goes anywhere near as high as the old ceiling did,
so this stays a lot closer to ground the game is known to handle.

Replaces `storage-10000`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
