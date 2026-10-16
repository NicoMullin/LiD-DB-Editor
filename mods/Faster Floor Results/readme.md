# Faster Floor Results

After you take an escalator, the floor results screen replays your route on
the floor map before it lets you carry on. The next floor is often loaded long
before the replay ends. This mod speeds the replay up, so the Next prompt
shows up about as soon as the floor is ready.

This is a first try (0.1.0): it changes the two timings the screen reads from
the game's UI settings. If the prompt still holds you up, tell me how long and
on which screen.

## Your value

**Replay speed** - default **600**, from **30** (stock) to **6000**.

Select this mod and open the **Configuration** tab on the right to change it,
or right-click it and pick **Change values...**. Press Enter or click away,
then **Save Mod List**.

## Parts

| Part   | What it does                                                         | Ships |
|--------|----------------------------------------------------------------------|-------|
| replay | The route replay at your speed (`mPlayRate`, stock 30)               | on    |
| map    | The map opens and closes in 0.1 s instead of 0.6 s each              | on    |

## What it changes

Three keys in `BrgGame/Config/SteamPCRelease-BrgUI.ini`, the game's own UI
settings file. The rest of the file is left exactly as it is.

| Section                             | Key                      | Stock |
|-------------------------------------|--------------------------|-------|
| `BrgGame.BrgUIMenu_FloorResult`     | `mPlayRate`              | 30.0  |
| `BrgGame.BrgUIMenu_FloorResult_Map` | `mStartAnimationSeconds` | 0.6   |
| `BrgGame.BrgUIMenu_FloorResult_Map` | `mEndAnimationSeconds`   | 0.6   |

The game checks this file at startup, so the mod needs the game's file check
off for `SteamPCRelease-BrgUI.ini`. The manager offers to do that when you
save.

## Reverting

Automatic. The manager keeps the stock file the first time it changes it;
unticking the mod and saving puts it back.

## Built for game 5.0.4.4.0 - 1.91
