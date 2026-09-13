# Reward Box Holds 250

The reward box fills up five times less often.

## What it changes

`master_const_int.REWARD_BOX_LIMIT`, from **50** to **250**. One row.

The separate `PRESENT_BOX_LIMIT` - also 50 - is left alone. That is the present
box, not the reward box.

## Not yet confirmed in game

Verified against the database - the right rows change, by the right amount, and
unticking puts them back exactly - but nobody has played with it yet. Values
this size could still meet a cap somewhere the database does not know about.

## Reverting

Automatic. The manager copies every row it is about to change into
`snapshots/reward-box-250.json` first, so unticking it and saving puts the original
numbers back.

## Built for game 5.0.3.0.0 - 1.87
