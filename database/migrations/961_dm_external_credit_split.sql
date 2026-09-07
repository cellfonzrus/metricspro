-- 961_dm_external_credit_split.sql — let the DM correct the EXTERNAL-CREDIT portion of a verified
-- store-day's card total, so the mig-935 original-vs-modified audit trail and the settlement tally
-- keep working on DM-corrected days (owner directive 2026-09-04).
--
-- ── THE DEFECT THIS FIXES (evidence-first) ─────────────────────────────────────────────────────
-- `closing/verified_overlay.apply_overlay` maps the DM's ONE corrected card figure `dm_store_cc`
-- onto BOTH column families and ZEROES the folded siblings:
--       dm_store_cc → store_cc, t_credit ; zero epay_cc, t_ext_cc
-- That zeroing is CORRECT arithmetic today — `dm_store_cc` is the DM's corrected COMBINED card
-- total, and a consumer summing t_credit + t_ext_cc would otherwise double-count. But it means that
-- on a DM-verified-and-corrected store-day the EXTERNAL-CREDIT-MACHINE split is destroyed: the
-- declared external figure reads 0.00, so a tally against the third-party processor's scraped
-- settlement total would report the entire day as SHORT — a fabricated variance, not evidence.
--
-- ── THE FIX: an ADDITIVE seventh DM field that SPLITS, never MOVES, the corrected total ─────────
-- `dm_ext_cc` = how much of the DM's corrected card total `dm_store_cc` was taken on the external
-- credit machine. The overlay's INVARIANT is total preservation, to the cent:
--
--     dm_ext_cc IS NULL  ⇒  t_credit = dm_store_cc            , t_ext_cc = 0.00   (TODAY, unchanged)
--     dm_ext_cc SET      ⇒  t_credit = dm_store_cc − dm_ext_cc, t_ext_cc = dm_ext_cc
--                           ────────────────────────────────────────────────────
--                           t_credit + t_ext_cc = dm_store_cc  in BOTH branches
--
-- So every downstream consumer that reads the CARD TOTAL is byte-identical either way — including
-- the mig-939 bill-pay coverage card base (`commcalc.router._closing_collected_by_store_day`), the
-- mig-944 3-way recon credit leg (`closing/router.cash_recon_management`, which overrides the slot
-- with `dm_store_cc` directly), `/closing/summary` `total_collected`, and the deposit / cash-position
-- readers (which use `overlay_cash_reader`, a cash-only path this migration does not touch at all).
-- Only the SPLIT between the two card columns becomes known. Proof:
-- `backend/harness_external_credit_recon.py` §C (total-preservation truth table, both branches) and
-- the unchanged `backend/harness_verified_overlay.py`.
--
-- ── DUPLICATE CHECK ────────────────────────────────────────────────────────────────────────────
-- No new table. `dm_ext_cc` joins the SIX existing `dm_*` columns on the mig-029
-- `daily_closing_verification` upsert row and its append-only mig-935 audit twin, and rides the
-- EXISTING machinery end to end: `verification_audit.DM_FIELDS` (so `changed_fields` /
-- `build_audit_row` / `edited_after_verify` / `submission_dm_fields` cover it with no new logic),
-- `verified_overlay.build_overlay_map` / `has_correction` / `apply_overlay`, and the DM Verify +
-- submissions exports' existing Original-vs-DM columns. NO new endpoint: `POST /closing/verify`
-- already forwards the DM body.
--
-- ── MONEY ──────────────────────────────────────────────────────────────────────────────────────
-- Total-preserving by construction (invariant above) and NULL on every existing row ⇒ every booked
-- number is byte-identical the moment this migration is applied. No seed, nothing to approve.
-- Pre-961 safety: `build_overlay_map` selects `dm_ext_cc` and, on the PostgREST "column does not
-- exist" error, RETRIES with the legacy six-column select — an un-migrated org keeps its overlay.
--
-- Additive + idempotent. RLS/grants inherited from migs 029 / 935. Config-free; not a data feed →
-- no data_lineage_registry / 925 entry.
--
-- REVERT (paste and run to undo — drops only what this migration owns; corrections stored in
-- dm_ext_cc are lost, and DM-corrected card days revert to the merged split):
--   ALTER TABLE commcalc.daily_closing_verification       DROP COLUMN IF EXISTS dm_ext_cc;
--   ALTER TABLE commcalc.daily_closing_verification_audit DROP COLUMN IF EXISTS dm_ext_cc;
--   ALTER TABLE commcalc.daily_closing_verification_audit DROP COLUMN IF EXISTS prior_dm_ext_cc;
--   NOTIFY pgrst, 'reload schema';

ALTER TABLE commcalc.daily_closing_verification
  ADD COLUMN IF NOT EXISTS dm_ext_cc NUMERIC;

COMMENT ON COLUMN commcalc.daily_closing_verification.dm_ext_cc IS
  'DM-corrected EXTERNAL-CREDIT-MACHINE portion OF dm_store_cc (mig 961). NULL = the DM did not '
  'split the corrected card total ⇒ verified_overlay behaves exactly as before mig 961. When set, '
  't_ext_cc = dm_ext_cc and t_credit = dm_store_cc − dm_ext_cc, so the card TOTAL never moves.';

ALTER TABLE commcalc.daily_closing_verification_audit
  ADD COLUMN IF NOT EXISTS dm_ext_cc       NUMERIC,
  ADD COLUMN IF NOT EXISTS prior_dm_ext_cc NUMERIC;

COMMENT ON COLUMN commcalc.daily_closing_verification_audit.dm_ext_cc IS
  'New value of daily_closing_verification.dm_ext_cc on this revision (mig 961; append-only trail '
  'from mig 935 — prior value in prior_dm_ext_cc, presence in changed_fields).';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 961 complete — dm_ext_cc on daily_closing_verification + its mig-935 audit twin (total-preserving split)' AS status;
