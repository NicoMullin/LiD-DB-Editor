-- Template: the "no metadata" mod layout.
--
-- A folder containing a single .sql file is a valid mod. The manager derives
-- the name from the folder name, applies the whole file as one raw_sql_file
-- patch, and snapshots every table the file writes so Revert still works.
--
-- This folder starts with "_", so the manager skips it. Copy it, rename the
-- copy to something without the underscore, and put your own SQL here.
--
-- The statement below is a deliberate no-op so that a straight copy of this
-- folder cannot change anything. Delete it and write your own.
UPDATE master_shop_product_price SET price = price WHERE 0;

-- A real one would look like this:
--
--   UPDATE master_shop_product_price SET price = 1 WHERE price > 1;
--   UPDATE master_shop_product_price SET medal = 1 WHERE medal > 1;
--
-- Anything SQLite accepts works, and the whole file runs in one transaction:
-- if any statement fails, none of them are applied.
