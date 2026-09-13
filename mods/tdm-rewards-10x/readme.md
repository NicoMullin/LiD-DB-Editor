# TDM Rewards x10

Everything Tokyo Death Metro pays out in Kill Coins and SP, multiplied by
**10**.

## What it changes

Two tables, as two patches you can switch on independently.

**Rank bonuses** - `master_tdm_rank`, all **15** ranks: `win_bns_spirit`,
`win_bns_money`, `def_bns_spirit`, `def_bns_money`, `weekly_bns_spirit`,
`weekly_bns_money`, and the two caps that go with the defence bonuses
(`def_bns_spirit_limit`, `def_bns_money_limit`) so a bigger bonus is not
immediately clipped back to the old ceiling.

**Battle payouts** - `master_war_reward`, all **48** rows: `win_spirit`,
`win_money`, `lose_spirit`, `lose_money`. Raiding and defending, and the
consolation for losing as well as the reward for winning.

## What it deliberately leaves alone

- **Mystery bags** (`win_mysterybag`, `def_bns_bag` and friends). Those hold
  names and comma-separated odds, not amounts - multiplying them is nonsense.
- **Death Medals** (`win_medal`, `lose_medal`). Every one is zero in this
  build, so there is nothing to multiply.
- **Rank thresholds** (`point_min`, `point_max`) and the battle conditions.
  This changes what you are paid, not what you have to do to earn it.

## Pick one

x2, x5 and x10 rewrite the same numbers, so each names the other two in
`conflicts_with`.

## Not yet confirmed in game

Verified against the database - the right rows change, by the right amount, and
unticking puts them back exactly - but nobody has played with it yet. Values
this size could still meet a cap somewhere the database does not know about.

## Reverting

Automatic. The manager copies every row it is about to change into
`snapshots/tdm-rewards-10x.json` first, so unticking it and saving puts the original
numbers back.

## Built for game 5.0.3.0.0 - 1.87
