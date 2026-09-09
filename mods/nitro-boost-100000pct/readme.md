# Nitro Boost EXP +100,000%

Both Nitro Boost (EXPUP_02) and EXPUP_01 give 100,000% EXP instead of 40% / 10%.

Nitro Boost (`SKL_EXPUP_02`) and its lesser sibling (`SKL_EXPUP_01`)
grant 100,000% EXP instead of 40% and 10%.

Writes only `master_skill.val0`, so it leaves skill costs and everything
else in `master_skill` alone - any mod that edits a different column of that
table stacks with it without conflicting.
