# Stew Multi-Pull

Adds a third entry to the Mushroom Club's stew menu, between **Purchase** and
**Check Lineup**: **Purchase x10**, or x5, x15, x20 or x25 - pick the number
in the **Configuration** tab ("Pulls per purchase") and save.

It pulls that many decals in a row. The stew animation plays once, for the
first pull, and the closing animation once at the end; in between, each decal
shows its own result card, and you press the button to see the next one.

## What a pull costs

Nothing new. Each pull is the game's normal single pull, one after another:

- each costs what one pull costs, so a price mod such as Decal Draw Price
  still applies to every one of them;
- each takes the next decal from the queue the game keeps in your save, the
  same decals that many single pulls would have given;
- the game saves after each one, as it always does.

If you run out of Kill Coins, or one kind of decal reaches 99, partway through,
the pulls stop there and the game's own message says why. The closing
animation still plays.

Plain **Purchase** works exactly as before, and the bonus-box stews are not
changed.

## What it changes

Two functions of the stew menu in `BrgGame.upk`, as a `.PackagePatch` - one
for each choice, in `tfc/x5` to `tfc/x25`. The manager applies the one you
chose to your own `BrgGame.upk` alongside other mods that change that file,
such as Instant Drops, and puts the stock file back when every mod that
changes it is switched off. Like any changed game package, it needs the game's
file check switched off (Tools > Hash Patcher).

The menu text comes from the game's own words, "Purchase" and its "x#0", so
it reads right in every language the game ships.

The patches are made by `tools/build_stew_multi_pull.py` from the stock
`BrgGame.upk` of game 1.89.
