# Armor Durability

Every piece of armour lasts *your chosen number of* times as long before it breaks.

## Your value

**Durability multiplier** - default **x2**, anything from x1 to x10.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Whole numbers only - these are whole-number columns in the game. 1 leaves it at stock. The ceiling
was x100 (v2.0.0) - lowered to x10 in v2.0.1 after a related mod (Decal Draw
Price) was reported to crash the game at a very high value. At x100 the
highest-durability piece in the game would land 100x past anything this
field has ever actually held; x10 stays a lot closer to ground the game is
known to handle.

## What it changes

`master_part.dur` on every head, body and leg piece (979 rows).

Replaces `armor-durability-2x` and `-5x`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
