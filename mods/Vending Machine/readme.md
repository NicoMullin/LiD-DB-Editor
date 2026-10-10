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

## How it differs from online

- **Everything is on sale every day.** Online, the machine drew 8 of the daily
  goods at random for each player, and 8 blueprints each Thursday. Offline the
  game has no random pick; it sells every good marked "always in stock". A
  plugin to bring the daily draw back is planned.
- **A blueprint can be bought once a day**, not once for good.

## What it changes

Adds rows to `master_automaticshop_lineup` on the `COMMON` list, the one the
offline game fills the Buy tab from: goods 90001 and up as online, 100000 and
up for your own list. As online with Death Metals in the mix, it also sets the
three cosmetic armours (goods 26702-26704) to Death Metals. Nothing else, and
switching it off puts the table back as it was.

The Bloodnium and R-Point exchanges are unchanged: they still have their online
lists. The Bloodnium one changes with the day of the week.
