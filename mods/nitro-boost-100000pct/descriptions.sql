-- ============================================================
-- Nitro Boost - text patch
-- LET IT DIE - masters.db
--
-- Updates the player-facing description text for the
-- Nitro Boost (EXPUP_02) and EXPUP_01 skills so it reads
-- "100,000%" instead of "40%" / "10%".
--
-- Order does not matter: these are independent UPDATEs and do
-- not touch master_skill.val0, which nitro-boost-100000pct owns.
--
-- The x'0a20' on the end of each string is the newline + space
-- the game stores at the end of every skill description. Keep it.
--
-- No BEGIN/COMMIT here: the mod manager already runs every enabled
-- mod inside one transaction, so wrapping this in another would
-- either error or break that atomicity. (If you run this file by
-- hand with `sqlite3 masters.db < descriptions.sql`, add them
-- back, or just accept per-statement autocommit.)
-- ============================================================

-- ============= EXPUP_02 (Nitro Boost): 40% -> 100,000% =============
UPDATE master_text SET txt = 'Increases EXP gained by 100,000%.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'int';

UPDATE master_text SET txt = 'Erhöht erhaltene EP um 100.000 %.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'deu';

UPDATE master_text SET txt = 'Aumenta un 100.000% la EXP ganada.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'esn';

UPDATE master_text SET txt = 'Augmente le taux d''EXP gagnée' || x'0a' || 'de 100.000 %.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'fra';

UPDATE master_text SET txt = 'Aumenta del 100.000% l''ESP ottenuta.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'ita';

UPDATE master_text SET txt = 'Aumenta o ganho de EXP em 100.000%.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'ptb';

-- ============= EXPUP_01 (Turbo-charged Engine): 10% -> 100,000% =============
UPDATE master_text SET txt = 'Increases EXP gained by 100,000%.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_01' AND lang = 'int';

UPDATE master_text SET txt = 'Erhöht erhaltene EP um 100.000 %.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_01' AND lang = 'deu';

UPDATE master_text SET txt = 'Aumenta un 100.000% la EXP ganada.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_01' AND lang = 'esn';

UPDATE master_text SET txt = 'Augmente le taux d''EXP gagnée' || x'0a' || 'de 100.000 %.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_01' AND lang = 'fra';

UPDATE master_text SET txt = 'Aumenta del 100.000% l''ESP ottenuta.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_01' AND lang = 'ita';

UPDATE master_text SET txt = 'Aumenta o ganho de EXP em 100.000%.' || x'0a20'
  WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_01' AND lang = 'ptb';

-- ============================================================
-- MISSING: jpn, chn, kan, kor
--
-- The original patch covered these four as well, but the text was
-- corrupted in transfer before it reached this file - the UTF-8
-- continuation bytes were lost, so the characters could not be
-- recovered. Rather than guess at Japanese, Chinese and Korean game
-- text, they are left out: those four languages keep the stock
-- "40%" / "10%" wording. Everything else - including the actual EXP
-- multiplier - works exactly the same.
--
-- To add them back, paste the six statements below and fill in the
-- text from the untouched original file, then reload the mod (F5).
--
-- UPDATE master_text SET txt = '...' || x'0a20'
--   WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'jpn';
-- UPDATE master_text SET txt = '...' || x'0a20'
--   WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'chn';
-- UPDATE master_text SET txt = '...' || x'0a20'
--   WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'kan';
-- UPDATE master_text SET txt = '...' || x'0a20'
--   WHERE sct = 'SKILL_DESCRIPTION' AND id = 'TXT_SKL_EXPUP_02' AND lang = 'kor';
-- ... and the same four for TXT_SKL_EXPUP_01.
--
-- Save the file as UTF-8. The manager reads it as UTF-8 (BOM tolerated).
-- ============================================================

-- ============= VERIFICATION (run separately if you want) =============
-- SELECT sct, id, lang, txt FROM master_text
--   WHERE id IN ('TXT_SKL_EXPUP_01','TXT_SKL_EXPUP_02')
--     AND sct = 'SKILL_DESCRIPTION'
--   ORDER BY id, lang;
--
-- Expected: every row reads "100,000%" instead of "10%" or "40%".
