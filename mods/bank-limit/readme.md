# Bank Limit

Every Safe and Spirit Tank level holds *your chosen number of* times as much, all 99 levels.

## Your value

**Capacity multiplier** - default and maximum **x10** - this is the only value confirmed working in game.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Whole numbers only - these are whole-number columns in the game. 1 leaves it at stock.

## What it changes

`limit` on every level of `master_safe_level` (Kill Coins) and `master_spirit_tank_level` (SP), all 99 levels.

Two parts you can switch separately: the Kill Coin bank and the SP bank.

The ceiling was x100 (v2.0.0), reasoned about only in terms of fitting the
game's integer range - true, but not the same thing as being inside what the
game's UI has ever actually shown for this field. Lowered to x25 (v2.0.1)
then to x10 after a related mod (Decal Draw Price) was reported to crash the
game at a very high value: x10 is confirmed working in game; nothing above
it has been, so the cap matches what is actually known to be safe rather
than a guess at it.

Replaces `bank-limit-10x`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
