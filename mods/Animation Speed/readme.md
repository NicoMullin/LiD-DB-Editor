# Animation Speed

Your fighter's slow everyday actions, made quicker: eating, kicking a chest
open, picking things up, putting on gear, catching animals, grilling and
whacking vending machines. Each has its own speed, so you can speed up only
the ones that bother you.

## Settings

| Setting             | What it speeds up                                        | Default |
|---------------------|----------------------------------------------------------|---------|
| Eating              | Eating mushrooms, meat and animals, sitting or standing  | 150%    |
| Kicking chests open | The kick that opens a chest                              | 150%    |
| Picking up items    | Bending down for an item on the floor                    | 150%    |
| Putting on gear     | Picking up a weapon or armour piece and putting it on    | 150%    |
| Catching animals    | Grabbing an animal, and lifting one off the floor        | 150%    |
| Grilling            | Holding a mushroom or animal over a fire                 | 150%    |
| Vending machines    | Whacking a Tower vending machine open, and stepping back | 150%    |

Each goes from **50%** (half speed) to **300%** (three times as fast). **100%**
is the game's own speed.

Select this mod and open the **Configuration** tab on the right to change them,
or right-click it and pick **Change values...**. Then **Save Mod List**.

The action ends when its animation does, so a faster animation is a shorter
wait: at 200% you can move again in half the time. What the action does is the
same - the same food heals you, the same chest drops the same things.

Only your own fighter is sped up. Enemies, Haters and animals play some of the
same animations, and they keep their own speed. An animal you lift up plays its
part at its usual speed, so at a high Catching speed the two may not line up
for a moment.

Shops' buying and recycling scenes and the toilet are not on this list: they
are cutscenes the level plays (Matinee), not your fighter's own animations, so
this mod cannot reach them.

## What it changes

- `BrgGame.upk`: the two functions that start a fighter's animation
  (`BrgPawn_Base.PlayAnimation` and `PlayAnimationUpperPart`). For your own
  fighter they look up a speed for the animation by its name, and play it at
  that speed. An animation without one plays as it always did.
- `master_const_float`: one row per animation, `LID_ANIM_` followed by the
  animation's name, holding its speed (1.5 for 150%). The settings write these.

To speed up another animation, add a row for it, for example
`LID_ANIM_ANI_CH_EVE_VendingMachine` with `1.5`, in your own mod. The game's
animation names are in the `AS_CH_Main_*` packages.

The game checks its packages at startup, so this mod needs the game's file
check off for `BrgGame.upk`. The manager offers to do that when you save.

## Reverting

Automatic. The manager keeps the stock `BrgGame.upk` the first time it changes
it; unticking the mod and saving puts it back and removes the rows.

## Built for game 5.0.5.0.0 - 1.92
