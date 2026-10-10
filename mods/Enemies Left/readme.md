# Enemies Left

Adds one line to the HUD while you explore the Tower: how many enemies are
still alive on the floor, and how many it started with.

    Enemies left: 12 / 20

The numbers are the game's own. "Alive" is the count the game itself uses to
decide a floor's enemies are all dead - Haters and monsters, not the skeletons
a boss raises - and the total is what the floor started with.

It only shows when the game shows its HUD: not in menus, cutscenes, while the
screen fades, or after death, and never in the Waiting Room.

## Configuration

- **Across / Down** - where it goes, in pixels on the game's 1920 x 1080
  screen (it scales to yours). It starts near the top right.
- **Line up the text** - starting at that point, centred on it, or ending at
  it. "Ending at that point" keeps it tidy against the right edge.
- **Text size** - 100% is the game's own HUD text.
- **Text colour** - white, yellow, orange, red, pink, purple, light blue, cyan
  or green. It always has a black shadow, so it reads on any floor.
- **Also show how many the floor started with** - off shows just
  "Enemies left: 12".

Change any of them and click **Save Mod List**; the game picks them up the
next time it starts.

The words are in every language the game has.

## What it changes

- `BrgGame.upk`: one function, `BrgHUD.PostRender`, as a `.PackagePatch`
  (`tfc`). Right after the game draws its UI each frame, it draws the line.
  Like any changed game package, it needs the game's file check switched off
  (Tools > Hash Patcher).
- `masters.db`: five numbers in `master_const_float` (`LID_ENEMY_HUD_X`, `_Y`,
  `_SCALE`, `_ALIGN`, `_COLOUR`) that the patch reads each frame, and the line's words in
  `master_text` (`TUB.TXT_LID_ENEMIES_LEFT`).

Switching it off puts both back.

The patch is made by `tools/build_enemies_left.py` from the stock
`BrgGame.upk` (1.89 and 1.91 have the same scripts).

## Changes

- **0.2.2** - no longer shows in the hub. Back from the Tower it kept the last
  floor's numbers on screen ("Enemies left: 0 / 20") until the next floor.
- **0.2.1** - fixes a crash on arriving on a floor. 0.2.0 read the game's text
  font with the instruction for a different kind of list, which went wrong at
  random depending on where things sat in memory - more often with some other
  mods' changes switched on.
