# TDM Rewards

Every Kill Coin and SP reward from Tokyo Death Metro is multiplied by *your chosen number of*.

## Your value

**Reward multiplier** - default **x2**, anything from x1 to x10.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Whole numbers only - these are whole-number columns in the game. 1 leaves it at stock. The ceiling
was x100 (v2.0.0) - lowered to x10 in v2.0.1 after a related mod (Decal Draw
Price) was reported to crash the game at a very high value. At x100 the
richest reward row would land 100x past anything these fields have ever
actually held; x10 stays a lot closer to ground the game is known to handle.

## What it changes

Every Kill Coin and SP payout in `master_tdm_rank` (15 ranks) and `master_war_reward` (48 rows).

Two parts you can switch separately: the rank bonuses, and the battle payouts.

The `_bag` columns hold odds, not amounts, so they are never multiplied.

Replaces `tdm-rewards-2x`, `-5x` and `-10x`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
