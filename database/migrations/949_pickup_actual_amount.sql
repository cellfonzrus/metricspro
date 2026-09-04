-- 949_pickup_actual_amount.sql — Actual cash picked from the envelope (owner directive 2026-09-04)
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "for cash pick up, one more column is needed actual cash picked from envelope."
--
-- On /closing/pickup the DM confirming a pickup can now record the ACTUAL cash physically taken
-- out of the envelope, beside the declared/expected figure (cash_pickup.amount — the mig-034
-- snapshot of store_cash + epay_cash at confirm time). Variance (actual − declared) + the
-- short/over/match status reuse envelope_report.count_fields — the SAME truth table the mig-936
-- envelope report applies to MANAGEMENT's later count. This column is deliberately NOT
-- envelope_count.counted_amount: different actor (DM at pickup vs management at count), different
-- moment, different key (envelope_count keys on closing_row_id, which is replaced on re-sync;
-- cash_pickup keys the logical envelope and survives re-uploads), and the envelope-short
-- chargeback machinery keys off counted_amount — conflating the two would move money on the
-- wrong evidence. Same declared-vs-recorded pairing convention as the deposit step's
-- deposit_amount/declared_amount (mig 089/942).
--
-- WHAT SHIPS HERE (all additive):
--   1. commcalc.cash_pickup.actual_picked_amount — the DM's actual count at pickup time. NULL =
--      not recorded (old rows, or a confirm without the input) — never coerced to 0.
--   2. commcalc.billpay_pickup.actual_picked_amount — the sibling mirror (mig 942: "the same
--      process same wiring" — the confirm machinery is one parameterized implementation, so the
--      billpay side gets the column for free and stays shape-compatible).
--   3. commcalc.cash_pickup_config.pickup_actual_relieves_cash (BOOLEAN, default false) — THE
--      MONEY-FLOW KNOB (the mig-942 billpay_relieves_cash precedent). false (default, today's
--      behavior, byte-identical): the DECLARED snapshot (`amount`) relieves the general cash
--      movement (_cash_position_core → the mig-938 balance-sheet store-cash line, Cash Position,
--      Store Cash on Hand, the pickup page's by_store panel) exactly as before; the actual figure
--      is display + a short/over flag only. true: the recorded ACTUAL relieves the movement where
--      present (declared where none recorded). Flipping it MOVES the balance-sheet cash number,
--      so the seed below is commented out under the owner-approval convention.
--
-- MONEY: nothing here moves a booked number for any org. The knob defaults to false and no org
-- seed is applied; the actual_picked_amount columns start NULL everywhere.
-- Additive + idempotent. RLS: columns on existing open_all tables (mig 034/942) — unchanged.
-- Run in the Supabase SQL editor.

ALTER TABLE commcalc.cash_pickup
  ADD COLUMN IF NOT EXISTS actual_picked_amount NUMERIC;
COMMENT ON COLUMN commcalc.cash_pickup.actual_picked_amount IS
  'Actual cash the DM physically took from the envelope at pickup time (owner 2026-09-04). NULL '
  '= not recorded (never coerced to 0). Declared/expected stays `amount` (mig-034 snapshot); '
  'variance + short/over/match via envelope_report.count_fields (the mig-936 envelope-report '
  'truth table). Relieves the general cash movement ONLY under '
  'cash_pickup_config.pickup_actual_relieves_cash (default false = declared relieves, as always).';

ALTER TABLE commcalc.billpay_pickup
  ADD COLUMN IF NOT EXISTS actual_picked_amount NUMERIC;
COMMENT ON COLUMN commcalc.billpay_pickup.actual_picked_amount IS
  'Sibling mirror of cash_pickup.actual_picked_amount (mig 942 shared machinery): actual '
  'bill-pay cash the DM took from the envelope. NULL = not recorded. Folds into the general '
  'cash movement only when BOTH billpay_relieves_cash (mig 942) and '
  'pickup_actual_relieves_cash (mig 949) apply.';

ALTER TABLE commcalc.cash_pickup_config
  ADD COLUMN IF NOT EXISTS pickup_actual_relieves_cash BOOLEAN DEFAULT false;
COMMENT ON COLUMN commcalc.cash_pickup_config.pickup_actual_relieves_cash IS
  'false (default) = pickup outflows relieve the general cash movement at the DECLARED snapshot '
  '(cash_pickup.amount) — byte-identical to pre-949 behavior; the actual-picked figure is '
  'display + short/over flag only. true = the recorded ACTUAL (actual_picked_amount) relieves '
  'the movement where present, declared where none was recorded. Flipping this moves the '
  'balance-sheet store-cash line (mig 938) — owner approval required.';

-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- ⛔ ORG EXAMPLE — COMMENTED OUT (owner-gate convention, mig 622/933/938/939/942): flipping the
-- knob makes the ACTUAL figure relieve the BS cash line. Only apply with explicit owner approval.
--
-- INSERT INTO commcalc.cash_pickup_config (org_id, pickup_actual_relieves_cash)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', true)
-- ON CONFLICT (org_id) DO UPDATE
--   SET pickup_actual_relieves_cash = EXCLUDED.pickup_actual_relieves_cash, updated_at = now();
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 949 complete — actual_picked_amount on cash_pickup + billpay_pickup, pickup_actual_relieves_cash knob' AS status;

-- REVERT:
--   ALTER TABLE commcalc.cash_pickup DROP COLUMN IF EXISTS actual_picked_amount;
--   ALTER TABLE commcalc.billpay_pickup DROP COLUMN IF EXISTS actual_picked_amount;
--   ALTER TABLE commcalc.cash_pickup_config DROP COLUMN IF EXISTS pickup_actual_relieves_cash;
--   (All reads are adaptive: pickup_actual.actual_relieves_cash resolves to false on a missing
--    column — declared relieves the cash movement, byte-identical to pre-949; the confirm writer
--    only includes actual_picked_amount when the client sends it; the list/board endpoints read
--    the column via select("*") and degrade to "no actual recorded".)
