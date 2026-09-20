# Reward Box Limit

The reward box holds *your chosen number,* instead of 50.

## Your value

**Reward box slots** - default **250 slots**, anything from 50 slots to 500 slots.

Select this mod and open the **Configuration** tab on the right to change it, or right-click it and pick **Change values...**. Press Enter or click away, then **Save Mod List**.

Stock is 50. It cannot go lower, so nothing waiting in the box is lost.

## What it changes

`master_const_int` `REWARD_BOX_LIMIT`.

It cannot be set below stock, so nothing waiting in the box is lost.

The ceiling was 9,999 (v2.0.0). Lowered to 500 in v2.0.1 after a related mod
(Decal Draw Price) was reported to crash the game at a very high value -
every sibling box-slot field in the stock game is exactly 50, with nothing
anywhere near as high as the old ceiling.

Replaces `reward-box-250`.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes them, so unticking the mod and saving puts the stock values back - whatever you had it set to.

## Built for game 5.0.3.0.0 - 1.87
