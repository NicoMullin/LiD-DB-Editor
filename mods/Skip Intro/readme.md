# Skip Intro

Straight to the title screen. The game's start-up logos are skipped: the
logo videos (Grasshopper, GungHo, Unreal and the two Death Drive videos) and
the GungHo, Supertrick and Unreal logo screens that follow.

The title screen and the story films are left alone.

## Parts

| Part   | What it does                                                        | Ships |
|--------|---------------------------------------------------------------------|-------|
| videos | Replaces the five logo videos with empty files, so nothing plays    | on    |
| logos  | Each logo screen fades in, shows and fades out in one frame         | on    |

## What it changes

**videos:** five files in `BrgGame/Movies`, replaced by empty ones:
`MV_GhM.bk2`, `MV_GOE.bk2`, `MV_UE.bk2`, `MV_FX_DDLogo.bk2` and
`MV_FX_DD_XXX_Logo.bk2`. The game's file check doesn't cover videos.

**logos:** nine bytes in `BrgGame.upk`, all in the logo screen's
`BrgUIMenu_OpeningLogo.Tick`. Each logo fades in over the fade time, stays
for the display time, then fades out over the fade time again. The mod points
those nine timings at the frame's own length instead, so each one passes in a
single frame. Nothing in the package changes size. This part needs the game's
file check off for `BrgGame.upk`; the manager offers to do that when you
save.

## Reverting

Automatic. The manager keeps the stock videos and `BrgGame.upk` the first time
it replaces them; unticking the mod and saving puts them back.

If you renamed the videos yourself to test this, rename them back before
saving, so the manager has the real files to keep.

## Built for game 5.0.4.4.0 - 1.91
