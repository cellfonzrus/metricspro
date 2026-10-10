-- 1071_cash_count_revision.sql — EVERY CORRECTION TO A MANAGEMENT CASH COUNT, ON THE RECORD
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim 2026-10-10: "also need to edit the cash entered by the management during cash
-- recon".
--
-- Two defects wearing one hat. (1) No way in: the deposit-accountability board offered
-- `open & count` on a SEALED envelope only, so once a count existed a typo could not be corrected
-- from the screen it was entered on. (2) And no record if you got in anyway: all three writers of a
-- management cash count UPDATE or UPSERT in place, so a second save replaced the first with nothing
-- kept — not the previous figure, not who had entered it, not that it had ever been different.
--
-- THE CLASS, NOT THE INSTANCE. Defect 2 is migration `935` happening again. That migration exists
-- because "the verification row is an UPSERT, so a second DM save overwrote the previous dm_*
-- correction with no history". The same sentence is true of a cash count, and a cash count is worse:
-- when it reads short, `envelope_report.chargeback_parent_row` can turn it into money off a named
-- rep's pay. **A cash figure a human typed is a statement by that human, and replacing it without
-- keeping the old one destroys evidence.**
--
-- So this is not an edit button with a log beside it. It is ONE append-only trail that EVERY writer
-- of a management cash count appends to, across all three tables and every org:
--     closing/router._confirm_pickup_impl   the DM's count at pickup          (cash_pickup)
--     closing/router._open_count_impl       the late open on the board        (cash_pickup / billpay_pickup)
--     closing/router.save_envelope_count    management's count on the receipt (envelope_count)
-- What a correction IS lives in ONE place, `closing/count_revision.revision_rows`; the router has
-- ONE writer, `_write_count_revisions`, that inserts what it returns.
-- `backend/harness_cash_count_revision.py` §D FAILS THE BUILD if a writer of a count column stops
-- calling it or if anything else inserts into this table — so the trail cannot un-wire, which is the
-- failure mode §19.18 records three times.
--
-- WHY A NEW TABLE AND NOT A COLUMN (the duplicate-check gate). Columns cannot hold a HISTORY: one
-- `previous_amount` column keeps only the last-but-one value, which is the same defect one step back.
-- The existing audit tables are each bound to their own subject (`daily_closing_verification_audit`
-- mig 935 is keyed on a verification row and carries the six `dm_*` money columns by name; the
-- `core.access_log` family records reads, not value changes), so none can carry a cash count from
-- three different tables. This table is deliberately SUBJECT-PARAMETERISED — `subject_table` is a
-- VALUE, not a branch (RULE TWO) — so the fourth count writer needs no fourth audit table.
--
-- ── WHAT SHIPS ─────────────────────────────────────────────────────────────────────────────────
--   commcalc.cash_count_revision — append-only, one row per FIELD whose stored value changed:
--     org_id, subject_table, subject_row_id, store_code, close_date, employee_name, field,
--     previous_amount / new_amount  (money columns — so a figure is never read back out of a string
--                                    and a reviewer can total the corrections)
--     previous_text  / new_text     (who-counted, handed-to, comment)
--     changed_by, changed_at, note
--
-- ONLY A REAL CHANGE IS RECORDED. Re-saving the same number writes nothing (compared on rounded
-- cents, so 250 and 250.00 are one figure), and a FIRST entry is not a correction — there was nothing
-- to overwrite. A value being REMOVED is a change, because a figure being taken away is precisely
-- what a reviewer needs to see.
--
-- NEVER A CHANGE WITHOUT A CHANGER, BUT NEVER A LOST CHANGE EITHER: `changed_by` is NULLABLE and a
-- NULL reads as "not recorded", rather than attributing the edit to whoever was nearby. Dropping the
-- revision because the actor is unknown would lose the evidence to protect the attribution, which is
-- backwards.
--
-- 💰 MOVES NO MONEY. One new visibility table. It books nothing, pays nothing, settles nothing;
-- no P&L, GP, payroll, commission or payout path reads it, and nothing here alters an existing
-- table, column, amount or constraint. Correcting a count can of course change a variance a human
-- later turns into a chargeback — through the EXISTING, human-ticked `envelope_report` flow.
--
-- ADAPTIVE BY CONSTRUCTION (the mig-201 precedent every writer in this module follows): the router's
-- revision write is wrapped so an un-migrated database still records the COUNT itself — only the
-- trail is missing, and the API says so rather than refusing the correction. Nothing breaks before
-- this is run.
--
-- RLS: `open_all` for the authenticated role, matching every other commcalc operational table
-- (mig 034 / 942) — org scoping is enforced in the query layer, as it is everywhere here. There is
-- deliberately NO update or delete policy expressed in application code: nothing in the codebase
-- updates or deletes a revision row, which `harness_cash_count_revision.py` §D asserts.
--
-- Additive + idempotent. Run in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS commcalc.cash_count_revision (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  subject_table   TEXT NOT NULL,
  subject_row_id  TEXT,
  store_code      TEXT,
  close_date      DATE,
  employee_name   TEXT,
  field           TEXT NOT NULL,
  previous_amount NUMERIC,
  new_amount      NUMERIC,
  previous_text   TEXT,
  new_text        TEXT,
  changed_by      TEXT,
  changed_at      TIMESTAMPTZ DEFAULT now(),
  note            TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);

-- The subject vocabulary is pinned to `closing/count_revision.SUBJECTS` by
-- harness_cash_count_revision.py §A, so the database cannot hold a subject the module has no
-- behaviour for. A new count writer adds its table to BOTH, in one PR.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'cash_count_revision_subject_known') THEN
    EXECUTE $q$ALTER TABLE commcalc.cash_count_revision
               ADD CONSTRAINT cash_count_revision_subject_known
               CHECK (subject_table IN ('cash_pickup', 'billpay_pickup', 'envelope_count'))$q$;
  END IF;
END $$;

-- The two reads this table has: "has THIS figure been corrected" (the board, per subject row) and
-- "what was corrected in this range" (the export / review).
CREATE INDEX IF NOT EXISTS cash_count_revision_subject
  ON commcalc.cash_count_revision (org_id, subject_table, subject_row_id);
CREATE INDEX IF NOT EXISTS cash_count_revision_day
  ON commcalc.cash_count_revision (org_id, close_date, store_code);

COMMENT ON TABLE commcalc.cash_count_revision IS
  'APPEND-ONLY trail of every correction to a management cash count (owner 2026-10-10). One row per '
  'FIELD whose stored value changed, across cash_pickup / billpay_pickup / envelope_count. Nothing '
  'updates or deletes a row here — that is what makes it evidence. Written in ONE place, '
  'closing/router._write_count_revisions, from closing/count_revision.revision_rows; locked by '
  'backend/harness_cash_count_revision.py. See index 73.';
COMMENT ON COLUMN commcalc.cash_count_revision.subject_table IS
  'WHICH table the corrected figure lives in — a VALUE, not a branch (RULE TWO). Pinned to '
  'count_revision.SUBJECTS by the harness.';
COMMENT ON COLUMN commcalc.cash_count_revision.previous_amount IS
  'The figure BEFORE this write. NULL for a text-field correction, or where the figure was absent '
  'and is therefore a first entry rather than a correction (a first entry writes no row at all).';
COMMENT ON COLUMN commcalc.cash_count_revision.changed_by IS
  'WHO made the correction. NULLABLE and a NULL means NOT RECORDED — never a guess, and never a '
  'reason to drop the revision: losing the evidence to protect the attribution is backwards.';

ALTER TABLE commcalc.cash_count_revision ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname = 'commcalc' AND tablename = 'cash_count_revision'
                    AND policyname = 'open_all') THEN
    EXECUTE $q$CREATE POLICY open_all ON commcalc.cash_count_revision
               FOR ALL TO authenticated USING (true) WITH CHECK (true)$q$;
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1071 complete — commcalc.cash_count_revision (append-only cash-count corrections)'
       AS status;

-- REVERT:
--   DROP TABLE IF EXISTS commcalc.cash_count_revision;
--   (Safe at any time, and it destroys the correction history — which is the point of the table, so
--    a revert is a decision, not a cleanup. The COUNTS themselves live on cash_pickup /
--    billpay_pickup / envelope_count and are untouched by this migration either way; the router's
--    revision write is wrapped, so with the table gone a correction still records the count and the
--    API simply reports that the trail is unavailable.)
