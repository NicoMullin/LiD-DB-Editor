# Colored PlayStation Buttons v1.4

**By S3er0i9ng** — included with the manager with their permission.
Original release: <https://letitdiemods.pages.dev/>

Replaces the on-screen button prompts with colored PlayStation ones.

The game keeps a hash for the file this mod replaces
(`UI_ButtonGuide_STM_SF.upk`), and refuses a replacement at startup with an
error box naming it. So this mod needs the game's file check switched off for
that one package first — the manager checks, and will not apply the mod until
it is, rather than letting you meet that error.

The manager does not change `BrgGame-Steam.exe` itself. Switching the check off
is a separate tool.

Once it is off, tick the mod and click **Save Mod List**, with the game closed.
Unticking it puts the game's own file back.

Do not also run the mod's own `Run.cmd` — the manager already does its job, and
the two would undo each other.
