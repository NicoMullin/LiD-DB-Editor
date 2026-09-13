# Bank Limit x10

Every level of both banks holds ten times as much - a zero on the end.

| Level | Vanilla   | With this mod |
|-------|-----------|---------------|
| 1     |    50,000 |       500,000 |
| 99    | 2,560,000 |    25,600,000 |

## What it changes

`master_safe_level."limit"` and `master_spirit_tank_level."limit"`, multiplied
by ten, on all **99 levels of each**. The Safe is your Kill Coins; the Spirit
Tank is your SP. Both start at 50,000 at level 1, so both get the same
treatment.

The two halves are separate patches, so you can keep one and drop the other -
tick the mod, then untick "Kill Coins" or "SP" underneath it.

## What it does not change

- **The upgrade price** (`price`). Levelling a bank still costs what it did.
- **How much a raider can steal** (`rob_limit`). That stays put, so a bigger
  balance is not a bigger target: you keep ten times as much and lose the same.

## Confirmed in game

These values are known to work in play - another mod uses the same ones.

## Reverting

Automatic. The manager copies every row it is about to change into
`snapshots/bank-limit-10x.json` first, so unticking it and saving puts the original
numbers back.

## Built for game 5.0.3.0.0 - 1.87
