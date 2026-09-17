# Revive Cost

Every grade's revive costs *your chosen price in* KC, including the default revive. The sign held up in-game is a texture, so it still shows the old price - you pay *your chosen price in* KC either way.

## Your value

**Price** - default **1 KC**, anything from 1 KC to 1,000,000 KC.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Stock prices run from 1,000 to 50,000 KC depending on grade; this sets them all the same.

## What it changes

`master_shop_product_price.price` on all 8 revive rows (`PRD_CONTINUE`, `_1` to `_6`, `_MONEY`).

Every grade costs the same price you choose.

The price on the sign held up in-game is part of a **texture**, not the database, so it still shows the old number. You pay the price you chose either way.

Replaces `revive-cost-1kc`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
