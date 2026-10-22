Download the zip below, unzip it anywhere **writable** — not Program Files —
and run `LID DB Mod Manager.exe`.

**No Python needed.** Python, Qt and the C++ runtime are all inside the build.

Windows will warn that the publisher is unknown, because the build is unsigned.
Click **More info**, then **Run anyway**. Some antivirus flags PyInstaller
executables for the same reason — the SHA-256 of this zip is at the bottom of
these notes, so you can check the file you got is the one built here.

Keep the whole folder together: the `.exe` needs `_internal\` beside it, and
`mods\` is where your mods live.

### Updating from 0.11.0 or newer

Close the game, then **Help → Check for updates...** and **Update now**. It
downloads this version, checks it against the SHA-256 GitHub lists for it, and
restarts as the new version. Your mods, settings, snapshots and backups stay where they
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

### What's new in Beta V0.13.2

**Linux: GitHub can be reached again.** On many Linux systems (Fedora,
openSUSE, Arch and others), the Linux download could not reach GitHub at all:
the clean database download, **Get mods** and **Check for updates** all
stopped with "certificate verify failed". The program looked for the system's
trusted certificates where Ubuntu keeps them. It now finds them wherever the
system keeps them, and has its own copy for systems with none. Every Linux
build is now checked against GitHub on Fedora, openSUSE, Arch and Debian
before it is released.

**Updating:** 0.13.1 on Linux cannot update itself, because the update check
is what failed. Download 0.13.2 from the release page once and unpack it; on
its first start, click **I'm updating** and pick your 0.13.1 folder. Updates
work from then on. Nothing changes on Windows.

### What's new in Beta V0.13.1

**Linux.** There is now a Linux download beside the Windows one: unpack the
`-linux.tar.gz` and run **LID DB Mod Manager**. It updates itself through
**Help → Check for updates**, like the Windows one. It works with the game
played through Proton, on the Steam Deck in Desktop Mode too. On Linux the
manager now:

- sees the game running under Proton, so it waits for it to close before
  changing anything, and updates the save library when it does;
- finds Steam in its Linux folders, the Flatpak and the Snap;
- writes to the game's own files when a mod spells their names in other
  capitals (Windows treats those as the same file, Linux does not).

Plugin mods need one Steam launch option on Linux; the README's **Linux and
the Steam Deck** section has it. Nothing changes on Windows.

### What's new in Beta V0.13.0

**Your saves, in a new Saves tab.** A library of 30 save slots, each with a
nickname, a summary (Kill Coins, Splithium, Death Metals, time played, highest
floor, blueprints, every fighter's stats) and the mods it was played with.
**Switch to this save** puts it in the game and offers its mod list; **Use its
mod list** ticks those mods, and says so when the game already has them.
Saves are kept up to date when the game closes, backed up to Steam Cloud, and
can be shared with their mods as a `.lidpack`. **Start a new save...** begins a
fresh game, before or after the tutorial. **▶ Play** starts the game.

**A save editor** (**Edit...** on the Saves tab): the account's currencies;
fighters, their Death Bags, gear and decals, and the Fighter Freezer (buy,
delete, bring back, size); the Storage Box; research and blueprints; decals
and weapon mastery; the stew list; quests; dates and timers; and Tengoku runs.
It uses the save's own mods for its limits and lists, lists every change
before writing, and backs the save up first. Built with the help of the save
editors by oberlinx, g3usyk, mrzacarias and the lid-utils author, used with
their permission.

**Borderless Fullscreen** is in **Get mods**: the game fills the monitor with
no title bar.

**Game 1.92.** The update moved things inside BrgGame.upk, which stopped every
mod that changes it. **Quest Limit 0.1.1** and **Skip Intro 0.1.1** are made
for 1.92, and **Get mods** has 1.92 releases of Death Metals, Timers, Stew
Multi Pull and Fighter Class Passives. Colored PlayStation Buttons needs a new
release from its author for 1.92.

**Fixed:** after a game update, a save that failed part way could leave every
later save stopping with "the saved copy of BrgGame.upk is missing", which
Steam's file check could not fix. The manager now rebuilds from the game's
own file instead.

### What's new in Beta V0.12.9

**Plugins are switched in the mod list.** A mod can now name the plugin it
goes with, and **Save Mod List** switches that plugin with it: on while the
mod is ticked, off when it is not. Off means renamed to `.dll.off`, which the
Plugin Loader skips; nothing is deleted. A plugin with nothing else to change
gets a small mod of its own that is just its switch. Close the game first.

Every plugin mod in **Get mods** now names its plugin: Timers 0.3.2, HUD 0.2.1,
Death Metals 0.6.2, Login Bonus 0.1.1, Fighter Class Passives 0.2.2 and Stew
Multi Pull 0.5.1, and **Offline Fixes** gets a small mod that is just its
switch. Death Metals names Stew Multi Pull's plugin too, because its Death
Metal stew needs it; that plugin stays on while either mod is ticked.

**After getting these, tick the mods whose plugins you use**, then save. A
plugin you used without ticking its mod - HUD's or Login Bonus's, say - is
switched off on the next save until its mod is ticked. Updating a
switched-off plugin through Get mods keeps it switched off.

**HUD has grown** (Get mods): on top of durability numbers and the Enemies
Left counter, it now shows floor info, rage, bag space, mushroom timers and
run totals, with a minimap, the bow arc and low-durability colours. Press F8
in the game to move, size and set every part.

**Five new mods, no plugin needed:**

- **Bloodnium Multiplier.** Every kill's Bloodnium x2 up to x100. You see it
  at the end of a Tengoku run, where the total is added up.
- **Quest Limit.** Accept up to 99 quests at once instead of 10.
- **Faster Floor Results.** The route replay after an escalator runs faster,
  so you can carry on about as soon as the next floor is loaded.
- **Skip Intro.** Straight to the title screen, past the logo videos and the
  GungHo, Supertrick and Unreal logos.
- **Mushroom Timers.** How long mushroom effects last: one percentage for all
  of them, or seconds for each mushroom.

### What's new in Beta V0.12.7

**HUD, a new plugin mod in Get mods.** Durability numbers on the weapon bars
("45/60", "45" or "75%"), and the **Enemies Left** counter ("Enemies left:
12 / 20"), drawn by its plugin, `lid-hud.dll`. More HUD parts will follow,
each one switchable and movable.

**Enemies Left has moved into HUD.** It came with 0.12.6. If you have it, it
keeps working as before; when you get HUD, switch the old Enemies Left off
(the manager reports the two as a conflict while both are on).

### What's new in Beta V0.12.6

Everything from 0.11.0 to 0.12.6.

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
  know when a floor is cleared. (Part of HUD since 0.12.7: see above.)
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
