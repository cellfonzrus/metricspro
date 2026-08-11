-- 746_payroll_approval_lunch_adjustment.sql
-- 2026-08-11 — OWNER DIRECTIVE: "in the payroll hours approval, the lunch hour calculated on the
-- previous screen and the adjustment hours with a reason for adjustment should be added as
-- additional columns, the final payable hours should then be approved."
--
-- ⚠️ THE TRAP THIS SCHEMA IS SHAPED AROUND. `GET /storeops/payroll` returns `actual_hours` that is
-- ALREADY NET of the lunch deduction — router.py does
--     summary[eid]["actual_hours"] -= applied ; summary[eid]["lunch_deduction_hours"] += applied
-- so the obvious reading of the directive, payable = worked − lunch + adjustment over actual_hours,
-- would DEDUCT LUNCH TWICE and short every paycheque by 30 minutes a shift. The board therefore
-- shows Worked as the GROSS figure (net + lunch) and computes
--     payable = actual_hours (already net) + adjustment
-- which is the same identity without the second deduction. harness_payroll_lunch_adjustment.py
-- asserts exactly this, and fails if anyone later "simplifies" it.
--
-- adjustment_hours defaults to 0, so until a DM types one every existing payable figure is
-- byte-identical to what migration 431 produced. Nothing auto-applies; a number only moves when a
-- human enters it WITH a reason, which is then written to the mig-414 payroll_change_log.
--
-- The two *_at_approval snapshots freeze what the DM actually signed off. Punches get edited and
-- lunch config gets changed after the fact; without these, an approved row would silently drift
-- away from the number a human approved — the same principle as [[recalc-additive-never-erase-review]].

BEGIN;

ALTER TABLE storeops.payroll_approval
  ADD COLUMN IF NOT EXISTS adjustment_hours     numeric NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS adjustment_reason    text,
  ADD COLUMN IF NOT EXISTS lunch_at_approval    numeric,
  ADD COLUMN IF NOT EXISTS worked_at_approval   numeric;

COMMENT ON COLUMN storeops.payroll_approval.adjustment_hours IS
  'Manual +/- correction in hours, entered by the DM with a reason. 0 = untouched. Payable hours = '
  'the payroll screen''s actual_hours (ALREADY net of lunch) + this.';
COMMENT ON COLUMN storeops.payroll_approval.lunch_at_approval IS
  'The auto lunch deduction as it stood when the DM approved — frozen so a later config or punch '
  'edit cannot silently restate an approved week.';
COMMENT ON COLUMN storeops.payroll_approval.worked_at_approval IS
  'GROSS worked hours (net + lunch) as they stood when the DM approved. See lunch_at_approval.';

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM information_schema.columns
   WHERE table_schema = 'storeops' AND table_name = 'payroll_approval'
     AND column_name IN ('adjustment_hours', 'adjustment_reason',
                         'lunch_at_approval', 'worked_at_approval');
  IF n <> 4 THEN RAISE EXCEPTION 'expected 4 new payroll_approval columns, found %', n; END IF;
END $$;

COMMIT;
