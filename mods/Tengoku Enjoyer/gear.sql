-- Gear values from before the offline update, as ZeroUnderOne put them back.
-- Tengoku Enjoyer by ZeroUnderOne, included with permission. Recorded by tools/build_tengoku_enjoyer.py - do not edit by hand.

-- master_atk_scale: 2 updated, 0 added, 0 removed
UPDATE "master_atk_scale" SET "scale" = 200 WHERE "id" = 'ANI_CH_ATK_WP56_FF_Fire_03';
UPDATE "master_atk_scale" SET "scale" = 215 WHERE "id" = 'ANI_CH_ATK_WP56_Fire_03';

-- master_const_int: 2 updated, 0 added, 0 removed
UPDATE "master_const_int" SET "value" = 3 WHERE "id" = 'REDNAPALM_BULLET_CONSUMP_KNIFE';
UPDATE "master_const_int" SET "value" = 9 WHERE "id" = 'REDNAPALM_BULLET_DUR_DOWN_KNIFE';

-- master_part: 1 updated, 0 added, 0 removed
UPDATE "master_part" SET "atk_c" = 1.03 WHERE "id" = 'PT_ARM_WP024_005';

-- master_sklmv: 2 updated, 0 added, 0 removed
UPDATE "master_sklmv" SET "gauge_cost" = 1, "atk_rate" = 1000 WHERE "id" = 'SKL_31_0';
UPDATE "master_sklmv" SET "gauge_cost" = 1 WHERE "id" = 'SKL_36_0';
