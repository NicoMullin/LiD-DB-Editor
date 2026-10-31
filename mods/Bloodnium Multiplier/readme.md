# Bloodnium Multiplier

Everything you kill in the Tower of Barbs gives more Bloodnium: x2, x3, x5, or
anything up to x100. Splattershrooms can give more too, by a number of their
own.

## Your values

**Bloodnium** - default **x2**, from **x1** (stock) to **x100**. Every kill.

**Splattershrooms** - default **x1** (stock), up to **x100**. Eating a
Splattershroom, Big Splattershroom or Mega Splattershroom.

Select this mod and open the **Configuration** tab on the right to change them,
or right-click it and pick **Change values...**. Press Enter or click away,
then **Save Mod List**.

## What it changes

Six rows in `master_const_int`, each multiplied by **Bloodnium**:

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

Three rows in `master_mushroom_efc`, the "Gain Bloodnium" effects, each
multiplied by **Splattershrooms**:

| Row                 | Stock | Mushroom             |
|---------------------|-------|----------------------|
| `MSREFC_BLDMUSH_01` | 10    | Splattershroom       |
| `MSREFC_BLDMUSH_02` | 100   | Big Splattershroom   |
| `MSREFC_BLDMUSH_03` | 1,000 | Mega Splattershroom  |

Left alone on purpose:

- `BLOODNIUM_POINT_MAX` (999,999), the most Bloodnium you can hold.
- The Bloodnium a fighter needs to level up (`master_bodylvl_exp`), which is a
  cost, not something you earn.
- Bloodnium given as quest, login or shop rewards.

## Reverting

Automatic. The mod is applied against your untouched `masters.db`, so changing
x2 to x5 never multiplies twice, and unticking it and saving puts the stock
values back.

## Built for game 5.0.5.0.0 - 1.92
