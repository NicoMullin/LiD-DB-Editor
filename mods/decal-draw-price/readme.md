# Decal Draw Price

A draw from the Mushroom Club's decal pool costs *your chosen price in* KC instead of 50,000.

## Your value

**Price** - default **10,000 KC**, anything from 1 KC to 1,000,000 KC.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

The stock price is 50,000 KC.

## What it changes

One value: `master_shop_product_price.price` on the row `PRD_SKILL_GACHA`.

That row is the draw's price. `master_skillgacha` holds the draw itself (`SKLGACH_NORMAL_OFFLINE`) and points at `PRD_SKILL_GACHA` for what a pull costs. Individual decal prices, the odds and what is in the pool are left alone.

The row is picked by name, not by price, on purpose: `PRD_CONTINUE_6`, a revive price, also costs exactly 50,000, and a mod matching "whatever costs 50,000" would change that too.

This replaces `decal-cost-25k`, `-10k` and `-5k`. Their version 1.0.0 lowered 34 individual decal prices instead of the draw.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
