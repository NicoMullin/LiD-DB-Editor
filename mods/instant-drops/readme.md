# Instant Drops

Kills drop their reward the moment the enemy dies, instead of two seconds later.

## What it changes

When something dies, the game starts a timer and nothing appears until it runs
out. The wait is one number - twenty tenths of a second - and it is not in
`masters.db`: it is compiled into the script bytecode inside `BrgGame.upk`, in
a table entry the game calls **Item Drop Delay Time**.

A kill whose reward is a **mushroom** has a second wait on top. The mushroom
grows out of the corpse, and that starts its own timer - **Start Mushroom Delay
Time**, another twenty tenths - only once the first one has run out. Version
1.1 changed the first wait and not this one, which is why some kills were
instant and others still lagged: coins and parts were instant, mushrooms were
not.

This mod rewrites those two bytes, both to the same setting. Everything else in
the package is left exactly as the game shipped it.

The mushroom also waits for the body to stop moving. A corpse that is still
tumbling - knocked off a ledge, or flung by a heavy hit - holds its mushroom
until it has been still for a moment. That check is not a timer this mod
changes, so a mushroom on a rolling body can still take a beat.

| Setting | Meaning |
|---------|-----------------------------------------------|
| **0**   | The reward drops as the enemy dies (default)  |
| **10**  | One second, and one more for a mushroom       |
| **20**  | What the game ships with                      |

The two parts are independent: either can be on without the other.

## Stop coins being thrown

A second part, **switched off until you tick it**. Look under the mod in the
list and tick "Stop coins being thrown".

The coin is the only currency that is thrown. Every other drop is placed where
it fell, which is why SPLithium is on the floor at once while coins are still
arcing off walls and rolling under things. The coin is launched by two numbers
next to the label **Speed Z** and two next to **Speed XY**, with **Max Move
Time** capping how long it may keep moving. Taking the four launch numbers to
zero leaves coins where they drop.

It reaches coins from chests and breakables as well as from kills, because the
numbers belong to the coin itself rather than to what dropped it.

Max Move Time is set to a tenth of a second rather than zero: zero is a
meaningless duration, and the hop a coin does when you collect it is timed
against the same clock.

## Before it will apply

**The game's file check has to be off for `BrgGame.upk`.** The game keeps a
checksum for its own packages and refuses a changed one at startup, naming the
file and nothing else. The manager checks this before it writes anything and
tells you if it is still on.

Switching the mod off puts the game's own package back, byte for byte.

## Known quirk

Taking an escalator while coins are still in the air has been seen to crash the
game with the wait removed. Coins settle in about a second, so this is only
reachable by sprinting into an escalator the instant something dies. Set the
wait to 5 or 10 if you run into it.

## Credit

The findings - which numbers these are, where they sit, and that the bytes
around them identify them across game builds - are from **Claudia-diva's
LID-Patches** (MIT), where they are the `dropdelay` patch and its `flat_coins`
option. The mushroom timer sits in the same table, one entry further on.
