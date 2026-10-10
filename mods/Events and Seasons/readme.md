# Events and Seasons

Brings back the online game's events. The online game switched them on and off
from its server; the offline release still reads most of them from
`masters.db`, but every date in there has run out. This mod writes its own
rows, so nothing of the game's own is redated, and switching it off puts every
table back as it was.

Each part is set in Configuration. Change any of them and click **Save Mod
List**; the game picks them up the next time it starts.

## Hub festivals

- **Mushroom Club festival** - the Mushroom Club in its festival look, as
  during the stew festivals. The Super Stew Festival turns this on by itself.
- **Chokufunsha festival lights** - the neon lights on the parts shop.
- **TDM festival sign** - the animated Fort-kun sign.

All three on by default.

## Super Stew Festival

A festival stew at the Mushroom Stew shop, with the festival icon, its banner
and a flyer in the hub. One random decal a pull, from the decals you choose
(every ★★★ and up from the normal stew, until you pick), with the ★★★★ and
★★★★★ ones boosted - 3x by default, as the online festivals advertised.

- **Pay with** - Kill Coins (100,000 a pull by default) or Death Metals (10).
- **Decals in the festival stew** - the same decal picker as the Death Metals
  mod's stew.
- **Festival banner and hub flyer** - the online banner showed a picture from
  the game's server; offline the frame, title and text show without it.

It needs the **Stew Multi Pull plugin, `lid-stewpull.dll`** (in the manager:
**Get mods...**, then **Stew Multi Pull**; before Death Metals 0.6.0 this was
in `lid-deathmetals.dll`). The offline server only offers the first stew in
`masters.db`, and draws every normal stew from the one list kept in your save;
the plugin offers them all and gives this one its own list. Paying in Death Metals also needs the **Death
Metals mod** on: its `BrgGame.upk` patch takes out the filter that hides Death
Metal stews. With the Stew Multi Pull mod, its Purchase xN gives no decal twice.

## Bonuses

All off by default.

- **Free continues** - how many continues are free each day, as the online
  game's Free Continue campaigns gave (1, 3 or 4). They are topped up at the
  game's daily reset (10:00 UTC), so they show from the next reset.
- **Fighter recovery discount** - this much off recovering fighters, on top
  of any Express Pass discount. The event was 50%.
- **Chokufunsha sale weapons** - the sale's Jungle Machete E RG and Iron
  Hammer RG in the parts shop.

## Seasonal mushrooms

The game itself picks the season from your PC's month: winter (Snowcaps) in
January and February, spring (Eggshrooms) in April and May, summer (Parasol
Shrooms) in July and August, autumn (Pumpkinshrooms) in October and November,
and none in March, June, September and December.

- **In their own months** - as the game does it (the default).
- **One season's all year** - that season's mushrooms in every month, instead
  of the others'.
- **All four seasons, all year** - every season's mushrooms in every month,
  each a quarter as common, so seasonal mushrooms overall turn up about as
  often as in a season month.

The title screen and the hub's seasonal decorations still follow the month.

## Collab Haters

- **Travis Imposters** (No More Heroes), **WarDogs** (World of Tanks), or
  **both**, on the Tower's floors. Off by default.

This uses the game's own event spawn pattern, which also has more Haters
about. The WarDogs wear World of Tanks gear the PC release left out; the LET
IT DIE Crossover Content pack puts it back, so switch that on first.

## Quests

The game ships every weekly and season quest that ran online from January 2019
to January 2026, hidden and out of date: 365 weeks of 15 or 16 quests, and 28
seasons of six. Behind them are 43 quests, repeated - every week has Screamers,
Haters, a Jackal, TDM attacks and defences and the Tengoku floors, and one of
the four Forcemen, beasts and mushrooms in turn.

- **Quest schedule**
  - **As online** (the default): the weeks as they ran, each one the week that
    ran in that week of the year, a new set every Monday at 10:00 UTC, and the
    seasons by their seasons - put back on the calendar 365 weeks on, so they
    keep coming until January 2033.
  - **A new draw every few days**: every *Days per period* (7 starts them on
    Mondays), a fresh set of *Quests per period* drawn at random from the
    quests you picked. The draw for each period is worked out from a fixed
    formula, so saving the mod list again never reshuffles the period you are
    in. They are written up to a year ahead of the day you save (fewer when the
    periods hold many quests - about 6,000 quests in all); saving again later
    carries them on.
  - **All of them, all the time**: every quest you picked, always on the board.
    Its first-clear reward comes once; each clear after pays the smaller one.
  - **Off**.
- **Quests in the pool** - a window listing the 43 quests, with what each asks,
  the floor it needs, and its rewards. Only the ticked ones appear, whichever
  schedule you choose. Every one is ticked until you change it.

A quest still needs its floor reached, as it always did. In "As online" and
"A new draw" each week's or period's quest is new to your save, so its
first-clear reward comes again each time, as online.

The one-off event quests (anniversaries, Valentine's, collabs) are not in this
version: the game kept their names and rewards but not what they ask for, so
each has to be rebuilt.

## What it changes

Only `masters.db`:

- `master_event_schedule` - its own rows (`LID_EVENT_...`) for the hub
  festivals (`MUSHROOM_FES`, `CHOKU_FES`, `TDM_FES`),
  `RECOVERY_DISCOUNT` and `PART_SALE`, running from 1970 to 2038.
- `master_skillgacha`, `master_skillgacha_odds`, `master_shop_product`,
  `master_shop_product_price`, `master_text` - the festival stew
  (`SKLGACH_RANDOM_FEST`, the stew and its list of decals, its price
  `PRD_SKILL_GACHA_FEST`, and its words in every language).
- `master_mushroom_gen`, `master_mushroom_gen_odds` - the seasonal mushrooms.
- `master_model_schedule`, `master_model_group` - the collab Haters.
- `master_quest` and its four `master_quest_param_*` tables - the weekly and season quests:
  their dates moved and `old_flg` cleared, or copies of the picked ones (`LID_Q_...`).
