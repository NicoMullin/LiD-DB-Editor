# LET IT DIE Crossover Content v3.79

**By S3er0i9ng** — included with the manager with their permission.
Original release: <https://letitdiemods.pages.dev/>

Restores cut crossover content: decals in the Mushroom Club draw pool,
blueprint quests, and the models and artwork they need. This copy is the one
for game version 1.91 (Steam build 25767944), from their Mod Manager v1.3.25.

The mod has two parts, and both are needed:

- **Artwork** — 287 `.upk` packages copied into `BrgGame/CookedPCConsole`.
- **Database changes** — 50 decal-pool entries and 32 blueprint quests that make
  that artwork reachable, recorded from the pack's own v3.79 installer.

## Optional equipment

Three sets of gear are separate choices in Configuration, as in S3er0i9ng's
own manager. They are all on to begin with:

| Set                         | What it gives                                                                      |
|-----------------------------|------------------------------------------------------------------------------------|
| **DUKE-WoT Sniper Rifle04** | 1 blueprint quest and its research                                                 |
| **Assault Master G**        | head, top and bottom: 3 blueprint quests and their research                        |
| **White Kat**               | head, top and bottom: 3 blueprint quests and the research for all 15 upgrade tiers |

Switching a set off expires its blueprint quests and closes its research, the
way their manager's safe removal does. Nothing is deleted: the gear's models,
icons and item definitions stay, so anything you already own keeps working.
Switch it back on and save to get the quests and research back.

Tick it and click **Save Mod List**. Unticking it puts the database back and
takes the artwork out of the game.

## 3.79.1

The pack is still v3.79; the manager calls this 3.79.1 so that a copy already
switched on is taken off and put back with the changes below on the next save.

- **45 packages refreshed for game 1.91**: the 12 Warpig outfit pieces and the
  33 No More Heroes decal icons are built on game files that 1.91 changed.
  Until now the manager left those 45 out ("made for a different game build").
- **Decal descriptions**: the pack now also corrects the description text of
  three decals in every language (30 rows of `master_text`).

Do not also run the pack's own installer — the manager already does its job,
and the two would undo each other.
