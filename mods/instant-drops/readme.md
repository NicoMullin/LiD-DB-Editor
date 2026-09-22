# Instant Drops

Kills drop their reward the moment the enemy dies, instead of two seconds later.

## What it changes

When something dies, the game starts a timer and nothing appears until it runs
out. The wait is one number - twenty tenths of a second - and it is not in
`masters.db`: it is compiled into the script bytecode inside `BrgGame.upk`, in
a table entry the game calls **Item Drop Delay Time**.

This mod rewrites that one byte. Everything else in the package is left exactly
as the game shipped it.

| Setting | Meaning |
|---------|-----------------------------------------------|
| **0**   | The reward drops as the enemy dies (default)  |
| **10**  | One second                                    |
| **20**  | What the game ships with                      |

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

The finding - which number it is, where it sits, and that the bytes around it
identify it across game builds - is from **Claudia-diva's LID-Patches** (MIT),
where it is the `dropdelay` patch.
