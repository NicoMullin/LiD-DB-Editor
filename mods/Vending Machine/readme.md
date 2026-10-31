# Vending Machine

Puts the online game's stock back in Hernia, the vending machine in the
Waiting Room. Offline, its Buy tab only sells the seven things it always had:
two Barbmeats, a Solo Shelter, Tryptamine D and three cosmetic armours. Online
it also sold faction metals, materials and mushrooms every day, and blueprints
every week. Those lists came from the online server and were lost with it. They
were rebuilt here from the Let It Die wiki's Hernia page (as archived in June
2023), and every price was checked against the game's own data.

Or sell your own list instead: anything the game has but weapons and armour,
each priced and bundled the way you want.

Set it in Configuration and click **Save Mod List**. The machine restocks once
a game day (10:00 UTC), so the goods show up after the next daily reset. Tools >
**Start a new game day** brings that forward.

## What it sells

| Group          | Goods | Online                                                          |
|----------------|-------|-----------------------------------------------------------------|
| Faction metals | 24    | Blue to Orange of all four factions, one or five, for DM        |
| Materials      | 37    | 1★ to 7★ wood, iron, aluminium, oil and copper, 6★ and 7★ fibre |
| Mushrooms      | 12    | Silver and Golden Skillshrooms, Croakstool, 9 other Golden ones |
| Blueprints     | 40    | The weekly ones: 10 weapons and 30 armour pieces, for DM        |

Materials and mushrooms came for Kill Coins or Death Metals, many also in
bundles of 5. A single costs what the game itself says. A bundle has its own
price: 4★ and up cost 4 singles for 5; 1★ to 3★ came only in fives.

## Settings

- **Machine**: **As online** (the default) or **Your own list**. Either way the
  seven things it always sold stay. Switch the mod off for the machine exactly
  as the game has it.

### As online

- **Pay with**
  - **Kill Coins and Death Metals, as online** (the default): each good the way
    it was sold. The three cosmetic armours go back to Death Metals too
    (10, 16 and 28).
  - **Kill Coins only**: one Kill Coin price for each. Goods that were Death
    Metals only cost the game's own Kill Coin price, and a bundle of 5 costs 4.
  - **Death Metals only**: the online Death Metal prices for everything.
- **Weekly blueprints**: on by default.
- **What it sells**: tick or untick any of the 113 goods.

### Your own list

**Your list** → **Edit the list...** opens the list. **Add goods...** shows
everything you can put on it: consumables, materials, blueprints and mushrooms,
named as the game names them, with the game's own prices. For each good on the
list:

| Column   | What it is                                                                   |
|----------|------------------------------------------------------------------------------|
| Bundle   | How many one purchase gives, 1 to 999                                        |
| Pay with | Kill Coins or Death Metals                                                   |
| Price    | For the whole bundle. **Game's price** (0) is the game's price for that many |
| A day    | How many times it can be bought before the next daily reset, or no limit     |

The same good can be on the list more than once, say singly for Kill Coins and
in fives for Death Metals. Move up and Move down set the order on the Buy tab.
The list starts as the online stock; **Back to the default** puts that back.

The game only knows "once a day" or "no limit", so a good allowed 3 times a
day is put on the machine 3 times, each once a day.

Death Metal prices need the **Death Metals** mod, which gives you Death Metals
to spend.

### Rotation (needs the Vending Machine add-on)

Online, the machine drew 8 of the daily goods at random for each player, and 8
blueprints each Thursday. Offline the game has no random pick: it sells every
good marked "always in stock". The **Vending Machine add-on** (Get mods...,
manager 0.13.4 or newer) is a plugin that brings the draw back:

- **Take turns, as online**: off by default (everything on sale every day). On:
  the seven things the machine always sold stay every day, and the rest take
  turns.
- **Goods picked a day**: 8, as online. A new pick at each daily restock.
- **Blueprints picked a week**: 8, as online, new each Thursday (10:00 UTC).

As online, every good and every blueprint takes turns, so **What it sells**
and **Weekly blueprints** are greyed out while Rotation is on. With your own list,
**Your list** is sold every day, and two more lists take turns:

- **Your random pool**: "Goods picked a day" of them are picked each day.
- **Your weekly list**: "Blueprints picked a week" of them are picked each
  week. Made for blueprints, but any good can go on it.

On these two, a limit a day means once on a day the good is picked.

Without the add-on, Rotation leaves only the seven; switch it off, or get the
add-on. The add-on's plugin is switched on and off with this mod.

### Menu

Two more entries for the machine's own menu, both off by default. They are
**3bgdragon**'s, from
[Vending Machine Enhancement](https://github.com/3bgdragon/let-it-die-vending-enhancement),
included with their permission.

- **Refill ammo** adds **Ammo (20% cost)**. It lists your guns that are not
  full; pick one and its ammo is filled back up, for a fifth of what that
  weapon costs to buy at its level, rounded up, paid from your safe. Durability
  is not repaired.
- **Change decals** adds **Decals**. It opens the Mushroom Stew's decal screen
  for the fighter you are using, to put decals on or take them off, with the
  stew's own rules for each kind of decal.

Both change BrgGame.upk, so the game's check of that file has to be off (the
manager says how when you save). They work beside Timers, which changes the
same shop code as the ammo refill, and beside Stew Multi Pull, which changes
the same stew screen as the decals: each mod keeps its change.

They are built for game 1.92. A game update that changes BrgGame.upk may stop
them until they are rebuilt; the manager then says the package is not the one
they were made for, and changes nothing.

## How it differs from online

- **Without the add-on, everything is on sale every day** (see Rotation).
- **A blueprint can be bought once a day**, not once for good.

## What it changes

Adds rows to `master_automaticshop_lineup` on the `COMMON` list, the one the
offline game fills the Buy tab from: goods 90001 and up as online, 100000 and
up for your own list. As online with Death Metals in the mix, it also sets the
three cosmetic armours (goods 26702-26704) to Death Metals.

With Rotation on, the goods that take turns go on the online game's own
picking list instead, `AP` (120000 and up for your random pool), and the
weekly ones on `LID_WEEKLY` (140000 and up for your weekly list), both not
"always in stock". It sets how many are picked a day in
`master_automaticshop_schedule` (`purchase_goods_min` and `_max`, as online),
and a week in `master_const_int` (`LID_VENDING_WEEKLY_COUNT`).

With Refill ammo or Change decals on, it also changes up to 11 functions in
BrgGame.upk: the machine's menu (`BrgUIMenu_ItemVendingMachine`), the shop's
sell list it reuses for the ammo (`BrgUIMenu_ShopBase`) and the stew screen it
opens for the decals (`BrgUIMenu_SkillExchange`). `tools/build_vending_extras.py`
builds them from 3bgdragon's own reviewed code.

Nothing else, and switching it off puts the tables and the package back as
they were.

The Bloodnium and R-Point exchanges are unchanged: they still have their online
lists. The Bloodnium one changes with the day of the week.
