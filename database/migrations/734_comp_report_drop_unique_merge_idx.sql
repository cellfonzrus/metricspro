-- 734_comp_report_drop_unique_merge_idx.sql
--
-- WHY. A live upload of the April 2026 Comprehensive Comp file failed with:
--
--   duplicate key value violates unique constraint "raw_comp_report_merge_idx"
--   Key (org_id, period, external_reference_id)=(00000000-…-0001, April 2026, 1379) already exists.
--
-- The comp report carries MANY compensation line items per ExternalReferenceID — one payment
-- reference covers several rows — so `(org_id, period, external_reference_id)` is NOT a unique
-- key and a UNIQUE index on it rejects valid data. This is the SAME defect already recorded for
-- `raw_sales_dedup_idx (org_id, period, trans_id)`, which assumed one row per transaction when one
-- transaction has many line items, and was changed to non-unique for exactly this reason.
--
-- THE REPO ALREADY KNEW. Migration 031 drops this very index, with the comment:
--     "REPLACE stores the report verbatim, including any repeated external_reference_id, so a
--      unique constraint would reject valid rows."
-- Yet the index is present in the live database, and NOTHING in this repository creates it — the
-- only mention anywhere is 031's DROP. It was therefore created by hand outside the migrations,
-- after (or instead of) 031. This migration re-asserts the repo's intent and, unlike a one-off
-- statement in the SQL editor, leaves a tracked record so the next hand-made index is visible as a
-- divergence rather than a mystery.
--
-- WHY IT NEVER FIRED BEFORE. `external_reference_id` is NULL in every existing row (March 11,039 /
-- May 10,657 / June 3,943 — all NULL), and Postgres does not collide NULLs in a unique index, so
-- the constraint sat inert. The new comp mapper populates the column for the first time, which is
-- what surfaced it. Same family as the documented ON CONFLICT + nullable-unique-column trap.
--
-- Moves NO payout number, rate, plan, schedule of pay, or paid/earned column: this only stops a
-- correct row being refused at ingest. Idempotent, and reversible by recreating the index.

begin;

drop index if exists commcalc.raw_comp_report_merge_idx;

-- Keep the lookup benefit without the false uniqueness claim. Same columns, plain btree — this is
-- precisely the shape raw_sales_dedup_idx was converted to.
create index if not exists raw_comp_report_merge_idx
    on commcalc.raw_comp_report using btree (org_id, period, external_reference_id);

comment on index commcalc.raw_comp_report_merge_idx is
    'NON-UNIQUE deliberately (mig 734): the comp report has many line items per '
    'ExternalReferenceID, so (org_id, period, external_reference_id) is a lookup key, never a '
    'uniqueness guarantee. Re-pull dedup is handled by commcalc/safe_replace.py, which inserts the '
    'new load and only then retires the old one — it does not rely on a unique constraint.';

commit;
