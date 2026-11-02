# Storage Limit

The Coin Locker expands to *your chosen number,* instead of 2,000 — and, if you
want them, two optional switches that change how many slots each purchase adds
and what it costs.

## Your values

Select this mod and open the **Configuration** tab on the right to change any of
them, or right-click it and pick **Change values...**. Press Enter or click
away, then **Save Mod List**.

| Value                         | Default  | Range           | Used                                      |
|-------------------------------|----------|-----------------|-------------------------------------------|
| **Coin Locker slots**         | 10,000   | 2,000 to 20,000 | always                                    |
| **Change slots per purchase** | Off      | On / Off        | —                                         |
| **Slots per purchase**        | 100      | 1 to 1,000      | while "Change slots per purchase" is On   |
| **Change the price**          | Off      | On / Off        | —                                         |
| **Price per purchase**        | 1,000 KC | 1 to 200,000 KC | while "Change the price" is On            |

## The parts

The first part is always on. The other two follow their switches in
**Configuration**: while a switch is Off, its number is greyed out and the game
keeps its own value. They are independent: either can be on without the other.

Before v2.2.0 these two were parts you had to tick in the mod list, and changing
their numbers alone did nothing.

### Coin Locker slots (on)

`master_const_int` `COINLOCKER_EXPAND_LIMIT_COUNT`, stock 2,000. It cannot be
set below stock, so nothing already in the Coin Locker is stranded.

The ceiling was 99,999 (v2.0.0), never confirmed in game at the largest values.
Lowered to 20,000 in v2.0.1 after a related mod (Decal Draw Price) was reported
to crash the game at a very high value — no count/slot field anywhere in the
stock game goes anywhere near as high as the old ceiling did, so this stays a
lot closer to ground the game is known to handle.

### Slots per purchase ("Change slots per purchase" On)

`master_const_int` `COINLOCKER_EXPAND_COUNT`, stock 10.

This is the part that makes a big locker usable. At stock, filling a
10,000-slot locker means buying the expansion **a thousand times**. At 100 it is
a hundred times; at 1,000 it is ten.

| Slots per purchase | Purchases to reach 10,000 | to reach 20,000 |
|--------------------|---------------------------|-----------------|
| 10 (stock)         | 1,000                     | 2,000           |
| 100 (default)      | 100                       | 200             |
| 500                | 20                        | 40              |
| 1,000              | 10                        | 20              |

### Price per purchase ("Change the price" On)

`master_shop_product_price` `price`, for both `PRD_EXPAND_COINLOCKER` (stock
10,000 KC) and `PRD_EXPAND_COINLOCKER_MONEY` (stock 1,000 KC).

The game lists **two** prices for this one expansion. The game's own server
code charges `PRD_EXPAND_COINLOCKER`; this sets both anyway, so there is a
single box rather than two.

Roughly cost-neutral is 100 KC per slot, so 100 slots a purchase balances at
10,000 KC. Or set it to 1 and stop thinking about it.

## Reverting

Automatic. The manager keeps the rows it is about to change before it changes
them, so unticking the mod — or just one of its parts — and saving puts the
stock values back, whatever you had them set to. Switching one of the two
switches Off and saving does the same for that part.

## Built for game 5.0.3.0.0 - 1.87, 1.88 and 1.89

All three builds ship the same values for everything this touches, checked
against the clean databases for each.

Replaces `storage-10000`.
