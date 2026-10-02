"""Which tables the mod builder puts in front of people, and what it knows about them.

The database has 221 tables. Most of them wire the game together - which room
connects to which, what a boss's AI slot 14 holds - and someone who wants to
make the revive cost 1 Kill Coin has no use for any of it.

So the builder leads with the parts people actually ask about, sorted by what
they want to change rather than by how the database happens to be split up.
Everything else is still reachable under "Everything else", because the
descriptions exist and walling it off would help nobody.

The second half of this file is what has been learned the hard way since the
builder was first written: the tips shown above a table, which columns hold
dates, which tables are stored twice, and which rows the Steam game skips. Each
of those was a mod that looked right and did nothing, or crashed the game.

Order matters in GROUPS: it is the order the headings appear in.
"""

from __future__ import annotations

# A group is a heading, a line about it, and the tables under it, most useful
# first. Every table here must be described in explain_data (there is a test).
GROUPS: list[tuple[str, str, list[str]]] = [
    (
        "Prices & money",
        "What things cost in Kill Coins, SPLithium, Death Metals and Bloodnium, "
        "and how much the bank and the tank hold.",
        ["master_shop_product_price", "master_item", "master_safe_level",
         "master_spirit_tank_level", "master_waiting_reduce"],
    ),
    (
        "Weapons & armour",
        "What gear costs to craft and upgrade, its stats, and its defence.",
        ["master_part", "master_part_research", "master_equip_rank_point",
         "master_part_param_offset", "master_part_defattr", "master_ptarm"],
    ),
    (
        "Fighters",
        "What each fighter type and grade costs, how far it levels, its stats, "
        "and what uncapping it takes.",
        ["master_body_detail", "master_bodylvl_exp", "master_bodylvl_status_value",
         "master_bodylvl_limit_break_item", "master_freezer", "master_prison"],
    ),
    (
        "Decals",
        "Skill Decals: what they do, what they cost, and the Mushroom Club draw.",
        ["master_skill", "master_skillgacha_odds", "master_skillgacha",
         "master_skill_group", "master_skill_open_mushroom"],
    ),
    (
        "Mushrooms",
        "What mushrooms cost, what eating them does, and where they grow.",
        ["master_mushroom", "master_mushroom_efc", "master_mushroom_gen",
         "master_mushroom_gen_odds"],
    ),
    (
        "Beasts",
        "The creatures you catch and cook, and where they appear.",
        ["master_beast", "master_beast_efc", "master_beast_gen", "master_beast_param_int"],
    ),
    (
        "Drops & pickups",
        "What floors, boxes, enemies and Death Boxes give you, and how often.",
        ["master_floor_drop_gen", "master_floor_drop_ptlvl", "master_tmpfloor_item",
         "master_item_gen", "master_deathbox_gen", "master_deathbox_content_gen"],
    ),
    (
        "Vending machine & shops",
        "What the machine sells, the monthly rotation it follows, and where "
        "floor shops turn up.",
        ["master_automaticshop_lineup", "master_automaticshop_schedule",
         "master_shop_appearance"],
    ),
    (
        "Quests",
        "What quests ask for and what they pay.",
        ["master_quest", "master_quest_param_int", "master_quest_type"],
    ),
    (
        "Rewards",
        "What a reward id hands out, login bonuses, Mystery Bags and stamps.",
        ["master_reward", "master_login_bonus", "master_mysterybag_content_gen_odds",
         "master_stamp_bonus"],
    ),
    (
        "Enemies",
        "Screamers, small enemies, mid-bosses, the Four Forcemen, Haters and Jackals.",
        ["master_zombie_param", "master_zako_param_int", "master_mboss_param_int",
         "master_fourforcemen_param_int", "master_model_hater", "master_jackal",
         "master_zombie_phlvl_alloc", "master_enemy_abp"],
    ),
    (
        "Tokyo Death Metro",
        "Raiding ranks and payouts, the players you raid, and team wars.",
        ["master_tdm_rank", "master_dummy", "master_war_reward", "master_fort_whistle",
         "master_fort_break_bonus_rate"],
    ),
    (
        "Game rules",
        "Single settings the game reads by name: fall damage, how often floor "
        "shops appear, stat caps, hazards.",
        ["master_const_int", "master_const_float", "master_param_float"],
    ),
    (
        "Events & seasons",
        "Timed events and the seasons seasonal mushrooms follow. Every one in "
        "the stock game has already run out.",
        ["master_event_schedule", "master_mushroom_odds"],
    ),
]

# Columns whose value is a comma-separated LIST, not a single number. A spin box
# would quietly destroy these - "1,2,3" is three open decal slots, and typing 9
# in its place opens one slot, not nine. The editor shows them as plain text.
LIST_COLUMNS: set[tuple[str, str]] = {
    ("master_body_detail", "skill_slots"),
    ("master_body_detail", "abduct_bns_bag"),
    ("master_tdm_rank", "win_bns_bag"),
    ("master_tdm_rank", "def_bns_bag"),
    ("master_tdm_rank", "weekly_bns_bags"),
    ("master_area_setting", "conds"),
    ("master_area_setting", "replace_units"),
    ("master_area_connect_node", "flagofsxs"),
    ("master_area_connect_node_TEST", "flagofsxs"),
    ("master_area_connect_node_repeat_straight", "areaids"),
}

# Columns holding a date or time. The two kinds are stored differently, and a
# value in the wrong shape is silently ignored by the game rather than refused:
#   "epoch" - a whole number of seconds since 1970 (UTC). 0 or -1 means "none".
#   "text"  - '2026-08-01 00:00:00'.
# The builder shows both as dates and takes a date typed as 2027-01-31.
DATE_COLUMNS: dict[tuple[str, str], str] = {
    ("master_event_schedule", "start"): "epoch",
    ("master_event_schedule", "end"): "epoch",
    ("master_mushroom_odds", "inspires"): "epoch",
    ("master_mushroom_odds", "expires"): "epoch",
    ("master_quest", "start_date"): "epoch",
    ("master_quest", "end_date"): "epoch",
    ("master_skill", "start"): "epoch",
    ("master_skill", "end"): "epoch",
    ("master_skillgacha", "inspires"): "epoch",
    ("master_skillgacha", "expires"): "epoch",
    ("master_automaticshop_schedule", "expire"): "text",
}

# The floors are stored twice. master_floor holds every column, and five
# master_tmpfloor_ tables hold the same values split up (checked column by
# column - one column, clnum, differs). The game's code names all six and which
# copy it reads is not recorded, so an edit to one is made to the others too,
# wherever they share the column and held the same value before.
MIRRORED: list[list[str]] = [
    ["master_floor", "master_tmpfloor_stage", "master_tmpfloor_beast",
     "master_tmpfloor_gimic", "master_tmpfloor_item", "master_tmpfloor_mushroom"],
]


def mirrors_of(table: str) -> list[str]:
    """The other copies of a table that is stored more than once."""
    for family in MIRRORED:
        if table in family:
            return [t for t in family if t != table]
    return []


# Rows the Steam game never loads, with the words to put beside them. An edit to
# one of these is written, applied, and has no effect whatsoever - which is the
# worst kind of mod to debug, so the row says so up front.
SKIPPED_ROWS: dict[str, tuple[str, str]] = {
    # 39 decals are PlayStation-only. The Steam build skips any skill row with
    # platform 1 and no_steam 0 when it loads skills. A copy of one needs
    # platform 0 and a non-zero no_steam, or it silently does nothing.
    "master_skill": ("platform = 1 AND no_steam = 0", "PS4 only - skipped on Steam"),
}

# The vending machine's lists. MON to SUN are NOT days of the week: they are
# seven Bloodnium exchange lists, and master_automaticshop_schedule moves to the
# next one each month. The game's own script sorts goods into its three tabs by
# currency_type, which is how these were confirmed.
LINEUP_NAMES: dict[str, str] = {
    "COMMON": "Kill Coin shop",
    "RE": "Recycle-point exchange",
    **{code: f"Bloodnium exchange - monthly list {code}"
       for code in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")},
}

# What a lineup row's currency_type means, and the master_item column that holds
# that price. 3 and 4 are tested by name in the vending machine's script
# (exchange tab = 3, Bloodnium tab = 4); 0 is the only value on the Kill Coin
# list, whose items carry only a Kill Coin price. 1 and 2 are never used and are
# left out rather than guessed.
CURRENCY_TYPES: dict[int, tuple[str, str]] = {
    0: ("Kill Coins", "buy_money"),
    3: ("recycle points", "buy_recycle_point"),
    4: ("Bloodnium", "buy_bloodnium"),
}

# Which schedule columns name a list, and the pair that says how many goods from
# it the machine offers at once.
SCHEDULE_SLOTS: dict[str, tuple[str, str]] = {
    "common_lineup_id": ("purchase_goods_min", "purchase_goods_max"),
    "exchange_lineup_id": ("exchange_goods_min", "exchange_goods_max"),
    "bloodnium_exchange_lineup_id": ("bloodnium_exchange_goods_min",
                                     "bloodnium_exchange_goods_max"),
}

# "Good to know" lines shown above a table. Only things checked against the
# database, the game's own scripts, or a test in game - and where a line is an
# inference, it says so.
TIPS: dict[str, list[str]] = {
    "master_shop_product_price": [
        "The Mushroom Club decal draw is PRD_SKILL_GACHA (50,000 KC). Revives "
        "are PRD_CONTINUE and PRD_CONTINUE_1 to _6. PRD_CONTINUE_6 is also "
        "50,000, so pick rows by name, never by price.",
        "Nothing here costs more than 200,000 in the stock game. A player set the "
        "draw to 1,000,000 and the game crashed.",
    ],
    "master_item": [
        "What the vending machine charges is the item's own price here - Kill "
        "Coins, recycle points or Bloodnium, depending on the list it is on. "
        "Changing it changes it everywhere the item is sold.",
        "Blueprints (ITMP_...) have no names of their own; each is listed as the "
        "gear it makes.",
    ],
    "master_safe_level": [
        "The bank tops out at 2,560,000 at level 99 in the stock game.",
    ],
    "master_part": [
        "Haters carry the same gear, so stat and durability changes reach them too.",
    ],
    "master_body_detail": [
        "skill_slots is a list: \"1,2,3\" is three decal slots, not the number three.",
        "shop_open_floor is the floor that puts a grade in the shop; 999 means "
        "quest-only (grade 6).",
        "Grades past 6 and levels past 45 do work - another modder has added a "
        "grade 10 - but a new grade needs rows in all four fighter tables: this "
        "one, level EXP, stats by level and uncapping costs. Copy a row to start one.",
        "No fighter costs more than 100,000 in the stock game.",
    ],
    "master_skill": [
        "The price here is for buying that one decal. It is not the cost of a "
        "draw - that is PRD_SKILL_GACHA under Prices & money.",
        "#0 to #5 in a decal's description are filled from val0 to val5, so for "
        "those decals changing a value updates the text as well.",
        "Rows marked \"PS4 only\" are skipped by the Steam game. Editing one does "
        "nothing; a copy needs platform 0 and a non-zero no_steam.",
        "Haters use decals too, so a stronger decal is stronger for them as well.",
    ],
    "master_skillgacha_odds": [
        "The odds are weights, not percentages, and in the stock game they come "
        "only from star rating: 74, 72, 40, 4 and 2 for one to five stars.",
    ],
    "master_skillgacha": [
        "The draw's price is not here. product_id points at PRD_SKILL_GACHA, and "
        "that row under Prices & money holds it.",
    ],
    "master_mushroom_gen_odds": [
        "These are the same sets as mushroom spawns, one copy per season. Which "
        "season is in force is set under Events & seasons.",
    ],
    "master_floor_drop_gen": [
        "freq is a weight, not a percentage: a row's share of everything that "
        "source can drop on that floor.",
        "grp is an item group - see the grp column under Prices & money > Items. "
        "Groups 172 to 222 are the faction metals, Tuber metals and Death 'Roids.",
        "Death 'Roids never drop on 1F to 50F in the stock game; the Bloodnium "
        "exchange sells them.",
    ],
    "master_tmpfloor_item": [
        "Floors are stored twice - here and in the full floor table. Which copy "
        "the game reads is not recorded, so the builder changes both together.",
        "itemgenid picks a set from \"Which items are found where\"; itemmin and "
        "itemmax are how many pickups a layout gets.",
    ],
    "master_floor": [
        "Floors are stored twice - here and in five split-up tables. Which copy "
        "the game reads is not recorded, so the builder changes both together.",
    ],
    "master_automaticshop_lineup": [
        "MON to SUN are not days of the week. They are seven Bloodnium exchange "
        "lists, and the schedule moves to the next one each month. COMMON is the "
        "Kill Coin shop and RE the recycle-point exchange.",
        "The machine charges the item's own price, in the currency of the list.",
        "What is on offer is stored in your save when the machine restocks, so a "
        "change here shows at the next restock, not straight away.",
        "The stock schedule ran out on 1 August 2026. Use \"Keep the machine "
        "rotating\" so it has months left to restock from.",
    ],
    "master_automaticshop_schedule": [
        "One row per month: which lists the machine draws from until the date in "
        "expire. The dates are text (2026-08-01 00:00:00), not numbers.",
        "The last stock row expired on 1 August 2026.",
        "The min and max are how many goods from a list are offered at once. A "
        "list with more rows than the max has some left out (inferred - every "
        "stock row sets min and max to the list's size).",
        "Not yet tested in game: whether a save already past the last month picks "
        "up newly added months by itself.",
    ],
    "master_shop_appearance": [
        "The community \"Shop Always Appears\" mod sets every rate here to 100, "
        "plus SHOP_APPEARANCE_TIME to 0 and SHOP_INCIDANCE_INCREMENT to 100 "
        "under Game rules.",
    ],
    "master_const_int": [
        "FALL_DMG_BASE (150) and FALL_DMG_INC (50) set fall damage; both at 0 "
        "is proven in game to remove it.",
        "SHOP_APPEARANCE_TIME, SHOP_INCIDANCE_INCREMENT and SHOP_MAX_INCIDANCE "
        "decide how often floor shops turn up.",
        "HEAVEN_NEO_ROUTE_OPEN_NUM (4) caps how many Tengoku routes the 51F menu "
        "offers. The menu only knows five entries, so raising it does not add one.",
    ],
    "master_quest": [
        "A quest's rule and its wording are stored apart: raising a target under "
        "\"The number a quest enforces\" does not change the text the game shows.",
        "Every dated quest in the stock game has run out.",
    ],
    "master_reward": [
        "No reward in the stock game hands out Death Metals - there is no reward "
        "type for them. Whether the game would accept one is untested.",
    ],
    "master_login_bonus": [
        "The dated rows stop at 29 April 2026. Past that, only the run-of-days "
        "rows are left unless a mod adds more dates.",
    ],
    "master_event_schedule": [
        "start and end are shown as dates (UTC). Type a date like 2027-01-31 to "
        "change one.",
        "Every event in the stock game has already ended.",
        "FORT_SEASON is a run of 33 back-to-back seasons - extend the last one "
        "rather than making them all live at once.",
        "EVENT_SEASON_ rows are the mushroom seasons. A season needs its row here "
        "and the newest row of the matching set in \"Which seasonal mushroom "
        "odds are in force\" moved together - the way S3er0i9ng's calendar does it.",
    ],
    "master_mushroom_odds": [
        "inspires and expires are shown as dates (UTC). A seasonal set only counts "
        "between the two.",
        "Move the matching EVENT_SEASON_ row under Events & seasons at the same time.",
    ],
}

# The currencies worth gathering into a view of their own. The unit strings are
# the ones already written against columns in explain_data, so these views cost
# nothing to build - they are a filter, not new knowledge.
CURRENCIES: list[tuple[str, str, str]] = [
    ("Kill Coins", "KC", "Every price, payout and cap measured in Kill Coins."),
    ("SPLithium", "SPLithium", "Every price, payout and cap measured in SPLithium."),
    ("Bloodnium", "Bloodnium", "Everything priced or paid in Bloodnium."),
    ("Death Metals", "Death Metals",
     "Everything priced in Death Metals. No reward in the stock game hands them "
     "out, so the useful edit is making the things that cost them cheaper, or free."),
]

FIND_BY_CURRENCY = "Find a price"
FIND_BY_CURRENCY_NOTE = "Every column measured in one currency, across all tables."

EVERYTHING_ELSE = "Everything else"
EVERYTHING_ELSE_NOTE = (
    "The rest of the database: how floors join up, where each thing is placed, "
    "the game's own text, and the parts nobody has needed to change yet. All of "
    "it is described, but none of it is a beginner's first mod."
)


def grouped_tables() -> set[str]:
    """Every table that appears under a named heading."""
    return {table for _, _, tables in GROUPS for table in tables}


def group_of(table: str) -> str:
    for name, _, tables in GROUPS:
        if table in tables:
            return name
    return EVERYTHING_ELSE
