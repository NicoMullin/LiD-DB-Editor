# Quest Limit

The quest board lets you have 10 quests accepted at a time. This raises (or
lowers) that number.

## Your value

**Quests at once** - default **20**, from **1** to **99**. The game's own is 10.

Select this mod and open the **Configuration** tab on the right to change it,
or right-click it and pick **Change values...**. Press Enter or click away,
then **Save Mod List**.

## What it changes

One number in `BrgGame.upk`: the quest board's `mMaxQuestsAccepted`
(`BrgUIMenu_GameCenterNaomi`), 10 as the game ships it. It is found by its
property header, the property's name and type as this game build numbers them.
On another build the mod stops with a message rather than writing anywhere
else.

The game checks its packages at startup, so this mod needs the game's file
check off for `BrgGame.upk`. The manager offers to do that when you save.

## Reverting

Automatic. The manager keeps the stock `BrgGame.upk` the first time it changes
it; unticking the mod and saving puts it back.

## Built for game 5.0.4.4.0 - 1.91
