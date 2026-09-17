# Tower Static Radio

**By S3er0i9ng** — included with the manager with their permission.
Original release: <https://letitdiemods.pages.dev/> (LET IT DIE Mod Manager v1.3.8)

Adds a new station, **Tower Static**, on radio channel 501, playing:

- Tower of Barbs - One More Floor
- Mushroom Club - Premium Survival
- Waiting Room - Death Drive
- Momoko - Stir the Odds

The mod has two parts:

- **Music** — four new `.upk` packages copied into `BrgGame/CookedPCConsole`.
  They are new files, not replacements, so the game executable is not touched.
- **Database** — the station and its four tracks added to `master_radio_channel`
  and `master_radio_music`. Nothing already in either table is changed.

Tick it and click **Save Mod List** with the game closed. Unticking it takes the
station out of the database and the four packages out of the game.
