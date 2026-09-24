Download the zip below, unzip it anywhere **writable** — not Program Files —
and run `LID DB Mod Manager.exe`.

**No Python needed.** Python, Qt and the C++ runtime are all inside the build.

Windows will warn that the publisher is unknown, because the build is unsigned.
Click **More info**, then **Run anyway**. Some antivirus flags PyInstaller
executables for the same reason — the SHA-256 of this zip is at the bottom of
these notes, so you can check the file you got is the one built here.

Keep the whole folder together: the `.exe` needs `_internal\` beside it, and
`mods\` is where your mods live.

### Updating from an earlier release

You do not need a new folder. Either copy this release's `.exe` and `_internal\`
over your old ones, or unzip this one somewhere new and copy `state.json`,
`snapshots\`, `backups\` and your own `mods\` across. Both keep your mod list,
your load order and the copies of the game files your mods replaced. Do not run
two folders against the same game — each keeps its own record of what it
applied. The README has the details.

### Before you start

Point it at your `masters.db`. It does not have to be untouched: on the first
run the manager compares it with a clean copy of the same game build, says which
mods it can already see in there, and offers to take them over. The moment you
pick a database it also keeps a permanent `masters.db.original` copy.

### What's new in Beta V0.9.0

**Search, filter and sort the mod list.** One row of controls above the list: a
search box, a category, tags, and a sort order. Search matches a mod's name,
author, description, category and tags. Sorting only changes what you are looking
at — it never changes the load order, so what you see can never quietly change
what gets applied.

**Give a mod your own categories and tags.** Every mod that ships with the
manager comes with sensible ones already set, and you can change them or add your
own on any mod, including somebody else's. They are written into the mod's
`mod.json`, so they travel with the folder if you copy it to another machine. If
the author releases an update and you drop it in, their file replaces yours and
your tags on that mod go with it.

**Make all the text bigger or smaller.** **View → Text size**, from 70% to 200%.
Everything scales with it — padding, row heights and the headings — not just the
letters, so nothing ends up cramped or clipped. The setting is remembered.

**A new mod: Reward Pickup.** Dropped Kill Coins, SPLithium and Bloodnium come
straight to you instead of scattering across the floor. Two parts you can tick
separately, and an adjustable stagger between the pieces. It also brings a new
kind of mod — one that writes a game **config file** rather than the database or
a package. The game reads several of those at startup but never shipped them, so
nothing the game came with is edited, and switching the mod off deletes the file
again.

**The Coin Locker mod does two more things.** As well as the total size, you can
now set **how many slots each purchase adds** (1 to 1,000, stock is 10) and
**what a purchase costs**. Both are parts, switched off until you tick them.
Slots per purchase is the one that makes a big locker usable: at stock, filling a
10,000-slot locker means buying the expansion a thousand times.

**The Hash Patcher is fast again.** Switching the check off for everything took
about a minute with the window frozen; it now takes under half a second. Typing
in its search box was close to a second per pass and is now instant. Opening the
panel is twice as quick.

**Quieter, more accurate conflict warnings.** The mod list used to warn when two
mods wrote the same **table**, which meant warnings about mods that never
actually met — dozens of mods write `master_text` without touching the same
thing. Now it only tells you when two mods write the same **box**: same row, same
column. Change a weapon's damage in one mod and its durability in another and you
will hear nothing; change the damage in both and you get a red dot naming the
column. With every included mod switched on at once, that took the warnings from
fourteen down to none.

**Fixes**

- **A mod could lose part of its own way back.** If you changed a mod's value and
  saved again, rows first written by the later save were not recorded, and
  unticking the mod afterwards left those rows on the modded values instead of
  putting them back. This affected every mod with an adjustable value. The
  records this release writes are complete, and the ones already on disk are
  repaired.
- Every included mod is now marked as working on game build 1.89, so they no
  longer show a version warning. That is not a guess: for each mod, every row it
  writes was compared between the 1.88 and 1.89 clean databases and found
  identical.
- The Configuration tab showed a literal `&nbsp;&nbsp;` in some mods' settings
  instead of a gap. Setting names, limits and help text are now always shown as
  plain text, so nothing a mod author writes can affect the layout.
- Renaming a mod to the same name with different capitals no longer refuses with
  "a mod folder called that already exists".
- Two places where a chosen text size was being thrown away, so the diff panel
  and the mod list ignored it.

1,210 tests pass on Python 3.13 and 3.14.
