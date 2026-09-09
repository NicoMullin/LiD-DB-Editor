-- Any SQL SQLite accepts. Runs in the same transaction as the rest of the mod,
-- so if anything here fails, nothing from this mod is applied.
--
-- This example is a no-op you can safely delete.
UPDATE master_skill SET buy_money = buy_money WHERE 0;
