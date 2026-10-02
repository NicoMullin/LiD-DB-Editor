# Fall Damage

Falls hurt *your chosen percent* as much as normal. At 0, nobody takes fall damage, the same as if every fighter wore the **Super Shock Absorber** decal. It doesn't use up a decal slot, and it doesn't need the Fighter Class Passives hook.

## Your value

**Fall damage** - default **0%** (off), up to **100%** (stock).

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

## What it changes

Two rows in `master_const_int`:

| Row             | Stock | What it is                                                      |
|-----------------|-------|-----------------------------------------------------------------|
| `FALL_DMG_BASE` | 150   | 15% of the fighter's HP, taken by any fall over the safe height |
| `FALL_DMG_INC`  | 50    | another 5% of HP for every 100 units fallen past that height    |

Both are multiplied by your percent. The game script reads them in exactly one place, the fall-damage sum for human characters, and nothing else in the game uses them.

That sum runs for your fighter and for **Haters**, so Haters stop taking fall damage too. That's the same reach Super Shock Absorber has when it is handed out as a class passive.

Landing from a big drop still plays the heavy landing animation. That part is separate from the damage, and this mod doesn't change it.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.4.1.0 - 1.89
