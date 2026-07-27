-- 246_commission_installment_hardware_guard.sql — a DEVICE/PROMO PRICE can never be an MRC.
-- Additive + idempotent + safe to re-run.
--
-- OWNER BUG 2026-07-27 (verbatim): "Also tablets are currently being paid for the rebates it was the
-- case with the phones which we fixed earlier." Evidence (July 2026 M1 rows, luxelink):
--   357845420428952 / 357845420429083 / 357845420452713  Samsung Galaxy Tab A11+ 5G "TO - Promo
--   $279.99, …"  → M1 paid $14.00 on "MRC 279.99" = 5% of the DEVICE PROMO PRICE.
--
-- WHY MIG 233 DID NOT CATCH IT (root cause, verified in code): mig 233 bounded the bare-$ MRC prefill
-- to lines that "identify as a rate-plan line" — but a TABLET's DEVICE line identifies as one, because
-- its promo text contains the whole word "plan" ("Min $50 tablet plan w/6 months of service"). The
-- device line and the real rate-plan line then tied at rank 2, and the tie-break is ALPHABETICAL by
-- product description, so "Samsung Galaxy Tab …" beat "Total Wireless … Tablet 6-Month Plan $60" and
-- donated $279.99. WORDING alone cannot separate the two halves of an activation; STRUCTURE can.
--
-- THE FIX IS IN THE ENGINE and is ON BY DEFAULT: a line carrying a device IMEI (14-17 digits) is a
-- HARDWARE line. A hardware line is ranked BELOW every real rate-plan line, and it can never donate a $
-- that IS its own Ext/Unit price. Unresolvable → $0 + an `mrc_unresolved` warning (mig-233 semantics).
-- Also fixed here: a bare "<n> Month" TERM LENGTH ("6 Month Plan $60") no longer reads as a $6 monthly
-- charge, and when a promo line carries several $ amounts the PLAN-ADJACENT one wins over the first.
--
-- This migration only adds the two TENANT-EDITABLE knobs (RULE TWO):
--   installment_mrc_hardware_guard  TRUE (default) — apply the structural hardware guard.
--                                   FALSE          — pre-2026-07-27 ranking (escape hatch).
--   hardware_line_matcher           extra department/category values that are hardware for this tenant.
--                                   NULL = {} = the structural IMEI test only.
--
-- A tenant whose POS stamps Serial 1 on the AIRTIME line is protected automatically: a department or
-- category listed in that tenant's plan_line_matcher (mig 233) is never treated as hardware.
--
-- UNTIL THIS RUNS: the engine degrades to guard ON + empty sets, so the fix is live without it.
-- NOTHING here changes a stored number. Corrected amounts appear on the next POST /calculate.

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS installment_mrc_hardware_guard BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS hardware_line_matcher          JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.installment_mrc_hardware_guard IS
  'TRUE (default): a device line (IMEI / configured hardware department) can never donate its own price as a multi-month MRC. FALSE = pre-2026-07-27 ranking.';
COMMENT ON COLUMN commcalc.commission_org_config.hardware_line_matcher IS
  'Extra hardware department/category values: {"departments":[],"categories":[]}. NULL = structural IMEI test only.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 246 complete — commission_org_config.installment_mrc_hardware_guard + hardware_line_matcher '
       '(device/promo prices are structurally impossible as a multi-month MRC)' AS status;
