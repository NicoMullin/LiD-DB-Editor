# Fighter Bonus Points

A fighter you buy comes with bonus points: each of its six stats (HP, Strength,
Dexterity, Vitality, Stamina, Luck) rolls a bonus that raises that stat's level
cap until the fighter's first limit break. This mod makes the bonuses come out
bigger.

## How big a bonus is

Each stat rolls 0, 5, 10, 15 or 20% of the grade's level cap, rounded down. The
class plays no part. At stock half of all rolls give nothing:

| Grade | Level cap | 0% (odds 50) | 5% (20) | 10% (15) | 15% (10) | 20% (5) |
|-------|-----------|--------------|---------|----------|----------|---------|
| 1     | 5         | +0           | +0      | +0       | +0       | +1      |
| 2     | 9         | +0           | +0      | +0       | +1       | +1      |
| 3     | 13        | +0           | +0      | +1       | +1       | +2      |
| 4     | 17        | +0           | +0      | +1       | +2       | +3      |
| 5     | 21        | +0           | +1      | +2       | +3       | +4      |
| 6     | 25        | +0           | +1      | +2       | +3       | +5      |

The five sizes are built into the game; only their odds are data, so 20% is the
most a stat can get.

## Your choice

**Bonus points** in Configuration:

| Choice                                      | What it does                                                       |
|---------------------------------------------|--------------------------------------------------------------------|
| **Never nothing (stock odds for the rest)** | No stat rolls 0; 5, 10, 15 and 20% keep their stock odds (default) |
| **Always the biggest**                      | Every stat gets 20%: a grade 6 fighter gets +5 in all six          |
| **Your own odds**                           | Set the odds of each size yourself                                 |

Your own odds are shares, not percentages: each size's chance is its number out
of the five added together. All five at 0 gives no bonus at all.

## What it changes

`master_const_int` `BODY_BONUS_0`, `BODY_BONUS_5`, `BODY_BONUS_10`,
`BODY_BONUS_15` and `BODY_BONUS_20`: the odds the game reads for each size when
a fighter is bought.

Only fighters bought after the mod is on get the new odds; fighters you already
have keep their bonuses. Start the game after saving, since it reads the odds
once per start. The save editor's bonus choices follow the same five sizes.

## Reverting

Untick the mod and save: the stock odds come back. Fighters bought while it was
on keep what they rolled.
