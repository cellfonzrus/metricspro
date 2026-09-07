-- 988_envelope_outstanding_alert_days.sql — per-tenant grace periods for the envelope cash alarm
--
-- OWNER 2026-09-07, verbatim: *"We need to spend more time to fix the envelope checking issues. This
-- the forth month I have no control on envelopes."*
--
-- ── WHAT THE ALARM IS, AND WHY IT DID NOT EXIST ───────────────────────────────────────────────────
-- Live evidence, house org, since 2026-05-01: 1,666 envelopes declared worth $595,470.29; **1,474 of
-- them ($540,344.38) had NO pickup record at all**, and 183 of the 192 that were collected carried no
-- disposition. Nine envelopes reached "deposited" in four months.
--
-- None of that was a missing report — /closing/envelope-report, /closing/pickup and the
-- deposit-accountability board all show it ON DEMAND. What did not exist, among the 49 registered
-- attention providers, was anything that says it WITHOUT BEING ASKED. Every other blind spot in this
-- platform (overdue imports, unmapped stores, stale closings, expiring documents) pushes itself into
-- the login popup. Outstanding cash did not. The new provider
-- `closing_envelope_outstanding` (backend/app/modules/closing/attention_providers.py) closes that,
-- and it is registered cost="cheap" ON PURPOSE: its neighbour `closing_stale_stores` has been correct
-- and enabled the whole time but is cost="heavy", so it only runs under a deep scan, and the only
-- automatic deep run is the daily control-box check — which had never run. A check that is right and
-- never runs is worth nothing.
--
-- ── WHAT THIS MIGRATION ADDS — AND WHY IT IS OPTIONAL ─────────────────────────────────────────────
-- Two per-tenant grace periods, the RULE TWO config for thresholds the provider otherwise takes from
-- house defaults in code (2 days each). **The provider already works without this migration**: the
-- config read is wrapped, and a missing column falls back to the default rather than disabling the
-- check. Run this when you want a tenant to differ — e.g. a market whose DM collects twice a week.
--
--   envelope_uncollected_alert_days  days an envelope may sit DECLARED-BUT-NOT-COLLECTED before it
--                                    is reported. 0 disables that half.
--   envelope_undisposed_alert_days   days a COLLECTED envelope may carry no disposition (no deposit,
--                                    no hand-off) before it is reported. 0 disables that half.
--
-- Deliberately NOT one shared column: "nobody has picked it up" and "a DM took it and never said
-- where it went" are different failures with different owners and different urgencies, and an org
-- that wants to tune one should not be forced to move the other.
--
-- A NULL means "use the house default", exactly like `closing_stale_alert_days` (mig 505) beside it.
-- 0 means the tenant deliberately switched that half off — a read FAILURE never resolves to 0, so an
-- unreadable config table can never silently switch off a money alarm (harness §E3).
--
-- ── MONEY ─────────────────────────────────────────────────────────────────────────────────────────
-- None. This adds two nullable integer columns used only to decide WHEN to show a warning. No P&L
-- line, payout, accrual, commission figure or envelope amount reads them, and the provider itself is
-- read-only — it nets EEP through `closing/envelope.py`, the same helper the Cash Pickup screen uses,
-- rather than deriving envelope cash a second time.
--
-- Additive + idempotent. Run in the Supabase SQL editor.

ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS envelope_uncollected_alert_days INTEGER,
  ADD COLUMN IF NOT EXISTS envelope_undisposed_alert_days  INTEGER;

COMMENT ON COLUMN storeops.tenants.envelope_uncollected_alert_days IS
  'Days an envelope may sit declared-but-not-collected before the closing_envelope_outstanding '
  'attention provider reports it. NULL = house default (2). 0 = this tenant deliberately disabled '
  'that half. A config READ FAILURE falls back to the default, never to 0.';
COMMENT ON COLUMN storeops.tenants.envelope_undisposed_alert_days IS
  'Days a COLLECTED envelope may carry no disposition (no deposit, no hand-off) before the '
  'closing_envelope_outstanding attention provider reports it. NULL = house default (2). 0 = '
  'deliberately disabled. A read failure falls back to the default, never to 0.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 988 — per-tenant grace periods for the envelope cash alarm (the alarm itself works without this)' AS status;

-- Verify: the attention popup (or GET /core/attention) should carry
--   closing_envelope_uncollected  — envelopes declared but never collected
--   closing_envelope_undisposed   — collected, but no deposit/hand-off recorded
-- On the house org at the time of writing that is 866 envelopes / $372,478.36 and
-- 29 envelopes / $17,497.00 respectively, over a 60-day lookback.
--
-- REVERT:
--   ALTER TABLE storeops.tenants
--     DROP COLUMN IF EXISTS envelope_uncollected_alert_days,
--     DROP COLUMN IF EXISTS envelope_undisposed_alert_days;
--   (The provider keeps working on its house defaults — the read is wrapped.)
