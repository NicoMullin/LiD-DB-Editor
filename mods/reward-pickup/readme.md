# Reward Pickup

Dropped Kill Coins, SPLithium and Bloodnium come straight to you instead of
scattering across the floor and being collected a piece at a time.

## What it changes

A payout is not one object. Every currency spawns as up to twenty separate
actors that arc out from where the drop happened, bounce off walls, roll under
things and only settle after a second or two. None of that timing is a balance
rule in `masters.db` - it is presentation, driven by a handful of numbers on one
UnrealScript class:

    class BrgUIDebugEditParams extends Object native config(UIDebugEditParams)

An Unreal `config` class reads its values out of a `.ini` in `BrgGame/Config` at
startup, and falls back to the values compiled into it when the file is not
there. This is one the game never shipped, exactly as `BrgGraphicsConfig` reads
`BrgGraphicsConfig.ini` beside it. So this mod does not edit anything the game
came with: it writes the file the game was always willing to read, and switching
the mod off deletes it again.

**Spawn Wait** is what staggers the pieces, in hundredths of a second, which is
why a big payout trickles in rather than appearing at once. **Full Auto Mode** is
the game's own name for sending a piece straight to the player.

| Setting | Meaning                                              |
|---------|------------------------------------------------------|
| **0**   | Every piece appears at once (default)                |
| **4**   | What the game ships with                             |
| **20**  | A fifth of a second between pieces                   |

## Why the coins needed extra

The coin is the only currency that is *thrown*. Everything else is placed where
it fell, which is why SPLithium is at your feet while coins are still in the air.
Full Auto Mode will not collect a coin until it has settled, so instant pickup on
its own would still leave the coins lagging behind.

So the four numbers that launch it - `mCoin_ImplusZ_Min/Max` and
`mCoin_ImplusXY_Min/Max` - are set to zero as part of the same change, leaving
coins where they drop.

If you also have **Instant Drops** with its "Stop coins being thrown" part
ticked, that reaches the same behaviour from the other side, by rewriting the
launch speeds compiled into `BrgGame.upk`. Having both is harmless - they agree
on the answer - and either one on its own is enough.

## Fewer, bigger pieces

A second part, **switched off until you tick it**. Look under the mod in the list
and tick "Fewer, bigger pieces".

`DivNum` is how finely a payout is split into pieces and `MinNum` is the floor on
how many appear at all. Halving one and lowering the other pays exactly the same
amount in far fewer actors, which is worth having on a busy floor or a weak
machine. Nothing is lost - only the number of things the physics engine has to
push around.

## What it writes

`BrgGame/Config/BrgUIDebugEditParams.ini`, under
`[BrgGame.BrgUIDebugEditParams]`:

| Key                                                     | On      |
|---------------------------------------------------------|---------|
| `m{Coin,Spirit,Bloodnium}_SpawnWait`                    | setting |
| `m{Coin,Spirit,Bloodnium}_FullAutoMode`                 | 1       |
| `mCoin_Implus{Z,XY}_{Min,Max}`                          | 0       |
| `m{Coin,Spirit,Bloodnium}_{Coin,Spirit,Bloodnium}DivNum`| 2       |
| `m{Coin,Spirit,Bloodnium}_Min{Coin,Spirit,Bloodnium}Num`| 3       |

The last two rows are the "Fewer, bigger pieces" part. Only the keys listed here
are written: if you already have a file of your own at that path, everything else
in it is left exactly as it was, and switching this mod off puts it back byte for
byte.

## The game's file check

The game keeps a checksum for its config files as well as its packages, and this
one is on the list - so it refuses to start once the file exists unless the check
is off for it. The manager does that for you when you save; you can also do it by
hand under **Tools > Hash Patcher**. It changes one byte in the executable, no
program code, and it can be put back at any time.

## Credit

The findings - that this class reads a file the game never shipped, which keys
matter, and why the coin needs the extra four - are from **Claudia-diva's
LID-Patches** (MIT), where they are the `drops` patch and its `fewer_pieces`
option.
