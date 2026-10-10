# Golden Beasts

More golden beasts in the Tower of Barbs - the Golden Frog, Rat, Scorpion,
Pillbug, Lizard, Turtle, Bird, Snail, Honeycomb, Crab and Fish - or nothing
but golden ones. Set it in Configuration.

## Settings

| Setting       | What it does                                                             | Default      |
|---------------|--------------------------------------------------------------------------|--------------|
| Golden beasts | **More of them**, as set below, or **Nearly every beast is golden**      | More of them |
| How often     | How much more often a golden beast turns up, x1 (the game's odds) to x50 | x3           |

## How it works

Which beast turns up on a floor is drawn from one of the game's beast spawn
lists (`master_beast_gen`): a few beasts, each with a weight. Every floor area
and spawn point names one of these lists.

- **How often** multiplies the share of every golden beast in every list.
  Where the game has golden beasts they are usually 1 to 6% of a list: x3 makes
  that about 3 to 18%, x10 about 10 to 60%. The extra comes out of the normal
  beasts' weights, so each list adds up to the same total as before and every
  normal beast keeps a weight of at least 1.
- **Every floor** gets golden beasts too: twelve of the lists the floors use
  have none, so each beast in them gets its golden twin, at a twentieth of its
  own weight, times How often. This is always on - without it the game crashed
  on the way into a floor.
- **Nearly every beast is golden** hands each beast's weight to its golden
  twin, so a floor still has as many beasts as before. The Red Scorpion's twin
  is the Golden Scorpion and the Goggly Boleo's the Golden Fish. Each normal
  beast stays in its list at weight 1, about 2 beasts in 100. The five fixed
  beasts on 1F, 21F and 31F stay as they are.

Two things the game will not take, found the hard way: a spawn list whose
weights add up to more than they did, and a list with a golden beast but none
of the normal beasts left. Either one crashes the game on the way into a floor,
even a floor that list is not used on. This mod never does either.

The mod is applied against your untouched `masters.db`, so saving again or
changing x3 to x5 never multiplies twice. Switching it off puts the spawn lists
back exactly as they were. New odds take effect on floors generated after you
save: a floor you are already on keeps its beasts.

## What it changes

`master_beast_gen` only.
