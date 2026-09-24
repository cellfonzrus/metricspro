-- 1015_ma_leg_rate_schedule.sql
-- mod-commission · follows 1014 (`connector_registry`, #275). Additive + idempotent + safe to re-run.
-- ⚠ NOT APPLIED. Surfaced for owner approval (house rule: money-touching changes and migrations are
--   surfaced before applying). Nothing in this file moves a dollar; see §4 for exactly what it does
--   and does not do.
--
-- ═══ WHY (owner report 2026-09-21, verbatim) ═══════════════════════════════════════════════════
-- "something is still off the m2-m6 commission is 375% of the mrc considereing 75% each for 5
--  months the numbers still dont match … the m1 commssion is 98656 and m2-m12 is 92536 reacharch
--  where this is going worng and display each months commission in separate column so we see what
--  is going on"
--
-- The owner had to state the contract IN THE QUESTION — "75% each for 5 months" — because the
-- system has nowhere to hold it. Every per-leg rate check therefore happens on paper, by hand, once,
-- and is gone by the next period. That is the gap this column closes.
--
-- ═══ 1. WHAT WAS MEASURED (read-only, org 854f6d7b-…, all 8 periods Feb–Sep 2026) ══════════════
-- The contract SHAPE is confirmed in the data, against LIST MRC (= `mrc_net_discount` / 0.915; the
-- netted column already has the 8.5% bill-pay commission taken out and must never be the base):
--   • TRAILING legs pay exactly 75% of list. Modal amounts, whole feed:
--       41.25 on a $55 plan · 48.75 on $65 · 30.00 on $40 · 22.50 on $30 · 18.75 on $25.
--   • The SHEET's own first-month spiff is exactly 50% of list: August 2026, 1,183 rows with a list
--     MRC, median 0.500, mode 0.50 on 702 of them. Σ spiff_m1 = 23,271.90 on 1,248 activations.
--   • NOTHING is in M7..M12 in any period. The schedule does not run past M6.
--   • The CASH first-month rows are SHORT on both counts: August 508 rows / $6,049.96 against the
--     sheet's 1,163 rows / $23,271.90 that EARNED an M1. Same direction in June and July.
-- So: trailing legs at contract, M1 cash far below what the sheet says was earned. Holding the
-- schedule as data is what lets the report state that difference every period instead of never.
--
-- ═══ 2. RULE TWO ═══════════════════════════════════════════════════════════════════════════════
-- No rate, month count or plan price may live in a branch. 50%, 75%, "five months", 0.915 and "the
-- schedule ends at M6" are all TENANT FACTS. They live here, per org, with a HOUSE DEFAULT row on
-- 00000000-0000-0000-0000-000000000001 that tenant rows override — exactly like `report_pull_map`
-- (mig 207) and `pl_ma_month_spiff_source` (mig 314).
--
-- ═══ 3. THE COLUMNS ════════════════════════════════════════════════════════════════════════════
--   ma_leg_rate_schedule   jsonb  {"<rung>": <fraction of LIST MRC>} e.g. {"1":0.50,"2":0.75,…,"6":0.75}
--                                 NULL / '{}' (house default) = no schedule stated, and the report
--                                 says so rather than assuming one. A rung absent from the map is
--                                 reported as "no rate configured" — the same honest-absence posture
--                                 `ma_recon.NO_RULE_REASON` takes, never a guess.
--   ma_mrc_list_divisor    numeric the divisor that recovers LIST MRC from the netted column
--                                 (0.915 for this processor = 1 − the 8.5% bill-pay commission).
--                                 NULL = 1.0, i.e. "the stored column IS list".
--
-- Deliberately NOT stored here: a month COUNT. The schedule's length is however many rungs the map
-- has, and the ladder the report draws comes from the DATA (`commission_legs.months_present`), so a
-- rung that is paid but not in the schedule shows up as a rung with no configured rate — a finding —
-- rather than being invisible. That is the whole point of the per-month columns.
--
-- ═══ 3b. IT COEXISTS WITH MIGS 1013 AND 1014, STATED NOT INFERRED ══════════════════════════════
-- Mig `1013_pl_commission_source.sql` (#273) adds `pl_commission_source TEXT NOT NULL DEFAULT
-- 'feeds'` to this SAME table. This file adds two DIFFERENT, nullable columns with `ADD COLUMN IF
-- NOT EXISTS`, touches no existing column, sets no NOT NULL and no default, and its only INSERT is
-- an `ON CONFLICT DO NOTHING` on the house org row. The two are independent and may be applied in
-- either order; neither re-runs into the other.
-- Mig `1014_connector_registry.sql` (#275) creates `commcalc.connector_registry` — a DIFFERENT
-- table this file never touches. No interaction at all.
--
-- ⚠ THIS FILE WAS RENUMBERED TWICE, and the reason is worth recording rather than tidying away.
-- It was drafted as 1013 and collided with #273; renumbered to 1014 and collided with #275; it is
-- now 1015. Both collisions happened because it sat UNAPPLIED awaiting owner approval while other
-- migrations merged past it — which is the normal cost of surfacing a money-adjacent migration
-- instead of applying it, and is the right trade. A colliding number is how the wrong migration
-- gets run or skipped by hand, so the number is re-checked against `main` before every push.
--
-- ═══ 4. BLAST RADIUS ═══════════════════════════════════════════════════════════════════════════
-- ZERO until a row is written. No existing column changes, no existing default changes, nothing is
-- backfilled, no payout, booking, P&L line or GP figure reads these columns before the reader that
-- is added in the follow-up. Applying this file alone leaves every number on every report
-- byte-identical. It is surfaced separately from the code precisely so the owner can confirm the
-- 50% / 75% / 0.915 figures BEFORE any surface states them back to him as fact.
--
-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS ma_leg_rate_schedule;
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS ma_mrc_list_divisor;
--   DELETE FROM commcalc.commission_org_config
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND ma_leg_rate_schedule IS NOT NULL;   -- only if this file created the house row

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS ma_leg_rate_schedule jsonb,
  ADD COLUMN IF NOT EXISTS ma_mrc_list_divisor  numeric;

COMMENT ON COLUMN commcalc.commission_org_config.ma_leg_rate_schedule IS
  'Per-org month-of-life commission schedule: {"<rung>": <fraction of LIST MRC>}. A rung absent '
  'from the map has NO configured rate and is reported as such, never assumed. No month count is '
  'stored — the ladder the report draws comes from the data (commission_legs.months_present). '
  'House default NULL = no schedule stated. Owner report 2026-09-21; index SYSTEM_DATA_FLOW_INDEX §4a.2.';

COMMENT ON COLUMN commcalc.commission_org_config.ma_mrc_list_divisor IS
  'Divisor that recovers LIST MRC from the stored netted column (raw_ma_commission.mrc_net_discount '
  '= list x this). 0.915 for the Total/VidaPay processor: the 8.5% bill-pay commission is already '
  'netted out and deducting it twice understates every stated rate. NULL = 1.0 (the column IS list).';

-- HOUSE DEFAULT ROW — stated, not silent. NULL schedule means "this house has no opinion"; a tenant
-- states its own contract. Idempotent: never overwrites a value an org already set.
INSERT INTO commcalc.commission_org_config (org_id)
VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (org_id) DO NOTHING;
