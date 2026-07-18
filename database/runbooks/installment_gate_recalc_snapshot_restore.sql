-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- RUNBOOK (REVERSAL LAYER L3) — pre-recalc snapshot + instant restore for the Fix B installment gate.
-- Owner-mandated 2026-07-18. NOT a migration (do not run in sequence) — a PARAMETERIZED template the
-- operator runs by hand in the Supabase SQL editor BEFORE the first Calculate per (org, period) after the
-- mig-223 gate ships, so a bad recalc can be undone WITHOUT recomputing.
--
-- HOW TO USE: replace the three :params below and run STEP 1 before the recalc. If the recalc looks wrong,
-- run STEP 3 (restore) — it deletes the just-written rows for that period and re-inserts the snapshot.
--   :ORG        e.g. '854f6d7b-6590-4e4d-88ab-646f560d4f4c'   (any tenant — NOT hardcoded)
--   :STAMP      e.g. '20260718_1400'                          (a timestamp so multiple snapshots coexist)
--   :PERIODS    the period-spelling variants, e.g.  ('June 2026','2026-06')   (see _pvariants)
-- Both money tables are covered: sale_installment_ledger (what the gate writes) and rep_commissions (the
-- pay row that the installment component rolls into). Backups live in a per-run schema-less _backup table.
-- ══════════════════════════════════════════════════════════════════════════════════════════════════

-- ── STEP 1 — SNAPSHOT (run BEFORE the recalc) ───────────────────────────────────────────────────────
-- The backup table names embed :ORG-less :STAMP; keep the STAMP unique per run.
CREATE TABLE IF NOT EXISTS commcalc.sale_installment_ledger_backup_:STAMP AS
  SELECT * FROM commcalc.sale_installment_ledger
   WHERE org_id = ':ORG' AND pay_period IN :PERIODS;

CREATE TABLE IF NOT EXISTS commcalc.rep_commissions_backup_:STAMP AS
  SELECT * FROM commcalc.rep_commissions
   WHERE org_id = ':ORG' AND period IN :PERIODS;

-- sanity: how many rows were snapshotted
SELECT 'sale_installment_ledger' AS tbl, count(*) FROM commcalc.sale_installment_ledger_backup_:STAMP
UNION ALL
SELECT 'rep_commissions', count(*) FROM commcalc.rep_commissions_backup_:STAMP;

-- ── STEP 2 — (run the Calculate from the app: POST /commcalc/calculate/{period}) ────────────────────
-- Poll /commcalc/commissions/{period}; a >300s 502 still completed — DO NOT re-fire (that re-zeroes).

-- ── STEP 3 — RESTORE (run ONLY if the recalc is wrong; instant undo, no recompute) ──────────────────
-- Wrap in a transaction so a partial failure rolls back.
BEGIN;
  DELETE FROM commcalc.sale_installment_ledger
   WHERE org_id = ':ORG' AND pay_period IN :PERIODS;
  INSERT INTO commcalc.sale_installment_ledger
   SELECT * FROM commcalc.sale_installment_ledger_backup_:STAMP;

  DELETE FROM commcalc.rep_commissions
   WHERE org_id = ':ORG' AND period IN :PERIODS;
  INSERT INTO commcalc.rep_commissions
   SELECT * FROM commcalc.rep_commissions_backup_:STAMP;
COMMIT;

-- ── STEP 4 — CLEANUP (after you are satisfied the recalc is correct OR the restore is done) ─────────
-- DROP TABLE IF EXISTS commcalc.sale_installment_ledger_backup_:STAMP;
-- DROP TABLE IF EXISTS commcalc.rep_commissions_backup_:STAMP;
