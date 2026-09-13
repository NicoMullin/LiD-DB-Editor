"""Which tables the mod builder puts in front of people, and under what heading.

The database has 221 tables. Most of them wire the game together - which room
connects to which, what a boss's AI slot 14 holds - and someone who wants to
make the revive cost 1 Kill Coin has no use for any of it.

So the builder leads with the parts people actually ask about. Everything else
is still reachable under "Everything else", because the descriptions exist and
walling it off would help nobody, but it is not the first thing you see.

Order matters here: it is the order the tabs appear in.
"""

from __future__ import annotations

# A group is a heading and the tables under it, most useful first.
GROUPS: list[tuple[str, str, list[str]]] = [
    (
        "Weapons & Armour",
        "What gear costs to craft and upgrade, and what it does.",
        ["master_part_research", "master_part", "master_equip_rank_point", "master_ptarm"],
    ),
    (
        "Fighters",
        "What each fighter type and grade costs, how far it levels, and its stats.",
        ["master_body_detail", "master_bodylvl_exp", "master_bodylvl_status_value",
         "master_freezer", "master_prison"],
    ),
    (
        "Items",
        "Prices for everything that is not a weapon or armour piece.",
        ["master_item"],
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
        ["master_beast", "master_beast_param_int", "master_beast_gen", "master_beast_efc"],
    ),
    (
        "Decals",
        "Skill Decals, what they cost, and the odds of drawing each one.",
        ["master_skill", "master_skillgacha_odds", "master_skill_group",
         "master_skill_open_mushroom"],
    ),
    (
        "Vending machine",
        "What the machine sells, for how much, and when shops appear.",
        ["master_automaticshop_lineup", "master_shop_product_price",
         "master_shop_appearance", "master_automaticshop_schedule"],
    ),
    (
        "Quests",
        "What quests ask for and what they pay.",
        ["master_quest", "master_quest_param_int", "master_quest_type"],
    ),
    (
        "Rewards",
        "Quest rewards, login bonuses, Mystery Bags and Death Boxes.",
        ["master_reward", "master_login_bonus", "master_mysterybag_content_gen_odds",
         "master_deathbox_content_gen", "master_deathbox_gen", "master_stamp_bonus"],
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

# The currencies worth gathering into a view of their own. The unit strings are
# the ones already written against columns in explain_data, so these views cost
# nothing to build - they are a filter, not new knowledge.
CURRENCIES: list[tuple[str, str, str]] = [
    ("Kill Coins", "KC", "Every price, payout and cap measured in Kill Coins."),
    ("SPLithium", "SPLithium", "Every price, payout and cap measured in SPLithium."),
    ("Death Metals", "Death Metals",
     "Everything priced in Death Metals. Nothing in the database hands them out - "
     "no reward type grants them - so the useful edit is making the things that "
     "cost them cheaper, or free."),
]

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
