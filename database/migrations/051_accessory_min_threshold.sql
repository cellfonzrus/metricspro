-- 051_accessory_min_threshold.sql — add a "below minimum" accessory threshold.
-- Run in the Supabase SQL editor. Idempotent.
--
-- WHY: the Accessory Flags rule only flagged accessories sold ABOVE a max threshold (chargeback
-- risk). The user also wants to catch reps selling accessories BELOW an allowed minimum
-- (underselling / giving them away). This adds a second user-defined value; 0 = disabled (default),
-- so existing behavior is unchanged until a minimum is set.

ALTER TABLE commcalc.flag_rules
  ADD COLUMN IF NOT EXISTS accessory_min_threshold NUMERIC DEFAULT 0;   -- flag accessory sales with 0 < ext_price BELOW this; 0 = off

NOTIFY pgrst, 'reload schema';

SELECT 'flag_rules.accessory_min_threshold ready' AS status;
