-- 1067_pickup_counted_by.sql — WHO counted the envelope, and WHEN (owner directive 2026-10-10)
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "if the cash is not opened in cash pickup or epay pick up it should [have] the
-- option to open on this module and it should also give the field to enter the cash pick[ed] by the
-- manag[e]ment or handed over to the manag[e]ment".
--
-- THE CLASS, NOT THE INSTANCE. The instance is "a sealed envelope cannot be opened from the deposit
-- accountability board". The class is that the pickup row records a COUNT with no COUNTER:
--   · mig 949 added `actual_picked_amount` — what was counted.
--   · mig 990 added `envelope_opened`      — that the seal was broken.
--   · neither recorded WHO made that count or WHEN.
-- That was tolerable while the only possible counter was the DM confirming the pickup, because
-- `picked_up_by` named them by implication. It stops being tolerable the moment a second person can
-- open the envelope later: management opening a sealed envelope at the accountability step is NOT
-- the DM who collected it, and writing their name into `picked_up_by` would destroy the collection
-- record to store the count record — one fact overwriting a different one.
--
-- It is the same gap the index already names on the management side (`envelope_count.counted_by`
-- writing the literal string "management", recording THAT management counted and never WHO). This
-- closes it on the pickup side, for every caller of the shared writer, rather than for the one
-- screen that surfaced it.
--
-- WHAT SHIPS HERE (additive, nullable, no backfill, no default):
--   1. commcalc.cash_pickup.actual_counted_by    / .actual_counted_at
--   2. commcalc.billpay_pickup.actual_counted_by / .actual_counted_at   (the mig-942 sibling
--      mirror: the confirm machinery is ONE parameterized implementation, so a divergent shape
--      here would immediately break the shared writer.)
--
-- ONE HOME FOR THE WRITE. Both columns are written only through
-- `closing/pickup_actual.count_patch` — the single function that turns an open-and-count statement
-- into stored columns — which `closing/router._confirm_pickup_impl` (the DM's count at pickup) and
-- `closing/router._open_count_impl` (the late open from the accountability board) both dereference.
-- `backend/harness_deposit_accountability.py` §J FAILS THE BUILD if either stops dereferencing it
-- or if a second copy of these column names appears in the router.
--
-- NEVER A TIMESTAMP WITHOUT A PERSON: `actual_counted_at` is only ever written alongside a counter
-- name, so a bare timestamp can never stand in for an identity.
--
-- MONEY: nothing here moves a booked number, for any org. These columns are never summed, never
-- relieve cash, and are not read by any P&L, balance-sheet or commission path. Whether the actual
-- count relieves the general cash movement remains the pre-existing, still-default-false
-- `cash_pickup_config.pickup_actual_relieves_cash` knob (mig 949) — untouched here.
--
-- ADAPTIVE BY CONSTRUCTION (the mig-201 product_mrc precedent both writers already follow): the
-- columns are written only when a counter is supplied, and the write RETRIES WITHOUT them if the
-- error names them, so an un-migrated database still records the count itself — only the counter's
-- name and time are lost, never the money figure.
--
-- Additive + idempotent. RLS: columns on existing open_all tables (mig 034/942) — unchanged.
-- Run in the Supabase SQL editor.

ALTER TABLE commcalc.cash_pickup
  ADD COLUMN IF NOT EXISTS actual_counted_by TEXT;
ALTER TABLE commcalc.cash_pickup
  ADD COLUMN IF NOT EXISTS actual_counted_at TIMESTAMPTZ;
COMMENT ON COLUMN commcalc.cash_pickup.actual_counted_by IS
  'WHO counted the cash in this envelope (owner 2026-10-10) — the DM at pickup, or whoever opened '
  'it later from the deposit-accountability board. Beside actual_picked_amount (mig 949, what was '
  'counted) and envelope_opened (mig 990, that the seal was broken). Deliberately NOT picked_up_by: '
  'that names who COLLECTED the cash, and a late counter must not overwrite it. NULL = the counter '
  'was not recorded, never a guess. Written only through pickup_actual.count_patch. See index 23p.';
COMMENT ON COLUMN commcalc.cash_pickup.actual_counted_at IS
  'WHEN the count in actual_picked_amount was made. Only ever written alongside actual_counted_by, '
  'so a timestamp can never stand in for a person. NULL = not recorded.';

ALTER TABLE commcalc.billpay_pickup
  ADD COLUMN IF NOT EXISTS actual_counted_by TEXT;
ALTER TABLE commcalc.billpay_pickup
  ADD COLUMN IF NOT EXISTS actual_counted_at TIMESTAMPTZ;
COMMENT ON COLUMN commcalc.billpay_pickup.actual_counted_by IS
  'Sibling mirror of cash_pickup.actual_counted_by (mig 942 shared confirm machinery): who counted '
  'this bill-payment envelope. NULL = not recorded.';
COMMENT ON COLUMN commcalc.billpay_pickup.actual_counted_at IS
  'Sibling mirror of cash_pickup.actual_counted_at. Only written alongside actual_counted_by.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1067 complete — actual_counted_by / actual_counted_at on cash_pickup + billpay_pickup' AS status;

-- REVERT:
--   ALTER TABLE commcalc.cash_pickup    DROP COLUMN IF EXISTS actual_counted_by;
--   ALTER TABLE commcalc.cash_pickup    DROP COLUMN IF EXISTS actual_counted_at;
--   ALTER TABLE commcalc.billpay_pickup DROP COLUMN IF EXISTS actual_counted_by;
--   ALTER TABLE commcalc.billpay_pickup DROP COLUMN IF EXISTS actual_counted_at;
--   (Safe at any time. Both writers include these keys only when a counter is supplied and retry
--    WITHOUT them when the error names them, so pickups and late counts keep recording either way;
--    the money figures — amount, actual_picked_amount — are untouched by this migration and survive
--    a revert intact. The board simply stops naming the counter.)
