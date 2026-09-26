-- 1026_dlar_vintage_kpi_no_data.sql
-- Owner defect 2026-09-26 ("ElevateGo says Waleed meets 4/7, MetricsPro says 2/7") — index §19.28.
--
-- WRITTEN, NOT APPLIED. Numbered, idempotent, additive. BLOCK 1 is safe and money-neutral. BLOCK 2
-- MOVES PAST KPI SCORES and is therefore held for the owner's explicit sign-off — it is commented out,
-- with the measured before/after beside it. Do not run BLOCK 2 to "tidy up".
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════════
-- BLOCK 1 — AS OF WHEN WAS THIS TRUE (safe: additive, nullable, no row is changed)
-- ═══════════════════════════════════════════════════════════════════════════════════════════════════
-- THE DEFECT. Both Elevate Go reports are MONTH-TO-DATE for the portal's current period, and the sweep
-- files only the period the portal is currently serving. Once a month rolls over, that month's slice is
-- frozen at whatever day the last pull of it happened to be — forever. Measured live, house org:
--
--     period           raw_dlar_store written   raw_dlar_rep written    rep slice
--     August 2026      2026-09-02 (28 rows)     2026-08-24 (44 rows)    7 DAYS SHORT
--     June 2026        2026-07-02 (28 rows)     2026-06-29 (75 rows)    1 day short
--
-- The pay engine reads ATU / Protect / BYOD / Boost App from the REP grain and Family Plan / 3MR / AAL
-- from the STORE grain into ONE paid row, so for August it scored a rep on 24 days of one report and 31
-- of another. NEITHER TABLE HAD A COLUMN FOR AN AS-OF DATE. The only copy of it was a sentence in
-- `commcalc.dlar_sweep_config.last_detail`, overwritten on every run — and that sentence reported the
-- rows PULLED, so the 2026-09-02 run that refused to replace the rep table still read
-- "OK — 28 stores, 45 reps".
--
-- NOT BACK-FILLED ON PURPOSE. For a row written before this column existed the report's own date was
-- never recorded, and setting `as_of_date = created_at::date` would manufacture exactly the kind of fact
-- this change exists to remove. `dlar_sweep.vintage_of_row` instead treats `created_at` as an UPPER
-- BOUND and labels it `basis='write_date'`, so the answer reads "as of no later than 2026-08-24, and the
-- report's own date was not recorded" — which is the truth.
--
-- REVERT: ALTER TABLE commcalc.raw_dlar_rep   DROP COLUMN IF EXISTS as_of_date;
-- REVERT: ALTER TABLE commcalc.raw_dlar_store DROP COLUMN IF EXISTS as_of_date;

ALTER TABLE commcalc.raw_dlar_rep   ADD COLUMN IF NOT EXISTS as_of_date DATE;
ALTER TABLE commcalc.raw_dlar_store ADD COLUMN IF NOT EXISTS as_of_date DATE;

COMMENT ON COLUMN commcalc.raw_dlar_rep.as_of_date IS
  'The as-of date of the ADVOCATE (rep) report this row came from — its own import_date, not the store '
  'report''s. NULL = written before this column existed; created_at is then an upper bound only '
  '(dlar_sweep.vintage_of_row). Index §19.28.';
COMMENT ON COLUMN commcalc.raw_dlar_store.as_of_date IS
  'The as-of date of the DLAR (store) report this row came from — its own import_date. NULL = written '
  'before this column existed; created_at is then an upper bound only. Index §19.28.';

CREATE INDEX IF NOT EXISTS idx_raw_dlar_rep_asof
  ON commcalc.raw_dlar_rep (org_id, period, as_of_date);
CREATE INDEX IF NOT EXISTS idx_raw_dlar_store_asof
  ON commcalc.raw_dlar_store (org_id, period, as_of_date);
-- REVERT: DROP INDEX IF EXISTS commcalc.idx_raw_dlar_rep_asof;
-- REVERT: DROP INDEX IF EXISTS commcalc.idx_raw_dlar_store_asof;


-- ═══════════════════════════════════════════════════════════════════════════════════════════════════
-- BLOCK 2 — ⚠ MONEY: the 189 fabricated Boost App zeroes.  NOT RUN. OWNER SIGN-OFF REQUIRED.
-- ═══════════════════════════════════════════════════════════════════════════════════════════════════
-- THE DEFECT (fixed FORWARD in code; this block is about HISTORY only).
--     "boost_app_pct": (bounty / ga_prepaid * 100) if ga_prepaid > 0 else 0
-- `ga_prepaid = 0` is the portal not reporting a prepaid-activation count, not a rep with no
-- activations. Measured live 2026-09-26, read-only:
--
--     raw_dlar_rep rows                                       516
--     with ga_prepaid = 0, every one written a measured 0%     189
--       …of those, gross_adds > 0 (had activations)            139
--       …of those, boost_ready_bounty > 0 (SOLD the thing)     122
--       …of those, no activity at all                            7
--     July 2026 / August 2026 / September 2026: ALL 58 / 44 / 45 rep rows are in that set, so with
--     payout_config.tier_100_min_kpis = 7, tier 1.0 was UNREACHABLE for every house rep in three months.
--
-- WHAT THIS BLOCK WOULD DO: turn those fabricated zeroes into NULL, so `kpi_failing.score` reports them
-- as `no_data` and leaves them OUT of the met-count denominator. On its own that moves NO money — a 0
-- and a NULL both fail a target of 65 — so `kpis_met` and `tier` are unchanged and no past payout moves.
-- Changing the DENOMINATOR the display shows (7 → 6) is the visible effect.
--
--   -- UPDATE commcalc.raw_dlar_rep
--   --    SET boost_app_pct = NULL
--   --  WHERE org_id = '00000000-0000-0000-0000-000000000001'
--   --    AND COALESCE(ga_prepaid, 0) <= 0
--   --    AND COALESCE(boost_app_pct, 0) = 0;
--   -- REVERT: there is none — the fabricated 0 is not recoverable, which is why it waits.
--
-- ⚠ WHAT THIS BLOCK DOES **NOT** DO, AND MUST NOT BE CONFUSED WITH. It does not RECOMPUTE the metric on
-- a different denominator. The carrier's own Boost App % for the reported rep is 61.54% = 8 / 13 — i.e.
-- bounty over ALL activations, not over PREPAID activations. If that is the carrier's rule, the platform
-- has been using the wrong denominator as well as an empty one, and correcting it MOVES PAST TIERS.
-- Measured (read-only, nothing written), recomputing boostapp as boost_ready_bounty / gross_adds:
--
--     period          rep                 kpis_met      tier           subtotal    delta
--     July 2026       Khan, Ismail        4 -> 5        0.50 -> 0.75      293.09   +$73.27
--     July 2026       Singh, Simarjyot    6 -> 7        0.75 -> 1.00      475.22  +$118.80
--     August 2026     Singh, Simarjyot    4 -> 5        0.50 -> 0.75      594.75  +$148.69
--     ────────────────────────────────────────────────────────────────────────────  +$340.77
--
-- Three rep-months, all in the reps' favour (MetricsPro UNDER-paid). March / April / May / June /
-- September: no tier moves. THE OWNER'S TWO OPEN QUESTIONS, put to him and NOT answered here:
--   (i)  the 189 historical rows — correcting them changes past KPI scores and therefore past tiers;
--   (ii) confirm the rule: no prepaid activations → Boost App NOT MEASURED, rather than a zero failed.
-- A third, found while measuring: is the carrier's denominator ALL activations rather than prepaid ones?
