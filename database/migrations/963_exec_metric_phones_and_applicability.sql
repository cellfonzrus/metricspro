-- 963_exec_metric_phones_and_applicability.sql — the two silent-zero columns mig 962's detector found
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────────
-- The mig-962 coverage detector, on its first run against live data, flagged TWO more Executive-MTD
-- columns reading 0 for the house org for the same reason "Bill Payment Qty" did — a definition
-- describing another tenant's POS vocabulary. The owner answered both on 2026-09-04:
--
--   "tablet is not a phone but counts towards total activation,
--    activation fee with boost is called device set up fee which is accounted for"
--
-- ── 1. `phones` — a real defect, now fixed ────────────────────────────────────────────────────────
-- Stored tokens are category ['cellphone', 'kittedbranded'] (the other tenant's System-Category
-- spelling). The house org's handset lines carry brand CATEGORIES under handset DEPARTMENTS, so the
-- category tokens matched nothing and "Total Phones" read 0. Live August 2026 departments:
--
--     'iphone - xp'   454 lines  $53,559.97   (apple 450, '15 - 128gb' 4)
--     'android - xp'  738 lines  $20,447.37   (samsung 478, motorola 252, tcl 4, quality one 3, google 1)
--     'tablet - xp'    47 lines   $3,045.13   ← EXCLUDED per the owner: a tablet is not a phone
--     'byod'          369 lines               ← EXCLUDED: 357 of them are accessories, not handsets
--
-- Seeded rule: department ['iphone - xp', 'android - xp'] → 1,192 lines / $74,007.34 for August 2026,
-- up from 0. Tablets keep their own Tablet column and, as the owner notes, still count toward Total
-- Activation — which they already do and this migration does not touch: `_row()` folds tablet into
-- `d['activation']` before `ta` is summed, and only the DISPLAYED Activation column subtracts it out
-- (`_pure_new`). No activation number changes here.
--
-- ── 2. `activation_fee` — NOT a defect; a bucket this business does not have ───────────────────────
-- Stored token is product_desc ~ 'access charge', which matches nothing here. The owner confirms the
-- equivalent charge on this carrier is the DEVICE SET-UP FEE, which is already counted in its own
-- dedicated column (mig 263: department 'dev. charges or fees' / category 'device setup charge' —
-- 1,955 lines / $55,378.92 in August). Mapping activation_fee onto those lines would DOUBLE-COUNT the
-- same money in two columns, so the rule is deliberately left alone and the bucket is marked
-- NOT APPLICABLE instead: its 0 is the correct answer.
--
-- That is what the new `applicable` flag is for. Without it the coverage banner would report this
-- correct 0 as a defect every single month, and a warning that is always on is a warning nobody
-- reads. The flag silences the BANNER only — the bucket is still classified and counted, so if such
-- a line ever does appear it is counted and visible. A flag that hid real money would be worse than
-- the banner it silences.
--
-- ── MONEY ─────────────────────────────────────────────────────────────────────────────────────────
-- None. Both columns are Executive-MTD display metrics. `phones` (total_phones) and `activation_fee`
-- feed no P&L line, payout, accrual, commission or target: unlike `bill_payment` they are not read by
-- /metric-recon or by the mig-944 3-way recon either. This changes two display columns from a wrong 0
-- to the right number, and silences one false alarm.
--
-- Other tenants are untouched: the house own-rows are pinned by org, and the carrier presets apply
-- only to orgs whose commcalc.carrier resolves to the same code.
--
-- Additive + idempotent. Requires mig 962 (the `carrier` column + preset resolution).

-- the applicability flag ---------------------------------------------------------------------------
ALTER TABLE commcalc.exec_metric_config
  ADD COLUMN IF NOT EXISTS applicable BOOLEAN DEFAULT true;
COMMENT ON COLUMN commcalc.exec_metric_config.applicable IS
  'true (default, and NULL) = normal. false = this tenant''s business has no such line, so the '
  'column''s 0 is the CORRECT answer and exec_metric_defs.bucket_coverage must not report it as a '
  'gap (it is listed under `not_applicable` instead). Silences the banner ONLY — the bucket is still '
  'classified and counted, so a line that does appear is never hidden.';

-- 1. phones: the boost PRESET (so the next tenant on this carrier is right on day one) --------------
INSERT INTO commcalc.exec_metric_config (org_id, bucket, rules, basis, carrier, applicable)
VALUES ('00000000-0000-0000-0000-000000000001', 'phones',
        '{"department": ["iphone - xp", "android - xp"]}'::jsonb, 'count', 'boost', true)
ON CONFLICT (org_id, bucket, carrier) DO UPDATE
  SET rules = EXCLUDED.rules, basis = EXCLUDED.basis, applicable = EXCLUDED.applicable, updated_at = now();

-- 1b. phones: the house org's OWN definition (an own-row exists and wins over the preset) -----------
INSERT INTO commcalc.exec_metric_config (org_id, bucket, rules, basis, carrier, applicable)
VALUES ('00000000-0000-0000-0000-000000000001', 'phones',
        '{"department": ["iphone - xp", "android - xp"]}'::jsonb, 'count', NULL, true)
ON CONFLICT (org_id, bucket, carrier) DO UPDATE
  SET rules = EXCLUDED.rules, basis = EXCLUDED.basis, applicable = EXCLUDED.applicable, updated_at = now();

-- 2. activation_fee: marked not-applicable, RULES DELIBERATELY UNCHANGED ---------------------------
--    (leaving the rule intact means that if such a line ever appears it is still counted; only the
--     false-alarm banner is suppressed.)
UPDATE commcalc.exec_metric_config
   SET applicable = false, updated_at = now()
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND bucket = 'activation_fee'
   AND carrier IS NULL;

INSERT INTO commcalc.exec_metric_config (org_id, bucket, rules, basis, carrier, applicable)
VALUES ('00000000-0000-0000-0000-000000000001', 'activation_fee',
        '{"product_desc_contains": ["access charge"]}'::jsonb, 'ext_price', 'boost', false)
ON CONFLICT (org_id, bucket, carrier) DO UPDATE
  SET applicable = EXCLUDED.applicable, updated_at = now();

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 963 — Total Phones vocabulary corrected; Activation Fee marked not-applicable' AS status;

-- Verify (GET /api/v1/commcalc/exec-mtd/August%202026):
--   by_location.total.total_phones  →  1192   (was 0)
--   metric_coverage.gaps            →  []     (was ['phones','activation_fee'])
--   metric_coverage.not_applicable  →  ['activation_fee']
--
-- REVERT:
--   UPDATE commcalc.exec_metric_config
--      SET rules = '{"category": ["cellphone", "kittedbranded"]}'::jsonb
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND bucket = 'phones' AND carrier IS NULL;
--   UPDATE commcalc.exec_metric_config SET applicable = true
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND bucket = 'activation_fee';
--   DELETE FROM commcalc.exec_metric_config
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND carrier = 'boost'
--      AND bucket IN ('phones', 'activation_fee');
--   ALTER TABLE commcalc.exec_metric_config DROP COLUMN IF EXISTS applicable;
--   (Reads are adaptive: a missing/NULL `applicable` resolves to true — pre-963 behavior exactly.)
