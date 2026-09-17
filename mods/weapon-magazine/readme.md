# Weapon Magazine

Every gun holds *your chosen number of* times as many rounds before reloading.

## Your value

**Magazine multiplier** - default **x2**, anything from x1 to x100.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Whole numbers only - these are whole-number columns in the game. 1 leaves it at stock.

## What it changes

`master_part.capacity` on every gun with a magazine (160 rows).

For the guns with no reserve ammo, this multiplies their entire supply.

Replaces `weapon-magazine-2x`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
