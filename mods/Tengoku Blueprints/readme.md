# Tengoku Blueprints

In Tengoku, a medium chest that holds a blueprint nearly always holds an
ordinary one. A Tengoku blueprint, one for Tengoku's own gear, turns up about
once in 400. This mod sets how many of them are Tengoku blueprints instead.

## Your values

**Tengoku blueprint share** - default **25%**, from **0%** (stock) to
**90%**, in steps of 5.

**The most medium chests on every floor** - default **off**. On, every Tengoku
floor that can have medium chests gets the most it can (3, where the game
rolls 1 to 3), so there are more chests to find them in.

Select this mod and open the **Configuration** tab on the right to change
them, or right-click it and pick **Change values...**. Press Enter or click
away, then **Save Mod List**.

## What it changes

**The share.** Each Tengoku floor has its own list of what a medium chest can
give (`master_floor_drop_gen`, type `PTGENTP_TRBOX_M`, floors `HVN_...`):
757 lists, all of them blueprints. A Tengoku blueprint is one of the 28
blueprint groups whose item id ends in `H4`. In the stock lists they have
18,060 of the 7,570,000 weight in all, about 0.24%.

For each list that has both kinds, the mod:

- gives the Tengoku groups your share of the list's total weight, split
  between them in the same proportions as before;
- scales every other blueprint down to fill the rest, each keeping at least
  weight 1, so none drops out of the list;
- keeps the list's total weight exactly as it was.

Lists with no Tengoku blueprint, every other chest and drop type, and every
floor outside Tengoku are left alone.

**The most chests.** `mtbmin` is set to `mtbmax` on the Tengoku rows of
`master_floor` and `master_tmpfloor_stage` that can have medium chests (605 of
757 floors). Floors with none stay at none.

Not changed: how often a medium chest gives Kill Coins rather than a
blueprint, and the large chests, Haters and Treasure Tubers, which are where
Tengoku blueprints mostly come from in the stock game.

## Seeing it in the game

The LiD HUD plugin's floor info can show "Tengoku blueprint on this floor"
when one is in a chest you have not opened yet. Get it with **Get mods...**.

## Reverting

Automatic. The mod is applied against your untouched `masters.db`, so changing
the share never stacks, and unticking it and saving puts the stock lists back.

## Built for game 5.0.5.0.0 - 1.92
