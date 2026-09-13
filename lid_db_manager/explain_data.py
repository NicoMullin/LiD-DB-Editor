"""What the database's tables and columns actually mean, in plain English.

This is the hand-written half of the explanation tab. The other half needs no
writing at all: names and descriptions come out of the game's own text, so a
skill or a quest can be named without anyone describing it here.

**Adding a table is meant to be easy.** Copy an entry, change the names. A
table nobody has described yet still works - it is shown with its real column
names and a note that there is no description yet - so this file can grow one
table at a time.

Anything you are not sure of, write "(not confirmed)" into the wording. It is
better to say so than to state a guess as fact.

You can also add or override entries without touching this file: put a
``table-notes.json`` in the manager's home folder, in the same shape as
``TABLES`` below. It is merged over these at startup, so a player or a mod
author can describe a table the program has never heard of.
"""

from __future__ import annotations

# How a row is named in the explanation.
#
#   {"kind": "level"}                    -> "level 7"
#   {"kind": "text", "column": "name"}   -> follow that column's
#                                           'SECTION.TXT_id' pointer into the
#                                           game's own text
#   {"kind": "key"}                      -> the row's key, joined with " / "
#   {"kind": "columns", "columns": [...]} -> named from its own columns, for
#                                           tables keyed on a plain row number.
#                                           "skip" drops filler values.
#   {"kind": "fighter_tier"}             -> "All-rounder, grade 2"
#   {"kind": "shop_product"}             -> a friendly product name, see NAMES
#
# "text" and "via" also take a "params" table, for names written with a gap in
# them ("Element Limit #0"), where the missing part is kept in another table.
#
# A column entry is (meaning, unit). The meaning is dropped into a sentence
# after the change: "<meaning> set to 1 KC, for levels 1-99", so write it as a
# noun phrase - "the price to buy it", not "sets the price".

TABLES: dict[str, dict] = {
    # -- the two Waiting Room stores ------------------------------------
    # Both are upgraded with SPLithium: "You can use SPLithium to enhance your
    # Kill Bank and SPLithium Tank" (the game's own Defense tutorial page).
    "master_safe_level": {
        "word": "level",
        "title": "Buffalo Bank (Kill Bank) upgrades",
        "about": "The store in your Waiting Room that holds your Kill Coins, "
                 "and what raiders attack. Levels 1 to 99, holding up to "
                 "2,560,000 at the top.",
        "row": {"kind": "level"},
        "columns": {
            "limit": ("the most Kill Coins it can hold", "KC"),
            "rob_limit": ("the most Kill Coins a raider can take from it (not confirmed)", "KC"),
            "price": ("the cost to upgrade to that level", "SPLithium"),
            "hp": ("its HP when someone raids you", ""),
            "def": ("its defense when someone raids you", ""),
            "waiting_minute": ("how long the upgrade takes", "min"),
        },
    },
    "master_spirit_tank_level": {
        "word": "level",
        "title": "SPLithium Tank upgrades",
        "about": "The Waiting Room tank that holds your SPLithium. Levels 1 to "
                 "99, holding up to 2,560,000 at the top.",
        "row": {"kind": "level"},
        "columns": {
            "limit": ("the most SPLithium it can hold", "SPLithium"),
            "rob_limit": ("the most SPLithium a raider can take from it (not confirmed)", "SPLithium"),
            "price": ("the cost to upgrade to that level", "SPLithium"),
            "hp": ("its HP when someone raids you", ""),
            "def": ("its defense when someone raids you", ""),
            "waiting_minute": ("how long the upgrade takes", "min"),
        },
    },
    # -- quests and their rewards ---------------------------------------
    "master_quest": {
        "word": "quest",
        "title": "Quests",
        # Quest names are written "Element Limit #0", with the number kept in a
        # separate table - so several quests share a name until it is filled in.
        "row": {"kind": "text", "column": "name",
                "params": {"table": "master_quest_param_text", "key": "qid",
                           "order": "no", "value": "val"}},
        "columns": {
            "no": ("its number in the quest list", ""),
            "cond_flr": ("the floor you have to reach", ""),
            "lvl": ("the level it asks for", ""),
            "prgmax": ("how many times you have to do it", ""),
            "first_rwd": ("the reward for finishing it the first time", ""),
            "rwd": ("the reward for finishing it again", ""),
            "cat": ("which list it appears in", ""),
            "type": ("what kind of objective it is", ""),
            "start_date": ("when it becomes available", ""),
            "end_date": ("when it stops being available", ""),
        },
    },
    "master_reward": {
        "word": "reward",
        "title": "Quest rewards",
        "about": "What a quest hands over. Quests point at these by id.",
        "row": {"kind": "key"},
        "columns": {
            "num": ("how many you get", ""),
            "type": ("what kind of thing it gives", ""),
            "val0": ("what it gives", ""),
            "name": ("its internal label (not shown in game)", ""),
        },
    },
    "master_login_bonus": {
        "word": "login day",
        "title": "Daily login bonuses",
        "about": "What signing in gives you. Most rows are a fixed calendar "
                 "date, and vanilla's calendar runs from 31 October 2019 to 29 "
                 "April 2026 and then stops - past that date there is nothing "
                 "left to hand out unless a mod adds more. The other 22 rows go "
                 "by how many days in a row you have played, up to ten.",
        # Keyed on a row number, so name each row by what earns it instead.
        "row": {"kind": "columns", "columns": ["cond_date", "cond_days", "cond_type"],
                "skip": ["0000-00-00", 0]},
        "columns": {
            "cond_type": ("what earns it - a date, a run of days, or VIP", ""),
            "cond_date": ("the date it is given on", ""),
            "cond_days": ("how many days in a row it takes", "days"),
            "rwdid": ("what it gives", ""),
        },
    },
    "master_mysterybag_content_gen": {
        "word": "prize",
        "title": "What comes out of a Mystery Bag",
        "about": "Every prize a bag can hold, with a weight against the others "
                 "of its rarity - copper, silver, gold, platinum and rainbow.",
        "row": {"kind": "key"},
        "columns": {
            "rarity": ("which grade of bag it comes from", ""),
            "freq": ("how likely it is, against the rest of its grade", ""),
            "rwdid": ("what it gives", ""),
            "display": ("whether it is shown on the bag's prize list", ""),
            "bingo": ("whether it counts towards the bingo card", ""),
        },
    },
    "master_mysterybag_content_gen_odds": {
        "word": "prize",
        "title": "Mystery Bag prizes, offline odds",
        "about": "The same prizes with their own weights for offline play - and "
                 "offline is the only set vanilla carries, so these are the odds "
                 "that actually apply.",
        # Every row is "offline", so saying so in each name adds nothing.
        "row": {"kind": "columns", "columns": ["id"]},
        "columns": {
            "rarity": ("which grade of bag it comes from", ""),
            "freq": ("how likely it is, against the rest of its grade", ""),
            "rwdid": ("what it gives", ""),
            "display": ("whether it is shown on the bag's prize list", ""),
            "bingo": ("whether it counts towards the bingo card", ""),
        },
    },
    # -- crafting, upgrading and equipment --------------------------------
    # Every material id here was checked against master_item: 1,300 of 1,332
    # resolve to real items, so the mate* columns really are materials.
    "master_part_research": {
        "word": "recipe",
        "title": "Crafting and upgrading",
        "about": "What each weapon or armour costs to make and to upgrade.",
        "row": {"kind": "via", "column": "ptid", "table": "master_part", "other_key": "id"},
        "columns": {
            "craft_money": ("the Kill Coins to craft it", "KC"),
            "craft_spirit": ("the SPLithium to craft it", "SPLithium"),
            "craft_rank_point": ("the rank points to craft it", ""),
            "lvup_money": ("the Kill Coins to upgrade it", "KC"),
            "lvup_spirit": ("the SPLithium to upgrade it", "SPLithium"),
            "lvup_rank_point": ("the rank points to upgrade it", ""),
            # The offline game does not make you wait for these any more.
            "init_waiting_minute": ("the wait to craft it", "min"),
            "add_waiting_minute": ("the extra wait per upgrade level", "min"),
            "mate1_id": ("the first material it needs", ""),
            "mate2_id": ("the second material it needs", ""),
            "mate3_id": ("the third material it needs", ""),
            "mate4_id": ("the fourth material it needs", ""),
            "mate5_id": ("the fifth material it needs", ""),
            "craft_mate1_num": ("how much of the first material it takes", ""),
            "craft_mate2_num": ("how much of the second material it takes", ""),
            "is_open": ("whether the recipe is available", ""),
        },
    },
    "master_part": {
        "word": "piece of equipment",
        "title": "Weapons and armour",
        "about": "One row per weapon or armour piece, including each upgrade "
                 "step. It has around ninety columns; only the plain ones are "
                 "described here.",
        "row": {"kind": "text", "column": "name"},
        "columns": {
            "atk": ("its attack power", ""),
            "def": ("its defense", ""),
            "dur": ("its durability", ""),
            "rarity": ("its star rating", ""),
            "exp": ("the EXP it gives", ""),
            "nextptid": ("what it upgrades into", ""),
            "lvllmt": ("the level cap it needs", ""),
            # Only guns and the fuel-burning weapons carry these: 160 rows have
            # a magazine, 117 of those also have a reserve. A weapon with a
            # magazine but no reserve - a rocket launcher, a flame wand - holds
            # everything it will ever fire in the magazine.
            "capacity": ("how many shots it holds before reloading", ""),
            "spare": ("the spare ammo carried for it", ""),
        },
    },
    "master_equip_rank_point": {
        "word": "rank",
        "title": "Equipment rank requirements",
        "about": "Ranks 1 to 55. The points climb the whole way, 0 up to 26,200.",
        "row": {"kind": "number", "word": "rank"},
        "columns": {
            "weapon_point": ("the points needed to reach that weapon rank", "pts"),
            "armor_point": ("the points needed to reach that armour rank", "pts"),
        },
    },
    "master_bodylvl_exp": {
        "word": "level",
        "title": "Fighter level EXP",
        "about": "How much EXP each level of a fighter takes, per grade.",
        "row": {"kind": "labelled", "words": ["grade", "level"]},
        "columns": {
            "exp": ("the EXP needed for that level", "EXP"),
            "bloodnium": ("the Bloodnium needed for that level", "Bloodnium"),
        },
    },
    "master_sklmv": {
        "word": "rage move",
        "title": "Rage moves",
        "row": {"kind": "text", "column": "name"},
        "columns": {
            "gauge_cost": ("how much rage gauge it uses", ""),
            "atk_rate": ("its attack multiplier", ""),
        },
    },
    # -- items, mushrooms and decals --------------------------------------
    "master_item": {
        "word": "item",
        "title": "Items",
        "about": "Everything that is not a weapon or armour piece - materials, "
                 "blueprints, mushrooms, tools and reward-box contents. The "
                 "1,899 blueprints have no names of their own, so each is shown "
                 "as the thing it makes.",
        "row": {"kind": "item", "column": "name"},
        "columns": {
            "buy_money": ("the Kill Coin price", "KC"),
            "buy_spirit": ("the SPLithium price", "SPLithium"),
            "buy_medal": ("the Death Metal price", "Death Metals"),
            "buy_bloodnium": ("the Bloodnium price", "Bloodnium"),
            "buy_recycle_point": ("the recycle-point price", "pts"),
            "sell_money": ("what you get for selling it", "KC"),
            "sell_recycle_point": ("the recycle points you get for it", "pts"),
            "rarity": ("its star rating", ""),
            "is_usable": ("whether you can use it", ""),
            "is_hub_sale": ("whether it is sold in the Waiting Room", ""),
            "itemtype": ("what kind of item it is", ""),
            "grp": ("which drop group it belongs to", ""),
            "buy_rank_money": ("the rank points buying it gives", "pts"),
            "buy_rank_spirit": ("the rank points buying it with SPLithium gives", "pts"),
            "sell_rank_rate": ("the rank points selling it gives", "pts"),
            "sort_no": ("where it sits in the list", ""),
        },
    },
    "master_mushroom": {
        "word": "mushroom",
        "title": "Mushrooms",
        "row": {"kind": "key"},
        # Each mushroom has two forms: c_ is raw ("cut"), r_ is roasted.
        "columns": {
            "price_b": ("what it costs to buy", "KC"),
            "price_s_money": ("what you get for selling it", "KC"),
            "price_s_medal": ("what you get in Death Metals", "Death Metals"),
            "price_s_recycle": ("what you get in recycle points", ""),
            "price_s_bloodnium": ("what you get in Bloodnium", "Bloodnium"),
            "recvhung": ("how much hunger it restores", ""),
            "enable_roast": ("whether it can be roasted", ""),
            "hunter_rate": ("how often a hunter brings one back", ""),
            "c_name": ("its name raw", ""),
            "c_pefcid": ("its good effect raw", ""),
            "c_nefcid": ("its bad effect raw", ""),
            "c_nefcrate": ("how likely the bad effect is raw", ""),
            "c_exp": ("the EXP for eating it raw", "EXP"),
            "c_recvhp": ("the HP it restores raw", ""),
            "r_name": ("its name roasted", ""),
            "r_pefcid": ("its good effect roasted", ""),
            "r_nefcid": ("its bad effect roasted", ""),
            "r_nefcrate": ("how likely the bad effect is roasted", ""),
            "r_exp": ("the EXP for eating it roasted", "EXP"),
            "r_recvhp": ("the HP it restores roasted", ""),
        },
    },
    # Checked across all 208 rows: the odds are set purely by the decal's star
    # rating - 1 star 74, 2 star 72, 3 star 40, 4 star 4, 5 star 2. The same
    # table appears in the Crossover pack's own installer.
    "master_skillgacha_odds": {
        "word": "decal",
        "title": "Decal draw pool",
        "about": "Which decals the Mushroom Club draw can give you, and how "
                 "often. The number is a weight, not a percentage - bigger "
                 "means more often, and vanilla sets it purely by star rating.",
        "row": {"kind": "via", "column": "sklid", "table": "master_skill", "other_key": "id"},
        "columns": {
            "odds": ("how often it comes out of the draw", ""),
            "display_priority": ("where it sits in the list", ""),
        },
    },
    # -- the Death Metro ------------------------------------------------
    # "When you successfully defend against attacks from other Fighters you
    # will obtain Kill Coins and SPLithium in accordance with TDM Rank" - the
    # game's own Defense tutorial page. The point ranges tile exactly.
    "master_tdm_rank": {
        "word": "rank",
        "title": "Death Metro ranks",
        "about": "The raiding ranks, from Bronze III upwards, and what each "
                 "one pays.",
        "row": {"kind": "text", "column": "name"},
        "columns": {
            "point_min": ("the points where the rank starts", "pts"),
            "point_max": ("the points where the rank ends", "pts"),
            "win_bns_money": ("the Kill Coins for winning a raid", "KC"),
            "win_bns_spirit": ("the SPLithium for winning a raid", "SPLithium"),
            "def_bns_money": ("the Kill Coins for a successful defence", "KC"),
            "def_bns_spirit": ("the SPLithium for a successful defence", "SPLithium"),
            "def_bns_money_limit": ("the most Kill Coins defending can pay", "KC"),
            "def_bns_spirit_limit": ("the most SPLithium defending can pay", "SPLithium"),
            "weekly_bns_money": ("the weekly Kill Coin bonus", "KC"),
            "weekly_bns_spirit": ("the weekly SPLithium bonus", "SPLithium"),
            "idx": ("its place in the ladder", ""),
            "group": ("which band it belongs to - Bronze, Silver and so on", ""),
            "win_bns_bag": ("the Mystery Bag odds for winning, as a list", ""),
            "def_bns_bag": ("the Mystery Bag odds for defending, as a list", ""),
            "weekly_bns_bags": ("the Mystery Bags paid weekly, as a list", ""),
            "dummy_group": ("which group of raid targets it faces", ""),
            "bp_order": ("its order in the battle pass", ""),
        },
    },
    "master_expert_lvl_reward": {
        "word": "master level",
        "title": "Weapon mastery rewards",
        "about": "What each master level of a weapon type gives you.",
        "row": {"kind": "text", "column": "name"},
        "columns": {"abp": ("the ABP needed to reach it", "ABP")},
    },
    # -- how a piece of equipment is put together -------------------------
    # These are what the Crossover pack writes to when it clones an item, so
    # they are worth describing even though few mods touch them directly.
    "master_asset": {
        "word": "model",
        "title": "Models",
        "about": "Each row points at a model inside the game's package files - "
                 "the link between the database and the .upk artwork.",
        "row": {"kind": "key"},
        "columns": {
            "mesh": ("the model it uses", ""),
            "pa": ("its physics", ""),
            "skt": ("where it attaches to the body", ""),
            "type": ("what kind of model it is", ""),
        },
    },
    "master_part_asset": {
        "word": "link",
        "title": "Which model a piece of equipment uses",
        "row": {"kind": "via", "column": "id", "table": "master_part", "other_key": "id"},
        "columns": {"asset": ("the model it uses", "")},
    },
    "master_part_defattr": {
        "word": "entry",
        "title": "Equipment defense by damage type",
        "about": "How well a piece of armour holds up against each of the six "
                 "damage types - slash, hit, shoot, fire, electric and poison.",
        "row": {"kind": "via", "column": "id", "table": "master_part",
                "other_key": "id", "with": ["attr"]},
        "columns": {"value": ("how much it defends against that damage type", "")},
    },
    "master_part_param_offset": {
        "word": "bonus",
        "title": "Stat bonuses from equipment",
        "about": "What wearing a piece adds to HP, Strength, Dexterity, "
                 "Stamina or Vitality.",
        "row": {"kind": "via", "column": "id", "table": "master_part",
                "other_key": "id", "with": ["type"]},
        "columns": {"value": ("how much it adds", "")},
    },
    "master_part_equipment": {
        "word": "piece",
        "title": "How equipment is drawn on a fighter",
        "about": "Cosmetic only - which bits of the body a piece covers or "
                 "recolours, per male and female model.",
        "row": {"kind": "via", "column": "id", "table": "master_part", "other_key": "id"},
        "columns": {
            "eyem": ("whether it draws the male eyes", ""),
            "eyef": ("whether it draws the female eyes", ""),
            "bra": ("whether it draws underwear", ""),
            "pantsm": ("whether it draws male trousers", ""),
            "pantsf": ("whether it draws female trousers", ""),
        },
    },
    "master_skillgacha": {
        "word": "draw",
        "title": "The Mushroom Club draw",
        "about": "The draw itself. There is only one row - the offline pool - "
                 "and the decals in it live in the draw-pool table.",
        "row": {"kind": "key"},
        "columns": {
            "odds_id": ("which set of odds it uses", ""),
            "product_id": ("what it costs to draw", ""),
        },
    },
    # -- Screamers, the NPCs you fight ------------------------------------
    # "zmb"/"zombie" in this database means the Screamers.
    "master_zombie_param": {
        "word": "level",
        "title": "Screamer stats by level",
        "about": "The Screamers you fight in the Tower, one row per level, 1 to 300.",
        "row": {"kind": "level"},
        "columns": {
            "mnymin": ("the least Kill Coins it drops", "KC"),
            "mnymax": ("the most Kill Coins it drops", "KC"),
            "mstrlvl": ("its master level, which climbs from 1 to 8", ""),
            # These four are identical on all 300 rows in vanilla, so changing
            # one level alone will not do much.
            "clever": ("how clever it is (the same on every level in vanilla)", ""),
            "atkprob": ("how often it attacks (the same on every level in vanilla)", ""),
            "grdprob": ("how often it guards (the same on every level in vanilla)", ""),
            "avdprob": ("how often it dodges (the same on every level in vanilla)", ""),
        },
    },
    # Every row adds up to exactly 100, so these are shares of its points.
    "master_zombie_phlvl_alloc": {
        "word": "build",
        "title": "How a Screamer's points are spread",
        "about": "Each build shares 100 points between the stats.",
        "row": {"kind": "key"},
        "columns": {
            "hp": ("the share that goes to HP", "%"),
            "str": ("the share that goes to Strength", "%"),
            "dex": ("the share that goes to Dexterity", "%"),
            "vit": ("the share that goes to Vitality", "%"),
            "stm": ("the share that goes to Stamina", "%"),
            "luk": ("the share that goes to Luck", "%"),
        },
    },
    "master_zombie_rwdtype": {
        "word": "drop type",
        "title": "What a Screamer can drop",
        "about": "Kill Coins, a mushroom, or a particular piece of equipment.",
        "row": {"kind": "key"},
        "columns": {"name": ("its internal label (Japanese, not shown in game)", "")},
    },
    "master_stage_zombie": {
        "word": "spawn",
        "title": "Where Screamers appear",
        "about": "One row per spawn point on a stage.",
        "row": {"kind": "key"},
        "columns": {
            "freq": ("how likely one is to appear there", ""),
            "eqtp": ("what it is carrying", ""),
            "rwdtp": ("what it drops", ""),
            "rwdmsr": ("the mushroom it drops", ""),
            "fixtype": ("the fighter type it is forced to", ""),
            "fixgrade": ("the grade it is forced to", ""),
            "fixlvl": ("the level it is forced to", ""),
            "gender": ("the gender it is forced to", ""),
        },
    },
    "master_fort_tutorial_zombie": {
        "word": "defender",
        "title": "Tutorial raid defenders",
        "row": {"kind": "key"},
        "columns": {
            "wave": ("which wave it comes in", ""),
            "fixgrade": ("its grade", ""),
            "fixlvl": ("its level", ""),
        },
    },
    # -- the player's own progress ----------------------------------------
    "master_rank_point": {
        "word": "rank",
        "title": "Player Rank",
        "about": "The account rank shown on your profile and in the Death "
                 "Metro. 130 ranks, and the points are cumulative.",
        "row": {"kind": "number", "word": "rank"},
        "columns": {"rank_point": ("the total points needed to reach that rank", "pts")},
    },
    "master_bodylvl_status_value": {
        "word": "step",
        "title": "Fighter stats by level",
        "about": "What a fighter's stats are at each level, per type and grade.",
        "row": {"kind": "labelled", "words": ["level", "fighter type", "grade"]},
        "columns": {
            "hp": ("its HP", ""),
            "str": ("its Strength", ""),
            "dex": ("its Dexterity", ""),
            "vit": ("its Vitality", ""),
            "stm": ("its Stamina", ""),
            "stmrecov": ("how fast its stamina comes back", ""),
            "luk": ("its Luck", ""),
            "skill": ("how many decal slots it has", "slots"),
            "bag": ("how many Death Bag slots it has", "slots"),
            "rage": ("how many rage gauge bars it has", "bars"),
        },
    },
    "master_bodylvl_limit_break_item": {
        "word": "step",
        "title": "Fighter uncapping costs",
        "about": "What a limit break at Mingo Head costs - normally Death 'Roids.",
        "row": {"kind": "labelled", "words": ["level", "fighter type", "grade", "limit break"]},
        "columns": {
            "item1id": ("the item it needs", ""),
            "item1_count": ("how many of that item", ""),
            "item2id": ("the second item it needs", ""),
            "item2_count": ("how many of that second item", ""),
            "limit_break_type": ("which part of the fighter it uncaps", ""),
        },
    },
    "master_area_connect_escalator": {
        "word": "connection",
        "title": "Escalators between areas",
        "about": "Which rooms connect to which, and what unlocks each path.",
        "row": {"kind": "key"},
        "columns": {
            "key": ("the mini-boss it wants beaten first", ""),
            "gate": ("the lever or button it wants activated first", ""),
            "toflr": ("the floor it leads to", ""),
            "toarea": ("the area it leads to", ""),
        },
    },
    # -- left over from the online days -----------------------------------
    # The game executable carries the name of every table it reads - 209 of the
    # 221 in the file. These ten are never named in it, and all of them are
    # things the offline game has no use for: online quests, store purchases,
    # seasonal rewards. Editing them is very unlikely to do anything, so the
    # tab says so rather than describing them in detail.
    "master_quest_online": {
        "word": "quest", "title": "Online quests (unused)", "unused": True,
        "about": "The old online quest list. The offline game uses the ordinary "
                 "quest table instead.",
        "row": {"kind": "text", "column": "name"},
    },
    "master_tdm_season_reward": {
        "word": "reward", "title": "Death Metro season rewards (unused)", "unused": True,
        "row": {"kind": "key"},
    },
    "master_steam_product": {
        "word": "product", "title": "Steam store products (unused)", "unused": True,
        "row": {"kind": "key"},
    },
    "master_ps4_product": {
        "word": "product", "title": "PlayStation store products (unused)", "unused": True,
        "row": {"kind": "key"},
    },
    "master_shop_product_type": {
        "word": "type", "title": "Shop product types (unused)", "unused": True,
        "row": {"kind": "key"},
    },
    "master_trbox_rwdtype": {
        "word": "type", "title": "Treasure box reward types (unused)", "unused": True,
        "row": {"kind": "key"},
    },
    "master_credit": {
        "word": "line", "title": "Credits roll (unused)", "unused": True,
        "row": {"kind": "key"},
    },
    "master_curve": {
        "word": "curve", "title": "Curves (unused)", "unused": True, "row": {"kind": "key"},
    },
    "master_debug_flag_group": {
        "word": "flag", "title": "Developer debug flags (unused)", "unused": True,
        "row": {"kind": "key"},
    },
    "master_drcat": {
        "word": "category", "title": "Drop categories (unused)", "unused": True,
        "row": {"kind": "key"},
    },

    # -- named, but their columns are not described yet -------------------
    # The heading alone is worth having; the columns still fall back honestly.
    # Around 130 columns, most of them spawn tables. Only the few whose
    # behaviour the data backs up are described; "zmb" here means Screamers.
    "master_floor": {
        "word": "floor",
        "title": "Floors of the Tower",
        "row": {"kind": "text", "column": "name"},
        "columns": {
            "no": ("its floor number", ""),
            "stgid": ("which part of the Tower it belongs to", ""),
            # Counts, all small: Screamers 0-40, mushrooms 0-85, beasts 0-20.
            # "bst" is confirmed - every bstgen value is a key in
            # master_beast_gen - and "msr" is the game's word for mushrooms.
            "zmbmin": ("the fewest Screamers on the floor", ""),
            "zmbmax": ("the most Screamers on the floor", ""),
            "msrmin": ("the fewest mushrooms growing there", ""),
            "msrmax": ("the most mushrooms growing there", ""),
            "bstmin": ("the fewest beasts there", ""),
            "bstmax": ("the most beasts there", ""),
            "bstgen": ("which set of beasts can appear", ""),
            "itemmin": ("the fewest items lying around", ""),
            "itemmax": ("the most items lying around", ""),
            "mbsmin": ("the fewest mini-bosses (not confirmed)", ""),
            "mbsmax": ("the most mini-bosses (not confirmed)", ""),
            "vmmax": ("how many vending machines (not confirmed)", ""),
            "shpnum": ("whether a shop appears - it is a yes/no", ""),
            "stpnum": ("whether the elevator stops here (not confirmed)", ""),
            # These run from -99 to +2303 in vanilla, so they adjust rather
            # than set - a negative number makes Screamers weaker.
            "zmbatkup": ("how much their attack is adjusted", "%"),
            "zmbdefup": ("how much their defense is adjusted", "%"),
            # Left at 0 on all 1,001 floors, so vanilla never uses them.
            "zmblvlmin": ("unused - 0 on every floor in vanilla", ""),
            "zmblvlmax": ("unused - 0 on every floor in vanilla", ""),
            "zmbmnymin": ("unused - 0 on every floor in vanilla", ""),
            # 0 on every floor here, but set to 1 on 194 floors of the
            # master_tmpfloor_stage copy - so it does mean something.
            "clnum": ("0 on every floor here, though the tmpfloor copy sets it", ""),
            "sbnum": ("unused - 0 on every floor in vanilla", ""),
            "soupshpgen": ("unused - 0 on every floor in vanilla", ""),
        },
    },
    "master_beast": {
        "word": "creature",
        "title": "Beasts",
        "about": "The 24 creatures you can catch and cook.",
        "row": {"kind": "text", "column": "name"},
        "columns": {
            "idx": ("its place in the list", ""),
            "bname": ("its name once grilled", ""),
            "rwdmsrid": ("the mushroom you get for catching it", ""),
            "hunter_rate": ("how often a hunter brings one back", ""),
        },
    },
    "master_hubcustomize": {"word": "decoration", "title": "Waiting Room decorations",
                            "row": {"kind": "text", "column": "name"}},
    "master_ptarm_type": {"word": "category", "title": "Weapon and armour categories",
                          "row": {"kind": "text", "column": "name"}},
    "master_quest_category": {"word": "category", "title": "Quest categories",
                              "row": {"kind": "text", "column": "name"}},

    # -- what a quest asks for, and what it says it asks for ---------------
    # A quest keeps its rule and its wording in separate tables. The name and
    # description are written with gaps in them - "Equip a #0 element weapon,
    # and defeat #1 enemies" - filled from the two _text tables below, while
    # the rule the game actually tests lives in _str and _int. Change one
    # without the other and the quest describes something it no longer asks.
    "master_quest_param_str": {
        "word": "condition",
        "title": "What a quest checks for",
        "about": "The real objective, in the game's own codes: which element, "
                 "which enemy, or a floor to start from and a floor to reach. "
                 "This is the part the game tests. What the player reads is "
                 "written separately, in the quest's description.",
        "row": {"kind": "via", "column": "qid", "table": "master_quest",
                "other_key": "qid", "with": ["no"],
                "params": {"table": "master_quest_param_text", "key": "qid",
                           "order": "no", "value": "val"}},
        "columns": {"val": ("what it checks for", "")},
    },
    "master_quest_param_int": {
        "word": "limit",
        "title": "The number a quest enforces",
        "about": "The 53 quests built around a number keep it here - three "
                 "Skill Decals, one item in the Death Bag, grade 6 equipment. "
                 "The description spelling that number out is written by hand, "
                 "so raising the limit here does not reword the quest.",
        "row": {"kind": "via", "column": "qid", "table": "master_quest",
                "other_key": "qid",
                "params": {"table": "master_quest_param_text", "key": "qid",
                           "order": "no", "value": "val"}},
        "columns": {"val": ("the limit it allows", "")},
    },
    "master_quest_param_text": {
        "word": "filled-in word",
        "title": "Words filled into quest names",
        "about": "Quest names are written with a gap in them - \"Element Limit "
                 "#0\" - and this fills it, almost always with the number that "
                 "tells one quest in a series from the next.",
        "row": {"kind": "via", "column": "qid", "table": "master_quest",
                "other_key": "qid", "with": ["no"]},
        "columns": {"val": ("the word or number it fills in", "")},
    },
    "master_quest_param_desc_text": {
        "word": "filled-in word",
        "title": "Words filled into quest descriptions",
        "about": "The same idea for descriptions, filled in order with area "
                 "names, elements and target counts. Editing these changes only "
                 "what the quest says - the rule it enforces is kept in "
                 "master_quest_param_str.",
        "row": {"kind": "via", "column": "qid", "table": "master_quest",
                "other_key": "qid", "with": ["no"]},
        "columns": {"val": ("the word or number it fills in", "")},
    },
    "master_quest_type": {
        "word": "kind of quest",
        "title": "Kinds of quest objective",
        "about": "The 29 shapes an objective can take - clear the beasts, kill "
                 "a number of enemies, reach a floor - and how each one reports "
                 "its progress.",
        "row": {"kind": "key"},
        "columns": {
            "hubclrprg": ("whether returning to the Waiting Room wipes its progress", ""),
            "hubclrdat": ("whether returning to the Waiting Room wipes its record", ""),
            "progress_chk": ("how its progress is counted", ""),
        },
    },
    "master_quest_reward_part": {
        "word": "reward",
        "title": "Equipment given as a quest reward",
        "about": "Only two quests hand over a piece of equipment directly, and "
                 "this sets what state it arrives in. Everything else rewards "
                 "through the normal reward tables.",
        "row": {"kind": "via", "column": "qid", "table": "master_quest",
                "other_key": "qid", "with": ["ptid"]},
        "columns": {
            "lvlmin": ("the lowest level it can come at", ""),
            "lvlmax": ("the highest level it can come at", ""),
            "durmin": ("the lowest durability it can come with", ""),
            "durmax": ("the highest durability it can come with", ""),
        },
    },
    # Empty in vanilla. That is not the same as dead - see "empty" vs "unused".
    "master_quest_condition_int": {"word": "condition", "title": "Quest conditions (numbers)",
                                   "row": {"kind": "key"}, "empty": True},
    "master_quest_condition_float": {"word": "condition", "title": "Quest conditions (decimals)",
                                     "row": {"kind": "key"}, "empty": True},
    "master_quest_condition_str": {"word": "condition", "title": "Quest conditions (codes)",
                                   "row": {"kind": "key"}, "empty": True},
    "master_quest_param_float": {"word": "value", "title": "Quest values (decimals)",
                                 "row": {"kind": "key"}, "empty": True},
    "master_skill_category": {"word": "category", "title": "Decal categories",
                              "row": {"kind": "text", "column": "name"}},

    # -- what is placed where, room by room -------------------------------
    # A whole family sharing one shape: (stage, room, point) -> how likely,
    # plus what appears there. "unit" is a room inside a stage and "pntid" is
    # a spot inside that room.
    "master_stage": {
        "word": "area",
        "title": "Areas of the Tower",
        "about": "The seven areas: Amusement, Arcade, Heaven, Hazama, Metro, "
                 "Rooftop, and the last boss area.",
        "row": {"kind": "key"},
        "columns": {
            "prefix": ("its English name in the game files", ""),
            "name": ("its Japanese name", ""),
            "drcat": ("which drop category it uses", ""),
        },
    },
    "master_stage_unit": {
        "word": "room",
        "title": "Rooms in each area",
        "row": {"kind": "key"},
    },
    "master_stage_zako": {
        "word": "spawn",
        "title": "Where small enemies appear",
        "about": "The lesser enemies - bone, hovering, turret and the rest - "
                 "one row per spot they can appear in.",
        "row": {"kind": "key"},
        "columns": {
            "type": ("which kind appears", ""),
            "freq": ("how likely it is to be there", ""),
            "path": ("the route it walks", ""),
        },
    },
    # -- the players you raid ---------------------------------------------
    # Offline there is nobody to raid, so the game keeps 1,687 stand-in
    # players. Each row is a base to attack: how far along its owner is
    # supposed to be, what is in their vaults to steal, and where the
    # defending fighter, its gear, its decals and even its name come from.
    "master_dummy": {
        "word": "raid target",
        "title": "The players you raid in Tokyo Death Metro",
        "about": "The stand-in players the game offers you to attack. The "
                 "safebox and tank columns are what there is to steal; the "
                 "eight *_freq columns are the odds of each fighter type "
                 "defending, and normally add up to 100.",
        "row": {"kind": "key"},
        "columns": {
            "rank_min": ("the lowest Player Rank it is offered at", ""),
            "rank_max": ("the highest Player Rank it is offered at", ""),
            "safebox_lvl": ("their Buffalo Bank level", ""),
            "safebox_money_min": ("the least Kill Coins in their bank", "KC"),
            "safebox_money_max": ("the most Kill Coins in their bank", "KC"),
            "spirittank_lvl": ("their SPLithium Tank level", ""),
            "spirittank_spirit_min": ("the least SPLithium in their tank", "SPLithium"),
            "spirittank_spirit_max": ("the most SPLithium in their tank", "SPLithium"),
            "lvl_min": ("the lowest level their fighter can be", ""),
            "lvl_max": ("the highest level their fighter can be", ""),
            "grade": ("their fighter's grade", ""),
            "limit_break": ("how far their fighter is uncapped", ""),
            "expert_lvl": ("their expert level", ""),
            "fort_setting_num": ("how many defences their base has", ""),
            "fort_dest_money_min": ("the least Kill Coins for wrecking the base", "KC"),
            "fort_dest_spirit_min": ("the least SPLithium for wrecking the base", "SPLithium"),
            "zmb_money_min": ("the least Kill Coins their Screamers carry", "KC"),
            "zmb_money_max": ("the most Kill Coins their Screamers carry", "KC"),
            "name_gen": ("which pool their name is drawn from", ""),
            "eq_gen": ("which set their equipment is drawn from", ""),
            "eq_skl_gen": ("which set their decals are drawn from", ""),
            "whistle_id": ("the alarm their base sounds", ""),
            "bal_freq": ("the chance the defender is an All-rounder", ""),
            "bre_freq": ("the chance the defender is a Striker", ""),
            "def_freq": ("the chance the defender is a Defender", ""),
            "tec_freq": ("the chance the defender is an Attacker", ""),
            "sht_freq": ("the chance the defender is a Shooter", ""),
            "col_freq": ("the chance the defender is a Collector", ""),
            "ski_freq": ("the chance the defender is a Skill Master", ""),
            "luk_freq": ("the chance the defender is a Lucky Star", ""),
        },
    },
    "master_dummy_group": {
        "word": "member",
        "title": "Groups of raid targets",
        "about": "Which stand-in players belong to each group, so the game can "
                 "offer a themed set rather than picking from all 1,687.",
        "row": {"kind": "columns", "columns": ["id", "dummy_id"]},
        "columns": {"dummy_id": ("the raid target in the group", "")},
    },
    "master_dummy_name": {
        "word": "name",
        "title": "Names given to raid targets",
        "about": "842 names in themed pools, one of which is picked when a "
                 "stand-in player is shown to you.",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely that name is (1 on every row, so all equal)", "")},
    },
    "master_dummy_chara_equip_gen": {
        "word": "outfit",
        "title": "What raid targets wear",
        "about": "Weighted sets of outfits for the defending fighter, pointing "
                 "into the same outfit lists the Tower's fighters use.",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely that outfit is, against the rest of its set", "")},
    },
    "master_dummy_chara_skill_gen": {
        "word": "decal",
        "title": "What decals raid targets carry",
        "about": "Weighted sets of Skill Decals for the defending fighter.",
        "row": {"kind": "via", "column": "sklid", "table": "master_skill",
                "other_key": "id", "with": ["id"]},
        "columns": {
            "freq": ("how likely that decal is, against the rest of its set", ""),
            "order": ("which decal slot it fills", ""),
        },
    },
    # -- how hard each attack animation hits -------------------------------
    "master_atk_scale": {
        "word": "attack",
        "title": "Damage and knockback per attack",
        "about": "One row per attack animation in the game - 514 of them - "
                 "setting how hard it hits, how much stamina it costs and how "
                 "far it knocks the target back. A backstab is scale 10,000 "
                 "against a normal attack's few hundred.",
        "row": {"kind": "key"},
        "columns": {
            "scale": ("how hard it hits", ""),
            "stmscl": ("how much stamina it costs", ""),
            "dmg_delaycf": ("the pause when it lands", ""),
            "grd_delaycf": ("the pause when it is blocked", ""),
            "ctr_delaycf": ("the pause when it is countered", ""),
            "dmg_kbdist": ("how far it knocks the target back", ""),
            "grd_kbdist": ("how far it pushes a blocking target back", ""),
            "strrate": ("how much the attacker's Strength counts", "%"),
            "dexrate": ("how much the attacker's Dexterity counts", "%"),
        },
    },
    "master_atk_scale_atkattr": {
        "word": "attack",
        "title": "Which element each attack deals",
        "about": "How an attack's damage is split across the six elements - "
                 "blunt, piercing, slashing, fire, electric and poison.",
        "row": {"kind": "key"},
        "columns": {"value": ("its share of that element", "")},
    },
    "master_part_atkattr": {
        "word": "piece of equipment",
        "title": "Which element a weapon deals",
        "about": "The same split, per weapon rather than per animation.",
        "row": {"kind": "via", "column": "id", "table": "master_part",
                "other_key": "id", "with": ["attr"]},
        "columns": {"value": ("its share of that element", "")},
    },
    # -- enemy tuning, kept as named settings -----------------------------
    # A dozen tables share one shape: (who, level, setting) -> value. The
    # setting's name is stored in the row rather than being a column, so the
    # meaning lives in the row's name and the only column to describe is the
    # value itself. The names are the developers' own - hp, exp, STR, but also
    # AttackWaitTime03 - so these entries say where a number applies and leave
    # the name to speak for itself rather than inventing a meaning for it.
    "master_mboss_param_int": {
        "word": "setting",
        "title": "Mid-boss stats, level by level",
        "about": "The whole numbers behind the four mid-bosses and the four "
                 "stage bosses - hp, exp, str, the Kill Coins and SPLithium "
                 "they drop, and what they spawn - for each of 32 levels up to "
                 "65. 32 settings in all.",
        "row": {"kind": "labelled", "words": ["", "level", ""]},
        "columns": {"value": ("the value of that setting", "")},
    },
    "master_mboss_param_float": {
        "word": "setting",
        "title": "Mid-boss timings and distances",
        "about": "The same eight bosses again, for the 119 settings that need a "
                 "decimal - how long it waits between attacks, how far away it "
                 "will use a long-range attack, how fast its bullets fade.",
        "row": {"kind": "labelled", "words": ["", "level", ""]},
        "columns": {"value": ("the value of that setting", "")},
    },
    "master_mboss_ai_float": {
        "word": "setting",
        "title": "Mid-boss behaviour patterns",
        "about": "599 named states a boss can be in - searching, missed, hurt, "
                 "attacking - each holding up to 24 numbered values and 22 "
                 "numbered actions. What each slot does is not written down "
                 "anywhere in the database; only which state it belongs to is.",
        "row": {"kind": "columns", "columns": ["type", "ptn", "name"]},
        "columns": {"value": ("the value of that slot", "")},
    },
    "master_fourforcemen_param_int": {
        "word": "setting",
        "title": "The Four Forcemen, level by level",
        "about": "Black Thunder, Pale Wind, Red Napalm and White Steel, across "
                 "eight levels, with 153 settings each - how angry they get and "
                 "for how long, how close they come, how much each weapon type "
                 "hurts them.",
        "row": {"kind": "labelled", "words": ["", "level", ""]},
        "columns": {"value": ("the value of that setting", "")},
    },
    "master_zako_param_int": {
        "word": "setting",
        "title": "Small enemy stats, level by level",
        "about": "The rank and file, levels 1 to 10: HP and Kill Coin ranges, "
                 "EXP, the four stats, and how readily they guard, dodge, grab "
                 "or throw dynamite.",
        "row": {"kind": "labelled", "words": ["", "level", ""]},
        "columns": {"value": ("the value of that setting", "")},
    },
    "master_zako_param_float": {
        "word": "setting",
        "title": "Small enemy timings",
        "about": "The same enemies, for the settings that need a decimal.",
        "row": {"kind": "labelled", "words": ["", "level", ""]},
        "columns": {"value": ("the value of that setting", "")},
    },
    "master_beast_param_int": {
        "word": "setting",
        "title": "Beast stats, level by level",
        "about": "Frogs, scorpions and the rest across five levels: how fast "
                 "they walk and run, the EXP and price they are worth, how hard "
                 "they are to stun, and whether they poison you.",
        "row": {"kind": "labelled", "words": ["", "level", ""]},
        "columns": {"value": ("the value of that setting", "")},
    },
    "master_beast_param_float": {
        "word": "setting",
        "title": "Beast timings",
        "about": "Ten rows only - the handful of beast settings that need a "
                 "decimal.",
        "row": {"kind": "labelled", "words": ["", "level", ""]},
        "columns": {"value": ("the value of that setting", "")},
    },
    "master_jackal_param_int": {
        "word": "setting",
        "title": "Jackal settings",
        "about": "Nine kinds of Jackal, with no level - one set of numbers each. "
                 "Their ids are placeholders (JACKAL_XXX, JACKAL_YYY), so the "
                 "database does not say which Jackal is which.",
        "row": {"kind": "labelled", "words": ["", ""]},
        "columns": {"value": ("the value of that setting", "")},
    },
    "master_param_float": {
        "word": "setting",
        "title": "Hazard settings",
        "about": "Eighteen rows covering the Tower's gimmicks - an electrified "
                 "floor does 20 damage every 3 seconds.",
        "row": {"kind": "labelled", "words": ["", "", ""]},
        "columns": {"value": ("the value of that setting", "")},
    },
    # Empty in vanilla. Whether anything ever filled them is not known.
    "master_fourforcemen_param_float": {"word": "setting", "title": "Four Forcemen decimals",
                                        "row": {"kind": "key"}, "empty": True},
    "master_jackal_param_float": {"word": "setting", "title": "Jackal decimals",
                                  "row": {"kind": "key"}, "empty": True},
    "master_param_int": {"word": "setting", "title": "Hazard whole numbers",
                         "row": {"kind": "key"}, "empty": True},

    "master_ngword": {
        "word": "word",
        "title": "The banned-word list",
        "about": "2,627 words the game refuses in a fighter's name. Offline "
                 "this only gets in the way of naming your own fighter, so "
                 "emptying it is a normal thing for a mod to do.",
        # Counted, never quoted: a sample of this list is slurs.
        "no_examples": True,
        "row": {"kind": "columns", "columns": ["word"]},
        "columns": {"word": ("the word that is refused", ""),
                    "type": ("what kind of text it applies to", "")},
    },
    "master_tgtpnt": {
        "word": "spot",
        "title": "Where things can be placed in a room",
        "about": "The named spots each room offers, one list per kind of thing "
                 "- treasure boxes, mushrooms, beasts, vending machines, "
                 "stamps, gates, small enemies, Screamers, mid-bosses and the "
                 "Four Forcemen. The stage tables say what goes in them.",
        "row": {"kind": "key"},
        "columns": {"stgid": ("which area the room is in", "")},
    },
    "master_area_connect_node": {
        "word": "connection",
        "title": "How areas join onto the elevator",
        "about": "Which elevator stop each area hangs off, and where along it. "
                 "isdef marks the one an area uses by default.",
        # idx is not unique, and five templates share each area, so the
        # template id has to be part of the name or they all read alike.
        "row": {"kind": "columns", "columns": ["id", "flrid", "areaid"]},
        "columns": {
            "elvflrid": ("the elevator stop it joins", ""),
            "isdef": ("whether this is the default way in", ""),
            "ofsx": ("how far along the stop it sits", ""),
            "flagofsxs": ("extra positions that only apply under a game flag", ""),
        },
    },
    "master_area_template_term": {
        "word": "term",
        "title": "Which area layout is used, by date",
        "about": "The Tower was built to rotate its layouts on a schedule: 4,019 "
                 "dated terms running from July 2015 to April 2027, each naming "
                 "one of five templates.",
        "row": {"kind": "columns", "columns": ["termid"]},
        "columns": {
            "expires": ("when that term ends, as a unix timestamp", ""),
            "tmplid": ("which layout template it uses", ""),
        },
    },

    # -- the floor tables, a second time -----------------------------------
    # Checked column by column: master_tmpfloor_stage has no columns of its
    # own, all 118 are master_floor's, and of the 115 non-key columns they
    # share exactly one (clnum) differs anywhere. The 18 columns master_floor
    # has on top are the beast, item, mushroom and gimmick ones - which are
    # exactly what the other four tmpfloor tables hold. So this family is the
    # same data as master_floor, split into five tables instead of merged into
    # one. Which of the two the game actually reads is not recorded anywhere,
    # so a mod changing floors should change both to be safe.
    "master_tmpfloor_stage": {
        "word": "floor",
        "title": "Floors (the split-up copy)",
        "about": "The same 1,000 floors as master_floor, minus the beast, item, "
                 "mushroom and gimmick columns, which live in the four tables "
                 "below. Every shared column matches master_floor except clnum. "
                 "If you edit floors, edit both copies.",
        "row": {"kind": "columns", "columns": ["id", "areaid"]},
        "columns": {
            "no": ("the floor number shown to the player", ""),
            "name": ("its name", ""),
            "zmbexp": ("the EXP its Screamers give", ""),
            "zmbmin": ("the fewest Screamers on it", ""),
            "zmbmax": ("the most Screamers on it", ""),
            "clnum": ("set to 1 on 194 floors here, and 0 everywhere in master_floor", ""),
        },
    },
    "master_tmpfloor_beast": {
        "word": "floor",
        "title": "Beasts per floor (the split-up copy)",
        "about": "How many beasts a floor has and which set they come from. "
                 "master_floor holds the same columns.",
        "row": {"kind": "columns", "columns": ["id", "areaid"]},
        "columns": {
            "bstlvlmin": ("the lowest level they can be", ""),
            "bstlvlmax": ("the highest level they can be", ""),
            "bstmin": ("the fewest on the floor", ""),
            "bstmax": ("the most on the floor", ""),
            "bstgen": ("which set they are drawn from", ""),
        },
    },
    "master_tmpfloor_item": {
        "word": "floor",
        "title": "Items per floor (the split-up copy)",
        "row": {"kind": "columns", "columns": ["id", "areaid"]},
        "columns": {
            "itemmin": ("the fewest items on the floor", ""),
            "itemmax": ("the most items on the floor", ""),
            "itemgenid": ("which set they are drawn from", ""),
        },
    },
    "master_tmpfloor_mushroom": {
        "word": "floor",
        "title": "Mushrooms per floor (the split-up copy)",
        "row": {"kind": "columns", "columns": ["id", "areaid"]},
        "columns": {
            "msrmin": ("the fewest mushrooms on the floor", ""),
            "msrmax": ("the most mushrooms on the floor", ""),
            "msrgen": ("which set they are drawn from", ""),
            "zmbmsrgen": ("which set the Screamers' mushrooms come from", ""),
        },
    },
    "master_tmpfloor_gimic": {
        "word": "floor",
        "title": "Hazard damage per floor (the split-up copy)",
        "row": {"kind": "columns", "columns": ["id", "areaid"]},
        "columns": {"gmcdmgscale": ("how hard the floor's hazards hit", "")},
    },
    # -- the radio ---------------------------------------------------------
    "master_radio_music": {
        "word": "track",
        "title": "The radio's music",
        "about": "144 tracks, with the title and artist the game shows while "
                 "one is playing.",
        "row": {"kind": "columns", "columns": ["title"]},
        "columns": {"title": ("the track's title", ""), "artist": ("who it is by", "")},
    },
    "master_radio_channel": {
        "word": "channel",
        "title": "The radio's channels",
        "about": "149 channels, each naming up to five tracks to play.",
        "row": {"kind": "columns", "columns": ["channel_name"]},
        "columns": {
            "channel_name": ("the channel's name", ""),
            "music_id1": ("its first track", ""),
            "music_id2": ("its second track", ""),
            "music_id3": ("its third track", ""),
            "music_id4": ("its fourth track", ""),
            "music_id5": ("its fifth track", ""),
            "rand_grp": ("which shuffle group it belongs to", ""),
        },
    },
    # -- odds and ends -----------------------------------------------------
    "master_deathbox_content_gen": {
        "word": "prize",
        "title": "What comes out of a Death Box",
        "about": "Prizes by box colour, weighted against the others of that "
                 "colour.",
        "row": {"kind": "key"},
        "columns": {
            "rarity": ("which colour of box it comes from", ""),
            "freq": ("how likely it is, against the rest of its colour", ""),
            "rwdid": ("what it gives", ""),
        },
    },
    "master_zako_gen": {
        "word": "entry",
        "title": "Which small enemies appear where",
        "about": "Weighted sets of small enemies, the same shape as the "
                 "mushroom and beast sets.",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely that enemy is, against the rest of its set", "")},
    },
    "master_model_pattern": {
        "word": "floor",
        "title": "Which Hater models appear on a floor",
        "about": "Each floor picks a group of character models, with a weight. "
                 "Five patterns (A to E) let the same floor look different.",
        "row": {"kind": "columns", "columns": ["pattern", "flrid", "areaid"]},
        "columns": {
            "modelgroup": ("which group of models it uses", ""),
            "modelrate": ("how likely that group is", ""),
        },
    },
    "master_model_group": {
        "word": "model",
        "title": "Groups of Hater models",
        "about": "Which character models belong to each group.",
        "row": {"kind": "columns", "columns": ["group_id", "model_hater_id"]},
        "columns": {"freq": ("how likely that model is within the group", "")},
    },
    "master_hair_display": {
        "word": "head part",
        "title": "Which hair shows under a hat",
        "about": "For each head item, the hair model drawn with it - the "
                 "Japanese circle means yes, it is shown.",
        "row": {"kind": "key"},
        "columns": {"asset": ("the hair model used", ""),
                    "value": ("whether the hair is shown", "")},
    },
    "master_ptarm": {
        "word": "arm item",
        "title": "Healing from arm equipment",
        "about": "What a First Aid Kit and its like restore.",
        "row": {"kind": "via", "column": "id", "table": "master_part", "other_key": "id"},
        "columns": {
            "subtype": ("what kind of item it is", ""),
            "cure0": ("how much of the first thing it cures", ""),
            "cure1": ("how much of the second thing it cures", ""),
            "cure2": ("how much of the third thing it cures", ""),
        },
    },
    "master_mushroom_efc": {
        "word": "effect",
        "title": "What eating a mushroom does",
        "about": "126 effects. 23 of them are named with a gap - \"Attack Up "
                 "#0%\" - which the row's own val0 fills, so the game names its "
                 "own numbers. The desc column is flavour text, never a rule.",
        "row": {"kind": "text", "column": "name"},
        "describes": {"column": "name", "values": ["val0", "val1", "val2", "val3", "val4", "val5"]},
        "columns": {
            "type": ("what kind of effect it is", ""),
            "val0": ("its first number", ""),
            "val1": ("its second number", ""),
            "val2": ("its third number", ""),
            "val3": ("its fourth number", ""),
            "val4": ("its fifth number", ""),
            "val5": ("its sixth number", ""),
        },
    },
    "master_beast_efcref": {
        "word": "beast",
        "title": "What eating a beast does",
        "about": "Which effect a beast gives, raw or grilled.",
        "row": {"kind": "via", "column": "bstid", "table": "master_beast",
                "other_key": "id", "with": ["state"]},
        "columns": {"efcid": ("the effect it gives", "")},
    },
    "master_shop_product_item": {
        "word": "product",
        "title": "Which item a shop product hands over",
        "row": {"kind": "shop_product"},
        "columns": {"itemid": ("the item you get", "")},
    },
    "master_elevator_stop_floor": {
        "word": "stop",
        "title": "The elevator's stops",
        "about": "61 stops, named from the game's own text.",
        "row": {"kind": "text", "column": "name"},
        "columns": {"elvid": ("which elevator it belongs to", "")},
    },
    "master_const_float": {
        "word": "setting",
        "title": "Game-wide settings (decimals)",
        "about": "135 numbers that apply everywhere - the chance of being "
                 "abducted is 0.03, and the ransom to get a fighter back is "
                 "worked out from their grade and level.",
        "row": {"kind": "key"},
        "columns": {"value": ("its value", "")},
    },
    "master_game_flg": {
        "word": "flag",
        "title": "Story and progress flags",
        "about": "708 switches the game sets as you play - which cutscenes you "
                 "have seen, what you have unlocked. The remarks are the "
                 "developers' own notes, in Japanese.",
        "row": {"kind": "columns", "columns": ["name"]},
        "columns": {"type": ("whether the game or the server owns it", ""),
                    "remarks": ("the developers' note about it", "")},
    },
    "master_skill_type": {
        "word": "kind of decal",
        "title": "Kinds of decal effect",
        "about": "256 effect types a Skill Decal can have. The names are the "
                 "developers' own, in Japanese.",
        "row": {"kind": "key"},
        "columns": {"name": ("the developers' name for it", ""),
                    "group": ("which group it belongs to", "")},
    },
    "master_body_name_male": {
        "word": "name",
        "title": "Names given to male fighters",
        "about": "The 455 names a male fighter can be born with.",
        "row": {"kind": "columns", "columns": ["name"]},
        "columns": {"name": ("the name", "")},
    },
    "master_body_name_female": {
        "word": "name",
        "title": "Names given to female fighters",
        "about": "The 493 names a female fighter can be born with.",
        "row": {"kind": "columns", "columns": ["name"]},
        "columns": {"name": ("the name", "")},
    },
    "master_team": {
        "word": "team",
        "title": "Teams",
        "about": "164 teams with their emblems, war records and member counts - "
                 "a snapshot of the online game, frozen as it was.",
        "row": {"kind": "columns", "columns": ["name"]},
        "columns": {
            "name": ("the team's name", ""),
            "emblem": ("its emblem", ""),
            "war_win": ("wars it has won", ""),
            "war_lose": ("wars it has lost", ""),
            "member_cnt": ("how many members it has", ""),
            "active_member_cnt": ("how many are active", ""),
            "tdm_point_avr": ("its average Death Metro points", ""),
            "pos_x": ("where it sits on the map, left to right", ""),
            "pos_y": ("where it sits on the map, top to bottom", ""),
        },
    },
    "master_region": {
        "word": "country",
        "title": "Which region a country belongs to",
        "about": "249 countries mapped onto regions, used for matchmaking when "
                 "the game was online.",
        "row": {"kind": "key"},
        "columns": {"region": ("the region it belongs to", "")},
    },
    "master_steam_dlc": {
        "word": "item",
        "title": "Steam DLC contents",
        "about": "What each piece of Steam DLC delivers, and the mail it "
                 "arrives in.",
        "row": {"kind": "text", "column": "name"},
        "columns": {
            "itemdefid": ("its Steam item id", ""),
            "appid": ("its Steam app id", ""),
            "mailid": ("the mail it arrives in", ""),
        },
    },
    "master_area_setting": {
        "word": "area",
        "title": "Per-area settings",
        "about": "Settings for each of the 263 areas, including lists written "
                 "as JSON for the conditions that apply and the rooms swapped "
                 "in when they do.",
        "row": {"kind": "columns", "columns": ["stgid", "areaid"]},
        "columns": {
            "dlm": ("(nobody has worked this one out yet)", ""),
            "conds": ("the conditions that apply, as JSON", ""),
            "replace_units": ("the rooms swapped in, as JSON", ""),
        },
    },

    "master_area_escalator": {
        "word": "elevator",
        "title": "How the Tower's floors join up",
        "about": "Every escalator in the Tower, as a spot on a lower floor and "
                 "the spot on an upper floor it leads to. Heaven holds 1,322 of "
                 "the 1,814, since its floors are stitched together from pieces.",
        # Keyed on a row number; where it goes is the useful part.
        "row": {"kind": "columns", "columns": ["lflrid", "uflrid"]},
        "columns": {
            "lflrid": ("the floor it starts on", ""),
            "lareaid": ("the area it starts in", ""),
            "lunit": ("the spot it starts at", ""),
            "uflrid": ("the floor it arrives on", ""),
            "uareaid": ("the area it arrives in", ""),
            "uunit": ("the spot it arrives at", ""),
        },
    },
    "master_custom_part": {
        "word": "outfit piece",
        "title": "What the Tower's fighters wear",
        "about": "The kit given to the fighters you meet in the Tower - Haters "
                 "and the like - one row per equipment slot. Most of it sits "
                 "under DUMMY, with a smaller set per area.",
        "row": {"kind": "via", "column": "ptid", "table": "master_part",
                "other_key": "id", "with": ["site"]},
        "columns": {
            "ptid": ("the piece of equipment worn", ""),
            "level": ("what level it is worn at", ""),
        },
    },
    "master_subtitle": {
        "word": "subtitle line",
        "title": "Cutscene subtitles",
        "about": "One row per line of spoken dialogue per language, with the "
                 "moment in the clip it appears. The text column points at the "
                 "game's own text rather than holding the words.",
        "row": {"kind": "key"},
        "columns": {
            "txt": ("the line shown", ""),
            "time": ("how far into the clip it appears", "ms"),
        },
    },

    "master_stage_trbox": {
        "word": "box",
        "title": "Where treasure boxes appear",
        "row": {"kind": "key"},
        "columns": {
            "type": ("what size of box", ""),
            "rwdtype": ("what kind of reward it holds", ""),
            "rwd": ("the reward itself", ""),
            "freq": ("how likely it is to be there", ""),
            "apid": ("how it is placed", ""),
            "game_flg": ("the condition for it being there - a name starting "
                         "with ! means the opposite", ""),
        },
    },
    "master_stage_breakable_obj": {
        "word": "object",
        "title": "Where breakable objects appear",
        "row": {"kind": "key"},
        "columns": {
            "botype": ("what kind of object", ""),
            "freq": ("how likely it is to be there", ""),
        },
    },
    "master_stage_mushroom": {
        "word": "patch",
        "title": "Where mushrooms grow",
        "row": {"kind": "key"},
        "columns": {
            "msrid": ("which mushroom grows there", ""),
            "freq": ("how likely it is to be there", ""),
            "genid": ("which set it is drawn from", ""),
        },
    },
    "master_stage_beast": {
        "word": "spot",
        "title": "Where beasts appear",
        "about": "Vanilla leaves the beast itself blank here - which one turns "
                 "up is decided by the floor's own settings.",
        "row": {"kind": "key"},
        "columns": {
            "freq": ("how likely one is to be there", ""),
            "bstid": ("which beast - empty on every row in vanilla", ""),
        },
    },
    "master_stage_item": {
        "word": "spot",
        "title": "Where loose items appear",
        "about": "Vanilla leaves the item itself blank here - what actually "
                 "appears comes from the floor's drop tables.",
        "row": {"kind": "key"},
        "columns": {
            "freq": ("how likely something is to be there", ""),
            "itemid": ("which item - empty on every row in vanilla", ""),
            "genid": ("which set - empty on every row in vanilla", ""),
        },
    },
    "master_stage_vending_machine": {
        "word": "spot",
        "title": "Where vending machines appear",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely one is to be there (100 on every row in vanilla)", "")},
    },
    "master_stage_stamp": {
        "word": "spot",
        "title": "Where Uncle Death stamps appear",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely one is to be there", "")},
    },
    "master_stage_mboss": {
        "word": "spot",
        "title": "Where mini-bosses appear",
        "row": {"kind": "key"},
        "columns": {
            "type": ("which mini-boss", ""),
            "freq": ("how likely it is to be there", ""),
            "itemid": ("what it drops", ""),
            "path": ("the route it walks", ""),
        },
    },
    "master_stage_gate": {
        "word": "gate",
        "title": "Where gates appear",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely it is to be there", "")},
    },
    "master_stage_bo": {
        "word": "object",
        "title": "Breakable object strength",
        "row": {"kind": "key"},
        "columns": {"hp": ("how much punishment it takes", "")},
    },
    # -- what drops where --------------------------------------------------
    # Two tables working together: this one picks WHICH group of items can
    # drop, and the ptlvl one picks what LEVEL the dropped gear comes out at.
    # "grp" is an item group - 137 of the 157 groups here are groups in
    # master_item, and group 181 holds one item, D.O.D. ARMS Green Metal.
    # -- the pools things are picked from ---------------------------------
    # All four share a shape: a named set, a thing in it, and a weight. The
    # weight is measured against the others in the same set, not out of 100 -
    # vanilla's item sets happen to add up to 100, but the mushroom and beast
    # sets do not, so doubling one entry is what makes it twice as likely.
    "master_mushroom_gen": {
        "word": "entry",
        "title": "Which mushrooms grow where",
        "about": "77 named sets of mushrooms. A spot in the Tower points at a "
                 "set, and the set decides what grows there.",
        "row": {"kind": "via", "column": "msrid", "table": "master_mushroom",
                "other_key": "id", "text_column": "c_name", "with": ["id"]},
        "columns": {"freq": ("how likely that mushroom is, against the rest of its set", "")},
    },
    "master_mushroom_gen_odds": {
        "word": "entry",
        "title": "Which mushrooms grow where, by season",
        "about": "The same sets again, once per season - spring, summer, "
                 "autumn and winter each get their own odds, which is how the "
                 "Tower's mushrooms change through the year.",
        "row": {"kind": "via", "column": "msrid", "table": "master_mushroom",
                "other_key": "id", "text_column": "c_name", "with": ["odds_id", "id"]},
        "columns": {"freq": ("how likely that mushroom is that season", "")},
    },
    "master_beast_gen": {
        "word": "entry",
        "title": "Which beasts appear where",
        "about": "68 named sets of frogs, scorpions, bats and the rest. A spot "
                 "in the Tower points at a set and the set decides what turns up.",
        "row": {"kind": "via", "column": "bstid", "table": "master_beast",
                "other_key": "id", "with": ["id"]},
        "columns": {"freq": ("how likely that beast is, against the rest of its set", "")},
    },
    "master_item_gen": {
        "word": "entry",
        "title": "Which items are found where",
        "about": "82 named sets of materials and items. Unlike the mushroom and "
                 "beast sets, every one of these adds up to exactly 100, so the "
                 "numbers read as percentages.",
        "row": {"kind": "via", "column": "itemid", "table": "master_item",
                "other_key": "itemid", "with": ["id"]},
        "columns": {"freq": ("its share of that set", "%")},
    },

    "master_floor_drop_gen": {
        "word": "entry",
        "title": "What drops on each floor",
        "about": "For every floor and every thing that can drop something - "
                 "Haters, treasure boxes, mini-bosses, Screamers - a weighted "
                 "list of the item groups it can give you. The numbers are "
                 "weights against each other, not percentages.",
        "row": {"kind": "via", "column": ["flrid", "areaid"], "table": "master_floor",
                "other_key": ["id", "areaid"], "with": ["type", "grp"]},
        "columns": {
            "freq": ("how likely that group is", ""),
            "grp": ("which group of items it can give", ""),
            "type": ("what drops it", ""),
        },
    },
    "master_floor_drop_ptlvl": {
        "word": "entry",
        "title": "What level the dropped gear is",
        "about": "Levels 1 to 5, weighted per floor and per source - the "
                 "numbers are weights against each other, not percentages. On "
                 "the first floor a large treasure box is nearly always level 4.",
        "row": {"kind": "via", "column": ["flrid", "areaid"], "table": "master_floor",
                "other_key": ["id", "areaid"], "with": ["type", "lvl"]},
        "columns": {
            "freq": ("how likely that level is", ""),
            "lvl": ("the level of the gear that drops", ""),
        },
    },
    # Vanilla only has TEST_1/2/3 rows in these, so they are leftovers.
    "master_floor_drop_gen_event": {
        "word": "entry", "title": "Event drop overrides (test data only)",
        "about": "Vanilla only holds three rows, all named TEST.",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely that group is", "")},
    },
    "master_floor_drop_ptlvl_event": {
        "word": "entry", "title": "Event drop levels (test data only)",
        "about": "Vanilla only holds six rows, all named TEST.",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely that level is", "")},
    },
    # -- shops and the vending machine ------------------------------------
    # The lineup is split by day of the week - MON to SUN with 40 items each,
    # plus COMMON (always there) and RE. That is why an item added here does
    # not show up until the in-game day rolls over.
    "master_automaticshop_lineup": {
        "word": "item",
        "title": "Vending machine stock",
        "about": "What the vending machine offers. Each day of the week has its "
                 "own list, so a new item appears when that day comes round.",
        "row": {"kind": "via", "column": "type_id", "table": "master_item",
                "other_key": "itemid", "with": ["lineup_id"]},
        "columns": {
            "type_id": ("the item it sells", ""),
            "lineup_id": ("which day's list it is on", ""),
            "display_priority": ("where it sits in the list", ""),
            "currency_type": ("what you pay with", ""),
            "stock": ("whether it is in stock", ""),
            "is_special": ("whether it is a special offer", ""),
            # Vanilla leaves the whole pack/discount machinery alone.
            "freq": ("unused - 0 on every row in vanilla", ""),
            "pack_count": ("unused - 1 on every row in vanilla", ""),
            "pack_money": ("unused - 0 on every row in vanilla", ""),
            "money_discount_rate": ("unused - 0 on every row in vanilla", ""),
            "metal_discount_rate": ("unused - 0 on every row in vanilla", ""),
        },
    },
    "master_shop_product": {
        "word": "product",
        "title": "Shop products",
        "about": "Everything a shop can sell. The names here are developer "
                 "placeholders, not what the game shows.",
        "row": {"kind": "key"},
        "columns": {
            "type": ("what kind of product it is", ""),
            "name": ("its internal label (not shown in game)", ""),
        },
    },
    "master_shop_product_display": {
        "word": "listing",
        "title": "What each shop sells",
        "row": {"kind": "key"},
        "columns": {
            "prdid": ("the product it lists", ""),
            "rate": ("how likely it is to be offered (100 on every row in vanilla)", ""),
        },
    },
    # -- the game's own wording -----------------------------------------
    # By far the most edited table: every name and description lives here, one
    # row per language, so a single changed line is really ten changed rows.
    "master_text": {
        "word": "line",
        "title": "Game text (names and descriptions)",
        "about": "The words the game shows. Each line is stored once per language.",
        "row": {"kind": "game_text", "lang": "lang", "label": "id"},
        "columns": {"txt": ("the wording shown in game", "")},
    },
    "master_const_int": {
        "word": "setting",
        "title": "Game-wide settings",
        "about": "Single numbers the game reads by name.",
        "row": {"kind": "key"},
        "columns": {"value": ("its value", "")},
    },
    "master_shop_appearance": {
        "word": "entry",
        "title": "Where and when shops appear",
        "row": {"kind": "key"},
        "columns": {"rate": ("how likely it is to appear", "")},
    },
    # -- things the shipped mods touch, so the tab is useful today -------
    "master_shop_product_price": {
        "word": "product",
        "title": "Shop prices",
        "row": {"kind": "shop_product"},
        "columns": {
            "price": ("the price", "KC"),
            "medal": ("the Death Metal price", "Death Metals"),
            "spirit": ("the SPLithium price", "SPLithium"),
        },
    },
    "master_body_detail": {
        "word": "tier",
        "title": "Fighter tiers",
        "about": "What each fighter type and grade gives you. Note that "
                 "skill_slots is a LIST of the slots that are open (\"1,2,3\"), "
                 "not a count - putting 9 there opens one slot, not nine.",
        "row": {"kind": "fighter_tier"},
        "columns": {
            "price": ("the cost to unlock it", "KC"),
            "price_spirit": ("the SPLithium cost to unlock it", "SPLithium"),
            "discard_spirit": ("the SPLithium you get for discarding one", "SPLithium"),
            "shop_open_floor": ("the floor that unlocks it in the shop", ""),
            "param_lv_max": ("its level cap", ""),
            "bag_capacity": ("how much its Death Bag holds", "slots"),
            "skill_slots": ("which decal slots are open, as a list", ""),
            "rage_capacity": ("how many Rage Moves it can hold", ""),
            "battle_rate": ("its damage rate against other players", "%"),
            "abduct_bns_bag": ("the bag odds for abducting with it, as a list", ""),
            "rank_point": ("the rank points unlocking it gives", "pts"),
            "initial_parts": ("what it starts out wearing", ""),
        },
    },
    "master_skill": {
        "word": "skill",
        "title": "Skills and decals",
        "row": {"kind": "text", "column": "name"},
        # val0-val5 are deliberately absent: what they mean changes from one
        # skill to the next, so the skill's own description is shown instead.
        "describes": {"column": "desc", "values": ["val0", "val1", "val2", "val3", "val4", "val5"]},
        "columns": {
            "buy_money": ("the price to buy it", "KC"),
            "buy_spirit": ("the SPLithium price", "SPLithium"),
            "sell_money": ("what you get for selling it", "KC"),
            "rarity": ("its star rating", ""),
            "premium": ("whether it is a premium decal", ""),
        },
    },

    # -- the Hunter ---------------------------------------------------------
    # Send a hunter off and they come back with things. Three tables set what
    # they bring: one per floor, one per hour spent, one per luck.
    "master_hunter_reward_base": {
        "word": "floor",
        "title": "What a hunter brings back from a floor",
        "row": {"kind": "columns", "columns": ["flrid", "areaid"]},
        "columns": {
            "msrmin": ("the fewest mushrooms", ""), "msrmax": ("the most mushrooms", ""),
            "bstmin": ("the fewest beasts", ""), "bstmax": ("the most beasts", ""),
            "moneymin": ("the least Kill Coins", "KC"), "moneymax": ("the most Kill Coins", "KC"),
            "spiritmin": ("the least SPLithium", "SPLithium"),
            "spiritmax": ("the most SPLithium", "SPLithium"),
            "materialmin": ("the fewest materials", ""), "materialmax": ("the most materials", ""),
            "trboxmin": ("the fewest treasure boxes", ""),
            "trboxmax": ("the most treasure boxes", ""),
            "nopickup": ("whether they come back empty-handed", ""),
        },
    },
    "master_hunter_reward_hour": {
        "word": "hour",
        "title": "What a hunter brings back per hour",
        "about": "What each hour away adds, for 48 hours.",
        "row": {"kind": "number", "word": "hour"},
        "columns": {
            "msrmin": ("the fewest mushrooms", ""), "msrmax": ("the most mushrooms", ""),
            "bstmin": ("the fewest beasts", ""), "bstmax": ("the most beasts", ""),
            "moneymin": ("the least Kill Coins", "KC"), "moneymax": ("the most Kill Coins", "KC"),
            "spiritmin": ("the least SPLithium", "SPLithium"),
            "spiritmax": ("the most SPLithium", "SPLithium"),
            "materialmin": ("the fewest materials", ""), "materialmax": ("the most materials", ""),
            "trboxmin": ("the fewest treasure boxes", ""),
            "trboxmax": ("the most treasure boxes", ""),
        },
    },
    "master_hunter_reward_luck": {
        "word": "luck band",
        "title": "What a hunter's luck adds",
        "about": "The same rewards again, banded by the hunter's Luck.",
        "row": {"kind": "labelled", "words": ["luck", "to"]},
        "columns": {
            "msrmin": ("the fewest mushrooms", ""), "msrmax": ("the most mushrooms", ""),
            "bstmin": ("the fewest beasts", ""), "bstmax": ("the most beasts", ""),
            "moneymin": ("the least Kill Coins", "KC"), "moneymax": ("the most Kill Coins", "KC"),
            "spiritmin": ("the least SPLithium", "SPLithium"),
            "spiritmax": ("the most SPLithium", "SPLithium"),
            "materialmin": ("the fewest materials", ""), "materialmax": ("the most materials", ""),
            "trboxmin": ("the fewest treasure boxes", ""),
            "trboxmax": ("the most treasure boxes", ""),
        },
    },
    "master_hunter_reward_event_base": {
        "word": "floor",
        "title": "What a hunter brings back during an event",
        "row": {"kind": "columns", "columns": ["eventid", "flrid", "areaid"]},
        "columns": {
            "msrmin": ("the fewest mushrooms", ""), "msrmax": ("the most mushrooms", ""),
            "bstmin": ("the fewest beasts", ""), "bstmax": ("the most beasts", ""),
            "moneymin": ("the least Kill Coins", "KC"), "moneymax": ("the most Kill Coins", "KC"),
        },
    },
    "master_hunter_drop": {
        "word": "find",
        "title": "Gear a hunter comes back with",
        "about": "Banded by Player Rank, with what the find is worth.",
        "row": {"kind": "key"},
        "columns": {
            "type": ("whether it is a weapon or armour", ""),
            "rank_min": ("the lowest Player Rank it applies to", ""),
            "rank_max": ("the highest Player Rank it applies to", ""),
            "money": ("what it is worth", "KC"),
        },
    },
    # -- your base, and raids on it ----------------------------------------
    "master_freezer": {
        "word": "upgrade",
        "title": "Freezer upgrades",
        "about": "How many fighters you can keep on ice, and what each "
                 "expansion costs.",
        "row": {"kind": "number", "word": "upgrade"},
        "columns": {
            "price": ("what the upgrade costs", "KC"),
            "count": ("how many fighters it holds", ""),
            "waiting_minute": ("how long the upgrade takes", "min"),
            "rank_point": ("the rank points it gives", ""),
            "sum_rank_point": ("the running total of rank points", ""),
        },
    },
    "master_prison": {
        "word": "upgrade",
        "title": "Jail upgrades",
        "about": "How many captured fighters you can hold, and what each "
                 "expansion costs.",
        "row": {"kind": "number", "word": "upgrade"},
        "columns": {
            "price": ("what the upgrade costs", "KC"),
            "count": ("how many captives it holds", ""),
            "waiting_minute": ("how long the upgrade takes", "min"),
            "rank_point": ("the rank points it gives", ""),
            "sum_rank_point": ("the running total of rank points", ""),
        },
    },
    "master_fort_whistle": {
        "word": "alarm",
        "title": "Base alarms",
        "about": "The alarms you can buy for your base, in levels, with the "
                 "floor you have to reach before the shop sells them.",
        "row": {"kind": "text", "column": "name"},
        "columns": {
            "group": ("which alarm family it belongs to", ""),
            "lvl": ("its level", ""),
            "price": ("what it costs", "KC"),
            "shop_open_floor": ("the floor that unlocks it in the shop", ""),
        },
    },
    "master_fort_whistle_gen": {
        "word": "entry",
        "title": "Which alarms a base gets",
        "row": {"kind": "key"},
        "columns": {
            "whistle_id": ("the alarm", ""),
            "type": ("what kind of alarm it is", ""),
            "lvl": ("its level", ""),
            "num": ("how many of it", ""),
            "freq": ("how likely it is, against the rest of its set", ""),
        },
    },
    "master_fort_break_bonus_rate": {
        "word": "band",
        "title": "Bonus for wrecking a base",
        "about": "How much extra you get, banded by how much of the base you "
                 "destroyed.",
        "row": {"kind": "key"},
        "columns": {
            "rate_min": ("the least destruction it applies to", "%"),
            "rate_max": ("the most destruction it applies to", "%"),
            "bonus_rate": ("the bonus it gives", "%"),
        },
    },
    "master_fort_abduct_rate": {
        "word": "band",
        "title": "The chance of abducting a fighter",
        "row": {"kind": "key"},
        "columns": {
            "cond_min": ("the bottom of the band", ""),
            "cond_max": ("the top of the band", ""),
            "rate": ("the chance within it", ""),
        },
    },
    "master_fort_event": {
        "word": "event",
        "title": "Base event settings",
        "about": "One row, holding the dates and reward rates for a base event.",
        "row": {"kind": "key"},
        "columns": {
            "start_date": ("when it starts", ""),
            "end_date": ("when it ends", ""),
            "breakout_rate": ("the breakout rate", ""),
            "war_money_rwd_rate": ("the Kill Coin reward rate", ""),
            "war_spirit_rwd_rate": ("the SPLithium reward rate", ""),
            "war_medal_rwd_rate": ("the Death Metal reward rate", ""),
        },
    },
    "master_war_reward": {
        "word": "reward",
        "title": "Team war rewards",
        "about": "What a team war pays out, by what you achieved - people "
                 "abducted, bases wrecked - with a winning and a losing rate "
                 "for each. The names are the developers' own, in Japanese.",
        "row": {"kind": "key"},
        "columns": {
            "type": ("what it is measuring", ""),
            "name": ("the developers' name for it", ""),
            "cond_name": ("the developers' note on the condition", ""),
            "cond_min": ("the least that qualifies", ""),
            "cond_max": ("the most it applies to", ""),
            "count_diff_rate_min": ("the bottom of the team-size gap it applies to", ""),
            "count_diff_rate_max": ("the top of the team-size gap it applies to", ""),
            "win_spirit": ("the SPLithium for winning", "SPLithium"),
            "win_money": ("the Kill Coins for winning", "KC"),
            "win_medal": ("the Death Metals for winning", "Death Metals"),
            "win_mysterybag": ("the Mystery Bag for winning", ""),
            "lose_spirit": ("the SPLithium for losing", "SPLithium"),
            "lose_money": ("the Kill Coins for losing", "KC"),
            "lose_medal": ("the Death Metals for losing", "Death Metals"),
            "lose_mysterybag": ("the Mystery Bag for losing", ""),
        },
    },
    # -- Haters and Jackals -------------------------------------------------
    "master_model_hater": {
        "word": "Hater",
        "title": "The Haters you meet in the Tower",
        "about": "The 28 Hater builds: their fighter type and grade, the level "
                 "range they appear at, how their attack and defense are "
                 "adjusted, and where their name, outfit, decals and drops "
                 "come from. atkup and defup run negative as well as positive, "
                 "so they adjust rather than set.",
        "row": {"kind": "key"},
        "columns": {
            "type": ("its fighter type", ""),
            "grade": ("its grade", ""),
            "lvl_min": ("the lowest level it appears at", ""),
            "lvl_max": ("the highest level it appears at", ""),
            "atkup": ("how its attack is adjusted", "%"),
            "defup": ("how its defense is adjusted", "%"),
            "zmb_money_min": ("the least Kill Coins it carries", "KC"),
            "zmb_money_max": ("the most Kill Coins it carries", "KC"),
            "expert_lvl": ("its expert level", ""),
            "name_gen": ("which pool its name comes from", ""),
            "custom_part": ("which outfit it wears", ""),
            "eq_skl_gen": ("which set its decals come from", ""),
            "drop_gen": ("which set its drops come from", ""),
        },
    },
    "master_model_drop": {
        "word": "drop",
        "title": "What Haters drop",
        "row": {"kind": "via", "column": "itemid", "table": "master_item",
                "other_key": "itemid", "with": ["dropgenid"]},
        "columns": {"freq": ("how likely it is, against the rest of its set", "")},
    },
    "master_model_schedule": {
        "word": "schedule",
        "title": "When the Hater model patterns change",
        "about": "One row, naming the pattern in use and when it expires.",
        "row": {"kind": "columns", "columns": ["pattern"]},
        "columns": {"expire": ("when it expires", "")},
    },
    "master_jackal": {
        "word": "Jackal",
        "title": "Jackals",
        "about": "The 15 Jackals, with their stats, what they wear and what "
                 "they drop. Their names are placeholders in the database.",
        "row": {"kind": "key"},
        "columns": {
            "asset": ("its model", ""), "gender": ("its gender", ""),
            "hp": ("its HP", ""), "str": ("its Strength", ""),
            "dex": ("its Dexterity", ""), "vit": ("its Vitality", ""),
            "stm": ("its Stamina", ""), "luk": ("its Luck", ""),
            "exp": ("the EXP it gives", ""),
            "money_min": ("the least Kill Coins it carries", "KC"),
            "money_max": ("the most Kill Coins it carries", "KC"),
            "arml": ("what it holds in its left hand", ""),
            "armr": ("what it holds in its right hand", ""),
            "head": ("what it wears on its head", ""),
            "body": ("what it wears on its body", ""),
            "legs": ("what it wears on its legs", ""),
            "drop_item_id_1": ("the first item it can drop", ""),
            "drop_item_id_2": ("the second item it can drop", ""),
            "drop_coin_rate": ("how likely it is to drop coins", ""),
            "drop_part_weapon_rate": ("how likely it is to drop a weapon", ""),
            "drop_part_armor_rate": ("how likely it is to drop armour", ""),
            "drop_rmap_weapon_rate": ("how likely it is to drop a weapon blueprint", ""),
            "drop_rmap_armor_rate": ("how likely it is to drop an armour blueprint", ""),
            "drop_item_1_rate": ("how likely the first item is", ""),
            "drop_item_2_rate": ("how likely the second item is", ""),
        },
    },
    "master_jackal_bodylvl_status_value": {
        "word": "build",
        "title": "Jackal stats by level",
        "row": {"kind": "labelled", "words": ["level", "", "grade"]},
        "columns": {
            "hp": ("its HP", ""), "str": ("its Strength", ""),
            "dex": ("its Dexterity", ""), "vit": ("its Vitality", ""),
            "stm": ("its Stamina", ""),
            "stmrecov": ("how fast its stamina comes back", ""),
        },
    },
    # -- collectibles -------------------------------------------------------
    "master_magazine": {
        "word": "page",
        "title": "Where the magazine pages are",
        "about": "32 collectible pages, each pinned to a floor, a room and a "
                 "spot in it.",
        "row": {"kind": "labelled", "words": ["", "volume", "page"]},
        "columns": {
            "flrid": ("the floor it is on", ""), "areaid": ("the area it is in", ""),
            "unit": ("the room it is in", ""), "pntid": ("the spot it sits at", ""),
        },
    },
    "master_magazine_bonus": {
        "word": "bonus",
        "title": "Rewards for collecting magazine pages",
        "row": {"kind": "key"},
        "columns": {
            "stidx": ("the first page in the run", ""),
            "edidx": ("the last page in the run", ""),
            "rwdid": ("what it gives", ""),
        },
    },
    "master_stamp": {
        "word": "stamp",
        "title": "Which floor carries which stamp",
        "about": "The 50 Uncle Death stamps, one per floor.",
        "row": {"kind": "columns", "columns": ["flrid"]},
        "columns": {"idx": ("its place on the stamp card", "")},
    },
    "master_stamp_bonus": {
        "word": "bonus",
        "title": "Rewards for filling the stamp card",
        "row": {"kind": "key"},
        "columns": {
            "stidx": ("the first stamp in the run", ""),
            "edidx": ("the last stamp in the run", ""),
            "rwdid": ("what it gives", ""),
            "perfect": ("whether it needs a perfect run", ""),
            "flg": ("the flag it sets", ""),
        },
    },
    "master_poster_param": {
        "word": "poster",
        "title": "Where the posters are",
        "row": {"kind": "key"},
        "columns": {"posterid": ("which poster it is", "")},
    },
    "master_voucher": {
        "word": "voucher",
        "title": "Vouchers",
        "about": "What each voucher code hands over - a piece of armour, a "
                 "beast, and so on.",
        "row": {"kind": "key"},
        "columns": {"type": ("what kind of thing it gives", ""),
                    "value": ("what it gives", "")},
    },
    # -- how a fighter is put together --------------------------------------
    "master_body": {
        "word": "body",
        "title": "Fighter bodies",
        "about": "The base models a fighter can be born with: body, hair, "
                 "gender, skin tone and voice.",
        "row": {"kind": "key"},
        "columns": {
            "baid": ("its body model", ""), "haid": ("its hair model", ""),
            "gender": ("its gender", ""),
            "colr": ("how red its skin is", ""), "colg": ("how green its skin is", ""),
            "colb": ("how blue its skin is", ""),
            "voicep": ("its voice pitch", ""), "voiceb": ("its voice bank", ""),
            "provoke": ("which taunt it uses", ""),
        },
    },
    "master_gasmask": {
        "word": "mask",
        "title": "Gas mask models",
        "row": {"kind": "key"},
        "columns": {"gender": ("which gender it is for", ""),
                    "type": ("which kind of mask it is", "")},
    },
    # -- the Tower's furniture ----------------------------------------------
    "master_elevator": {
        "word": "elevator",
        "title": "The elevators",
        "row": {"kind": "key"},
        "columns": {"name": ("its internal name", ""),
                    "clridx": ("which colour it is", "")},
    },
    "master_floor_material": {
        "word": "floor",
        "title": "What each floor is made of",
        "about": "The surface a floor is built from - Moss, Desert, Blood and "
                 "the like. This is the table the community's floor-renaming "
                 "mods edit.",
        "row": {"kind": "columns", "columns": ["flrid", "areaid"]},
        "columns": {"mat": ("the material it is made of", "")},
    },
    "master_lastfloor": {
        "word": "floor",
        "title": "The top of the Tower",
        "about": "One row, naming the last floor there is and the letter Uncle "
                 "Death leaves you there.",
        "row": {"kind": "columns", "columns": ["flrid", "areaid"]},
        "columns": {"msg": ("the letter left for you", ""),
                    "isLast": ("whether it really is the last floor", "")},
    },
    "master_dustshooter": {
        "word": "spot",
        "title": "Where the dust shooters are",
        "row": {"kind": "key"},
        "columns": {"type": ("which kind it is", "")},
    },
    "master_gate": {
        "word": "gate",
        "title": "Gates",
        "row": {"kind": "key"},
        "columns": {"type": ("what kind of gate it is", ""),
                    "val0": ("its first number", ""), "val1": ("its second number", "")},
    },
    "master_stage_fourforcemen": {
        "word": "spot",
        "title": "Where the Four Forcemen appear",
        "row": {"kind": "key"},
        "columns": {
            "type": ("which of them it is", ""),
            "lvl": ("what level they are", ""),
            "freq": ("how likely they are to be there", ""),
        },
    },
    "master_stage_beast_floor": {
        "word": "spot",
        "title": "Beasts placed on a specific floor",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely it is", ""), "bstid": ("which beast", ""),
                    "genid": ("which set it is drawn from", "")},
    },
    "master_stage_mushroom_floor": {
        "word": "spot",
        "title": "Mushrooms placed on a specific floor",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely it is", ""), "msrid": ("which mushroom", ""),
                    "genid": ("which set it is drawn from", "")},
    },
    "master_floor_item": {
        "word": "spot",
        "title": "Items placed on a specific floor",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely it is", ""), "itemid": ("which item", ""),
                    "genid": ("which set it is drawn from", "")},
    },
    "master_trbox_gen": {
        "word": "entry",
        "title": "Which treasure boxes appear",
        "row": {"kind": "key"},
        "columns": {"trid": ("which box", ""),
                    "freq": ("how likely it is, against the rest of its set", "")},
    },
    "master_deathbox_gen": {
        "word": "band",
        "title": "Which colour Death Box you get",
        "about": "Weighted by Player Rank - the higher your rank, the better "
                 "the odds of a gold box.",
        "row": {"kind": "key"},
        "columns": {
            "rank_min": ("the lowest Player Rank it applies to", ""),
            "rank_max": ("the highest Player Rank it applies to", ""),
            "blue": ("the weight for a blue box", ""),
            "copper": ("the weight for a copper box", ""),
            "silver": ("the weight for a silver box", ""),
            "gold": ("the weight for a gold box", ""),
        },
    },
    # -- decals, mushrooms and points ---------------------------------------
    "master_skill_group": {
        "word": "group",
        "title": "Caps on stacked decal effects",
        "about": "How far an effect can be stacked - equipment requirements "
                 "can be loosened by at most 10%, however many decals you wear.",
        "row": {"kind": "key"},
        "describes": {"column": "desc", "values": ["max_value"]},
        "columns": {"max_value": ("the most it can reach", "")},
    },
    "master_skill_open_mushroom": {
        "word": "decal",
        "title": "Mushrooms that unlock a decal",
        "about": "Eat enough of the right mushroom and the decal becomes "
                 "available.",
        "row": {"kind": "via", "column": "sklid", "table": "master_skill",
                "other_key": "id", "with": ["msrid"]},
        "columns": {"cnt": ("how many you have to eat", "")},
    },
    "master_mushroom_efctype": {
        "word": "kind of effect",
        "title": "Kinds of mushroom effect",
        "row": {"kind": "key"},
        "columns": {"name": ("its name", ""), "attr": ("which element it uses", ""),
                    "vfx": ("the effect shown on screen", "")},
    },
    "master_beast_efc": {
        "word": "effect",
        "title": "What eating a beast does",
        "row": {"kind": "text", "column": "name"},
        "describes": {"column": "desc", "values": ["val0", "val1"]},
        "columns": {"type": ("what kind of effect it is", ""),
                    "val0": ("its first number", ""), "val1": ("its second number", "")},
    },
    "master_beast_efctype": {
        "word": "kind of effect",
        "title": "Kinds of beast effect",
        "row": {"kind": "key"},
        "columns": {"name": ("its name", "")},
    },
    "master_enemy_abp": {
        "word": "enemy",
        "title": "ABP given by each kind of enemy",
        "about": "A boss gives 90, a mid-boss 70, a beast 5.",
        "row": {"kind": "key"},
        "columns": {"val": ("the ABP it gives", "")},
    },
    "master_stage_abp": {
        "word": "area",
        "title": "ABP given per area",
        "row": {"kind": "key"},
        "columns": {"val": ("the ABP it gives", "")},
    },
    "master_expert_point_reward": {
        "word": "reward",
        "title": "What expert points buy",
        "about": "The names are the developers' own, in Japanese.",
        "row": {"kind": "columns", "columns": ["type"]},
        "columns": {
            "point": ("what it costs", ""), "name": ("the developers' name for it", ""),
            "val0": ("its first number", ""), "val1": ("its second number", ""),
        },
    },
    "master_expert_point_reward_type": {
        "word": "kind", "title": "Kinds of expert point reward",
        "row": {"kind": "key"}, "columns": {"name": ("its name", "")},
    },
    "master_expert_lvl_reward_type": {
        "word": "kind", "title": "Kinds of expert level reward",
        "row": {"kind": "key"},
        "columns": {"name": ("its name", ""), "icon": ("its icon", "")},
    },
    # -- lookups the game keeps in its own language -------------------------
    # These name the codes used elsewhere, in Japanese, as the developers
    # wrote them. Left as they are rather than translated.
    "master_atkattr": {
        "word": "element", "title": "The six damage elements",
        "about": "Blunt, piercing, slashing, fire, electric and poison. The "
                 "names here are the developers' Japanese labels.",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_eqsite": {
        "word": "slot", "title": "Equipment slots",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_part_type": {
        "word": "kind", "title": "Kinds of equipment",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_part_class": {
        "word": "class", "title": "Equipment classes",
        "about": "Lower, higher and highest.",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_grip": {
        "word": "grip", "title": "How a weapon is held",
        "about": "One-handed, two-handed, or neither.",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_gender": {
        "word": "gender", "title": "Genders",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_money_type": {
        "word": "kind", "title": "Battery sizes",
        "about": "Not Kill Coins - these are the 10, 50 and 100 BATTERY pickups.",
        "row": {"kind": "key"}, "columns": {"name": ("its name", "")},
    },
    "master_mboss": {
        "word": "mid-boss", "title": "The mid-bosses",
        "about": "Eight of them, named in Japanese for what each has enhanced - "
                 "hearing, sight, predation and so on.",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_trbox_type": {
        "word": "kind", "title": "Kinds of treasure box",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_trbox_appearance": {
        "word": "look", "title": "How a treasure box looks",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_asset_type": {
        "word": "kind", "title": "Kinds of game asset",
        "row": {"kind": "key"}, "columns": {"name": ("its Japanese name", "")},
    },
    "master_escattr": {
        "word": "kind", "title": "Escalator attributes",
        "about": "Seven codes with no names of their own - the name column just "
                 "repeats the id, so the database says nothing about what they "
                 "mean.",
        "row": {"kind": "key"}, "columns": {"name": ("its name (a copy of its id)", "")},
    },
    "master_blt_type": {
        "word": "ammo", "title": "What ammunition costs",
        "row": {"kind": "key"}, "columns": {"price": ("what a round costs", "KC")},
    },
    # -- settings, schedules and text ---------------------------------------
    "master_const_str": {
        "word": "setting",
        "title": "Game-wide settings (text)",
        "about": "36 named strings, including the client version the database "
                 "was built for.",
        "row": {"kind": "key"}, "columns": {"value": ("its value", "")},
    },
    "master_event_schedule": {
        "word": "event",
        "title": "When events ran",
        "about": "Dated events from when the game was online, as unix "
                 "timestamps.",
        "row": {"kind": "key"},
        "columns": {
            "type": ("what kind of event it is", ""),
            "start": ("when it starts", ""), "end": ("when it ends", ""),
            "val0": ("its first setting", ""), "val1": ("its second setting", ""),
            "val2": ("its third setting", ""),
        },
    },
    "master_automaticshop_schedule": {
        "word": "schedule",
        "title": "When the vending machine restocks",
        "about": "Which lineups the machine uses and how many goods it offers.",
        "row": {"kind": "columns", "columns": ["expire"]},
        "columns": {
            "expire": ("when this schedule runs out", ""),
            "purchase_lineup_id": ("the lineup it sells from", ""),
            "common_lineup_id": ("the lineup it always sells from", ""),
            "purchase_goods_min": ("the fewest goods it offers", ""),
            "purchase_goods_max": ("the most goods it offers", ""),
            "exchange_lineup_id": ("the lineup it trades from", ""),
            "exchange_goods_min": ("the fewest trades it offers", ""),
            "exchange_goods_max": ("the most trades it offers", ""),
            "bloodnium_exchange_lineup_id": ("the lineup it trades Bloodnium from", ""),
        },
    },
    "master_waiting_reduce": {
        "word": "band",
        "title": "Shortening a wait",
        "row": {"kind": "key"},
        "columns": {
            "min_minute": ("the shortest wait it applies to", "min"),
            "max_minute": ("the longest wait it applies to", "min"),
            "add_rate": ("how much of the wait it removes", ""),
            "fix_add": ("the flat amount it removes", ""),
        },
    },
    "master_onetime_announce": {
        "word": "announcement",
        "title": "One-off announcements",
        "row": {"kind": "key"},
        "columns": {
            "type": ("what kind it is", ""), "timing": ("when it is shown", ""),
            "start": ("when it starts being shown", ""),
            "end": ("when it stops", ""), "textid": ("the text it shows", ""),
            "saveflgvar": ("the flag that records it was seen", ""),
            "condflgvar": ("the flag it depends on", ""),
            "ischeckcreated": ("whether it only shows to new players", ""),
        },
    },
    "master_terms": {
        "word": "document",
        "title": "Terms and privacy text",
        "about": "The legal text, per region and language.",
        "row": {"kind": "columns", "columns": ["region", "language"]},
        "columns": {
            "version": ("its version", ""), "contents": ("the terms text", ""),
            "policy": ("the privacy text", ""), "platform": ("which platform it is for", ""),
        },
    },
    "master_skillgacha_cautions": {
        "word": "notice",
        "title": "Decal draw small print",
        "row": {"kind": "columns", "columns": ["gacha_type", "region", "language"]},
        "columns": {"contents": ("the notice shown", "")},
    },
    "master_credit_steam": {
        "word": "line",
        "title": "The credits roll",
        "about": "1,070 lines, each with a layout type and up to ten pieces of "
                 "text. Names in the credits are real people - changing them "
                 "removes someone's credit for their work.",
        "row": {"kind": "columns", "columns": ["type"]},
        "columns": dict(
            [("type", ("how the line is laid out", ""))]
            + [(f"p{i}", (f"the text in column {i + 1}", "")) for i in range(10)]
        ),
    },
    "master_radio_jingle": {
        "word": "jingle", "title": "Radio jingles",
        "row": {"kind": "key"}, "columns": {"type": ("what kind of jingle it is", "")},
    },
    "master_radio_yotsuyama": {
        "word": "track", "title": "Yotsuyama radio tracks",
        "row": {"kind": "key"}, "columns": {"music_id": ("the track", "")},
    },
    "master_mushroom_odds": {
        "word": "set",
        "title": "Which seasonal mushroom odds are in force",
        "about": "Names each seasonal odds set and the window it applies in.",
        "row": {"kind": "columns", "columns": ["odds_id"]},
        "columns": {
            "name": ("its name", ""), "platform": ("which platform it is for", ""),
            "inspires": ("when it comes into force", ""),
            "expires": ("when it stops", ""),
        },
    },
    "master_mysterybag_content_odds": {
        "word": "set",
        "title": "Which Mystery Bag odds are in force",
        "about": "One row: offline.",
        "row": {"kind": "columns", "columns": ["odds_id"]},
        "columns": {
            "name": ("its name", ""), "platform": ("which platform it is for", ""),
            "inspires": ("when it comes into force", ""),
            "expires": ("when it stops", ""),
        },
    },
    "master_shop_product_part": {
        "word": "product",
        "title": "Which equipment a shop product hands over",
        "row": {"kind": "shop_product"},
        "columns": {"ptid": ("the equipment you get", "")},
    },
    # -- area plumbing ------------------------------------------------------
    # These wire rooms and areas together. Mostly ids pointing at other ids,
    # with little in them a player would recognise.
    "master_area_setting_unit": {
        "word": "room",
        "title": "Which rooms an area can use",
        "row": {"kind": "key"},
        "columns": {"kis": ("which variant of the room", "")},
    },
    "master_ref_area_setting": {
        "word": "area",
        "title": "Which layouts an area can borrow",
        "about": "An area can be built from another area's layout; this weights "
                 "the choice.",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely that layout is", "")},
    },
    "master_ref_boss_area_setting": {
        "word": "floor",
        "title": "Which layouts a boss floor can borrow",
        "row": {"kind": "key"},
        "columns": {"freq": ("how likely that layout is", "")},
    },
    "master_area_connect_node_repeat_straight": {
        "word": "run",
        "title": "Runs of floors that connect the same way",
        "about": "A shorthand for floors that all join up identically, written "
                 "once instead of one row per floor.",
        "row": {"kind": "key"},
        "columns": {
            "flrid_prefix": ("the start of the floor ids it covers", ""),
            "flrid_suffix_digit": ("how many digits their numbers have", ""),
            "flrid_suffix_ofs": ("what their numbering starts from", ""),
            "start_index": ("the first floor in the run", ""),
            "end_index": ("the last floor in the run", ""),
            "areaids": ("the areas it covers", ""),
            "desc_toflr": ("where it leads", ""),
        },
    },
    "master_area_connect_node_TEST": {
        "word": "connection",
        "title": "Area connections (test data)",
        "about": "A copy of the area connection table with TEST in its name. "
                 "Whether the game reads it at all is not known.",
        "row": {"kind": "columns", "columns": ["id", "flrid", "areaid"]},
        "columns": {
            "elvflrid": ("the elevator stop it joins", ""),
            "isdef": ("whether this is the default way in", ""),
            "ofsx": ("how far along the stop it sits", ""),
            "flagofsxs": ("extra positions that only apply under a game flag", ""),
        },
    },
    "monitoring": {
        "word": "row",
        "title": "monitoring",
        "about": "One row holding one number, with no name and nothing pointing "
                 "at it. Nobody has worked out what it is for.",
        "row": {"kind": "key"},
        "columns": {"value": ("the number, whatever it means", "")},
    },
}

# The shop's own product names in the database are developer placeholders
# ("Product 00"), so the useful ones are written out here.
NAMES: dict[str, str] = {
    "PRD_CONTINUE": "Revive",
}

# Coded values the game stores in ordinary columns. Swapped for plain words
# wherever a value is shown, so a change reads "fire" instead of
# "ATKATTR_FIRE". Anything not listed is shown exactly as it is stored.
VALUES: dict[str, str] = {
    # what dropped it
    "PTGENTP_HATER_L": "a big Hater",
    "PTGENTP_HATER_M": "a Hater",
    "PTGENTP_TRBOX_S": "a small treasure box",
    "PTGENTP_TRBOX_M": "a treasure box",
    "PTGENTP_TRBOX_L": "a large treasure box",
    "PTGENTP_TRBOX_SPXL_RARE": "a rare treasure box",
    "PTGENTP_TRZAKO": "a small enemy",
    "PTGENTP_ZOMBIE": "a Screamer",
    "PTGENTP_MBOSS1": "mini-boss 1",
    "PTGENTP_MBOSS2": "mini-boss 2",
    "PTGENTP_MBOSS3": "mini-boss 3",
    "PTGENTP_MBOSS4": "mini-boss 4",
    # damage types
    "ATKATTR_SLASH": "slashing", "ATKATTR_HIT": "blunt hits",
    "ATKATTR_SHOOT": "gunfire", "ATKATTR_FIRE": "fire",
    "ATKATTR_ELEC": "electricity", "ATKATTR_POISON": "poison",
    # stat bonuses
    "PRMOFS_HP": "HP", "PRMOFS_STR": "Strength", "PRMOFS_DEX": "Dexterity",
    "PRMOFS_VIT": "Vitality", "PRMOFS_STM": "Stamina",
    # the seven areas of the Tower (their English names are in master_stage)
    "S_AMS": "Amusement", "S_ARC": "Arcade", "S_HVN": "Heaven",
    "S_HZM": "Hazama", "S_MET": "the Metro", "S_RFT": "the Rooftop",
    "S_LAS": "the last boss area",
    # small enemies
    "ZAKO_BONE": "a bone enemy", "ZAKO_HOVERING": "a hovering enemy",
    "ZAKO_TURRET": "a turret", "ZAKO_SCRATCH": "a scratching enemy",
    "ZAKO_TREASURE": "a treasure enemy", "ZAKO_REVERSAL": "a reversal enemy",
    # treasure boxes
    "TBTP_SMALL": "small", "TBTP_MEDIUM": "medium", "TBTP_LARGE": "large",
    "TBTP_SPL": "special large", "TBTP_SPM": "special medium",
    "TBTP_SPXL": "special extra large",
    "TBRWD_ITEM": "an item", "TBRWD_MONEY": "Kill Coins",
    "TBRWD_PART": "a piece of equipment", "TBRWD_PART_ARM": "an arm piece",
    "TBRWD_PART_BODY": "a body piece", "TBRWD_PART_HEAD": "a head piece",
    "TBAP_FACE_DOWN": "face down", "TBAP_LEAN": "leaning",
    # breakable objects
    "BOX": "a box", "MINE": "a mine", "SHOOT": "a shooting target",
    # days the vending machine uses
    "MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
    "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday",
    "COMMON": "every day",
}
