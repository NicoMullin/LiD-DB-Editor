# Decals Cost 10,000 KC

The Mushroom Club's 50,000 KC decals drop to **10,000 KC**.

## What it changes

`master_skill.buy_money`, from 50,000 to 10,000, on the **34** skills priced
at exactly 50,000. Thirty-three of those are premium decals; one is not.

Nothing else moves. The other price bands - 18,500, 100,000, 150,000, 200,000
and 250,000 KC - are left exactly as they are, so this touches only the tier
you asked for.

## Pick one

There are three of these: 25,000, 10,000, 5,000 KC. They all rewrite the same 34 prices, so
each names the other two in `conflicts_with` and the manager warns if you tick
more than one.

## Why it cannot drift

The rule is "whatever costs 50,000". Applied twice that would match nothing the
second time, so the mod is marked `"apply": "diff"` - the manager measures it
against an untouched copy of the database every save, where those 34 rows still
cost 50,000.

## Not yet confirmed in game

Verified against the database - the right rows change, by the right amount, and
unticking puts them back exactly - but nobody has played with it yet. Values
this size could still meet a cap somewhere the database does not know about.

## Reverting

Automatic. The manager copies every row it is about to change into
`snapshots/decal-cost-10k.json` first, so unticking it and saving puts the original
numbers back.

## Built for game 5.0.3.0.0 - 1.87
