Download the zip below, unzip it anywhere **writable** — not Program Files —
and run `LID DB Mod Manager.exe`.

**No Python needed.** Python, Qt and the C++ runtime are all inside the build.

Windows will warn that the publisher is unknown, because the build is unsigned.
Click **More info**, then **Run anyway**. Some antivirus flags PyInstaller
executables for the same reason — the SHA-256 of this zip is at the bottom of
these notes, so you can check the file you got is the one built here.

Keep the whole folder together: the `.exe` needs `_internal\` beside it, and
`mods\` is where your mods live.

### Updating from 0.11.0

Close the game, then **Help → Check for updates...** and **Update now**. It
downloads this version, checks it against the SHA-256 GitHub lists for it, and
restarts as 0.12.6. Your mods, settings, snapshots and backups stay where they
are, and the folder keeps its name. Then click **Save Mod List** once, so the
mods updated in this release are applied again.

### Updating from 0.10.1 or older

Unzip this release into a folder of its own and close your old copy. On its
first start this one asks whether you are new or updating: click **I'm
updating** and pick your old copy's folder. It brings over your mod list, load
order, settings, the record of what each mod changed, your backups and your own
mods — plus the clean databases older versions carried — so nothing has to be
switched off and on again. Then click **Save Mod List** once. The old folder is
only read; delete it when you are happy. From then on, updates are one click.

### Before you start

Point it at your `masters.db`. It does not have to be untouched: the manager
compares it with a clean copy of the same game build, says which mods it can
already see in there, and offers to take them over. The moment you pick a
database it also keeps a permanent `masters.db.original` copy.

### What's new in Beta V0.12.6

Everything since 0.11.0.

**Game update 1.91.** The clean database for game 1.91 can be downloaded from
**Tools → Download a clean database...**, and every mod that ships with the
manager works on it. One bug is fixed along the way: after a game update, the
manager could rebuild `BrgGame.upk` from the copy it kept of the *old* game
version, so the game ran old scripts. It now checks a kept copy against the
game before using it, and if the copy is out of date it starts again from the
game's own file — or, if that file is modded too, it stops and asks you to
verify the game files in Steam.

**Get mods.** Some mods need a plugin: a DLL the game loads through the LiD
Plugin Loader. Those no longer come with the manager. Each has its own GitHub
page, where GitHub builds and tests every release. **Get mods...** (top right,
or **Mods → Get mods that need a plugin...**) lists them, shows which you have,
and gets one with a click. It shows every file it will write before writing
anything, puts the mod in the manager and its plugin in the game, and gets the
Plugin Loader too if you do not have it. Close the game first. There are six:

- **Stew Multi Pull**: Purchase x5 to x25 at the stew, with every decal shown
  together at the end. With its plugin the whole run is paid for, drawn and
  saved in one go, so x25 takes about as long as one pull.
- **Death Metals**: Death Metal rewards, the count on screen, Uncle Death's
  exchange, and Express Passes, the Coin Locker and a stew paid in Death Metals.
- **Timers**: crafting, research and facility upgrades take time again, with
  Uncle Death's time skip.
- **Login Bonus**: a random daily login bonus, from the online rewards for your
  floor or your own lists.
- **Fighter Class Passives**: a skill decal for each fighter class, always on,
  and the pause-map reveals.
- **Offline Fixes**: bugs in the game's own offline mode. The first one stops
  the game closing when a Hater dies as you take the stairs.

**Stew Multi Pull has moved.** It came with 0.11.0. It now lives on its own
GitHub page with its plugin (see Get mods above). If you already have it, it
keeps working as before, one pull after another; **Get mods** updates it and
keeps your setting.

**New mods that come with the manager:**

- **Enemies Left**: "Enemies left: 12 / 20" on the HUD while you explore, so you
  know when a floor is cleared. You set where it sits, its size and colour, and
  whether it shows the total. It hides in the Waiting Room.
- **Events and Seasons**: the online game's events, each switched on in the
  mod's settings. You get the hub's festival looks, the Super Stew Festival (for
  Kill Coins or Death Metals, with its own decals), the fighter recovery
  discount, the Chokufunsha sale, seasonal mushrooms all year, the Travis
  Imposter and WarDogs collab Haters, free continues, and the weekly and season
  quests. The festival stew needs the Stew Multi Pull plugin.
- **Golden Beasts**: golden beasts turn up as often as you like, on every floor
  — or nearly every beast in the Tower is golden.
- **Vending Machine**: the Waiting Room vending machine sells the online game's
  stock again, for Kill Coins or Death Metals. That means the faction metals,
  materials and mushrooms on its daily list and the weekly blueprints, at the
  online prices. You can also make your own list, priced and bundled as you
  like.

**Crossover Content 3.79.1.** S3er0i9ng's pack, refreshed for game 1.91:

- **Artwork:** 45 artwork files now match game 1.91. Before, the manager left
  them out with a "made for a different game build" warning.
- **Text:** three collab decals' descriptions are fixed in every language.
- **Equipment sets:** DUKE-WoT Sniper Rifle04, Assault Master G and White Kat
  can each be switched off in the mod's settings. They are on until you change
  that.

**Colored PlayStation Buttons 1.5** replaces 1.4. If you had 1.4 on, 1.5 takes
its place.

**Richer mod settings.** Mods can now give you:

- **Decals:** a window to tick decals in, with search, sorting by name, stars
  or category, and what each decal does.
- **Lists:** a window to tick entries off the mod's own list, such as which
  quests can appear.
- **Shop lists:** a list you edit, with prices and bundle sizes.
- **Rewards:** a list of rewards with the chance of each.
- **Parts:** parts that only apply when a setting is on.

A mod can also say which manager version it needs, and an older manager tells
you instead of applying it wrongly.

**Start a new game day.** **Tools → Start a new game day...** makes the game
treat your next start as a new day, so the daily things come round again
without waiting. It backs your save up first, every time, and will not touch
the save while the game is running.

The manager now goes online in exactly four cases, each one your own click or
setting: downloading a clean database, checking for updates, updating, and
getting mods. It only ever talks to GitHub.
