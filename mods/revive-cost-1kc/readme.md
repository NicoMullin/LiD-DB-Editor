# Revive Cost 1 Kill Coin (all grades)

Every grade's revive cost becomes 1 KC. Includes the default revive.

## The sign still shows the old price

You will actually be charged 1 KC — the mod changes the real price the game
charges. But the sign held up on screen when you are offered a revive is a
**texture**, not a database string, so it keeps showing the original cost
(5,000 / 10,000 / 20,000 / ... KC depending on grade).

Nothing in `masters.db` can fix that. If you want the displayed number to match
what you pay, you have to replace the sign texture in the game's asset files
yourself. That is outside what this manager does — it only ever touches
`masters.db`.

So: the price is modded, the picture of the price is not. Ignore the sign.

## What it changes

Sets `master_shop_product_price.price` to 1 for every row whose id starts with
`PRD_CONTINUE`.

Revert: automatic snapshot — the manager writes every original price to
`snapshots/revive-cost-1kc.json` before applying, so **Revert Selected** puts
the real prices back.
