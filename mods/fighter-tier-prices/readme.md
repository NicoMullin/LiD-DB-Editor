# Fighter Tier Prices

Every fighter (body) tier unlock price becomes *your chosen price in* KC.

## Your value

**Price** - default **1 KC**, anything from 1 KC to 100,000 KC.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Stock prices run from 1,000 to 100,000 KC; the three free tiers stay free. The
100,000 ceiling is the highest value this field is ever set to anywhere in
the stock game. (v2.0.0 allowed up to 1,000,000 - lowered in v2.0.1 after a
related price mod was reported to crash the game at a very high value.)

## What it changes

`master_body_detail.price` on the 118 tiers that cost anything.

The three tiers that are free in the stock game stay free.

Replaces `body-prices-1kc`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
