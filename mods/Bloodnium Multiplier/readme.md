# Bloodnium Multiplier

Everything you kill in the Tower of Barbs gives more Bloodnium: x2, x3, x5, or
anything up to x100.

## Your value

**Bloodnium** - default **x2**, from **x1** (stock) to **x100**.

Select this mod and open the **Configuration** tab on the right to change it,
or right-click it and pick **Change values...**. Press Enter or click away,
then **Save Mod List**.

## What it changes

Six rows in `master_const_int`, each multiplied by your value:

| Row                              | Stock | What gives it          |
|----------------------------------|-------|------------------------|
| `SCREAMER_BLOODNIUM_POINT`       | 1     | a Screamer             |
| `ENEMY_BLOODNIUM_POINT`          | 3     | an ordinary enemy      |
| `HARD_HATER_BLOODNIUM_POINT`     | 9     | a Hater                |
| `TREASURE_TUBER_BLOODNIUM_POINT` | 10    | a Treasure Tuber       |
| `MBOSS_BLOODNIUM_POINT`          | 100   | a mid-boss             |
| `BOSS_BLOODNIUM_POINT`           | 150   | a boss                 |

The game's own bonuses are percentages on top of these, so they grow with
them: the kill-streak bonus (every 10 kills), the floor-travel bonus (every 5
floors) and the VIP bonus.

Left alone on purpose:

- `BLOODNIUM_POINT_MAX` (999,999), the most Bloodnium you can hold.
- The Bloodnium a fighter needs to level up (`master_bodylvl_exp`), which is a
  cost, not something you earn.
- Bloodnium given as quest, login or shop rewards.

## Reverting

Automatic. The mod is applied against your untouched `masters.db`, so changing
x2 to x5 never multiplies twice, and unticking it and saving puts the stock
values back.

## Built for game 5.0.4.4.0 - 1.91
