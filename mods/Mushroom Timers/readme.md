# Mushroom Timers

Choose how long mushroom effects last.

## Settings

| Setting              | What it does                                                         | Default                 |
|----------------------|----------------------------------------------------------------------|-------------------------|
| Set the times        | **Same for every mushroom** or **Each mushroom on its own**          | Same for every mushroom |
| How long             | Same for every mushroom: every effect at this % of its own time      | 200%                    |
| Thrown mushrooms too | Also change how long a thrown mushroom's effect lasts on an enemy    | on                      |
| One per mushroom     | Each mushroom on its own: seconds when eaten, for 33 mushrooms       | the game's own          |

**Each mushroom on its own** sets how long a mushroom lasts when you eat it.
Grilled and thrown change by the same share: if Crushroom goes from 30 to 60
seconds, Grilled Crushroom goes from 40 to 80. Each box starts at the game's
own time, so a mushroom you leave alone stays as it is.

**Same for every mushroom** changes every effect, including the mushrooms that
hurt you when eaten (Fluffy Fungus puts you to sleep, Doomshroom poisons you).
To make only the helpful ones longer, use **Each mushroom on its own**.

The mushroom's info box in the game shows the new time on its own, because it
reads the same number.

## The game's own times

In seconds, when eaten / grilled / thrown at an enemy:

| Mushroom              | Eaten | Grilled | Thrown |
|-----------------------|-------|---------|--------|
| Boleoshroom           | 15    | 25      | 15     |
| Brainshroom           | 5     | 10      | 5      |
| Cateye Shroom         | 7     | 11      |        |
| Croakstool            | 30    |         | 30     |
| Crushroom             | 30    | 40      |        |
| Decoyshroom           | 30    | 60      | 30     |
| Doomshroom            | 30    | 60      | 30     |
| Dragonshroom          | 10    | 20      |        |
| Evershroom            | 20    | 30      |        |
| False Brainshroom     | 5     | 10      | 5      |
| Fishy Fungus          | 15    | 25      | 15     |
| Fluffy Fungus         | 20    | 40      | 20     |
| Frongus               | 5     | 10      | 5      |
| Gambleshroom          | 20    | 20      |        |
| Golden Dragonshroom   | 40    |         |        |
| Golden Evershroom     | 60    |         |        |
| Golden Fishy Fungus   | 60    |         | 60     |
| Golden Snailshroom    | 120   |         |        |
| Golden Stingshroom    | 60    |         |        |
| Golden Vampire Fungus | 90    |         |        |
| Guardshroom           | 30    | 40      |        |
| Helmetshroom          | 12    | 15      |        |
| Lavashroom            | 10    | 15      | 10     |
| Red Stingshroom       | 25    | 35      |        |
| Slowmungus            | 10    | 15      |        |
| Snailshroom           | 60    | 90      |        |
| Staminungus           | 30    | 40      |        |
| Stingshroom           | 30    | 40      |        |
| Toughshroom           | 30    | 40      |        |
| Transparungus         | 15    | 25      |        |
| Umbrella Rib Fungus   | 12    | 15      |        |
| Vampire Fungus        | 30    | 40      |        |
| Yogashroom            | 10    | 30      |        |

## What it changes

`tmmin` and `tmmax` (an effect's time in seconds) in `master_mushroom_efc`, for
the effects mushrooms give. Mushrooms that work at once (healing, food,
explosions) have no time and are not touched.

## Reverting

Automatic. The mod is applied against your untouched `masters.db`, so changing
the times never adds up, and unticking it and saving puts the game's own times
back.

## Built for game 5.0.4.4.0 - 1.91
