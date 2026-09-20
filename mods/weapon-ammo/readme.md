# Weapon Spare Ammo

Every gun carries *your chosen number of* times as much spare ammo.

## Your value

**Spare ammo multiplier** - default **x2**, anything from x1 to x10.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Whole numbers only - these are whole-number columns in the game. 1 leaves it at stock. The ceiling
was x100 (v2.0.0) - lowered to x10 in v2.0.1 after a related mod (Decal Draw
Price) was reported to crash the game at a very high value. At x100 the
gun with the most spare ammo would land 100x past anything this field has
ever actually held; x10 stays a lot closer to ground the game is known to
handle.

## What it changes

`master_part.spare` on every gun that carries spare ammo (117 rows).

Rocket launchers, flame wands and the Red Hot Iron line carry no reserve at all - everything sits in the magazine - so this does nothing for them. See `weapon-magazine`.

Replaces `weapon-ammo-2x`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
