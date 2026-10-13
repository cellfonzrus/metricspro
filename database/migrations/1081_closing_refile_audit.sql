-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- 1081 — A CLOSING RE-FILED TO ANOTHER STORE SAYS SO ON ITS FACE (owner 2026-10-13, index §29.14)
--
-- OWNER, verbatim: *"also the manager should be able to go in and move the wrong closing tor trh
-- cioreect store"*. The move itself needs no schema change — it rewrites `store_code` /
-- `store_address` / `store_name` / `dedup_key` on an existing row, through the one home
-- `backend/app/modules/closing/closing_refile.py`. What was missing is the TRAIL: after a move, the
-- row looked exactly like a closing that had always been filed at that store, so the repair of a
-- mistake was indistinguishable from the mistake never happening. These four columns are that trail.
--
-- ADDITIVE AND IDEMPOTENT. Four nullable columns and one index. No row is written, no amount moves,
-- nothing is recomputed, and `NULL` means what it has always meant: this closing has never been
-- re-filed. Safe to apply at any time, and the endpoint works without it (it reports that the trail
-- is missing rather than silently dropping it) — so this migration is a RECORD-KEEPING improvement,
-- not a gate on the feature.
--
-- MONEY: none. No amount column is touched and no total anywhere changes by a cent.
--
-- -- REVERT:
--   alter table commcalc.daily_closing drop column if exists refiled_from_store_code;
--   alter table commcalc.daily_closing drop column if exists refiled_at;
--   alter table commcalc.daily_closing drop column if exists refiled_by;
--   alter table commcalc.daily_closing drop column if exists refile_note;
--   drop index if exists commcalc.daily_closing_refiled_idx;
-- ════════════════════════════════════════════════════════════════════════════════════════════════

alter table commcalc.daily_closing
  add column if not exists refiled_from_store_code text,
  add column if not exists refiled_at              timestamptz,
  add column if not exists refiled_by              text,
  add column if not exists refile_note             text;

comment on column commcalc.daily_closing.refiled_from_store_code is
  'The store this closing was filed against BEFORE a manager re-filed it (closing/closing_refile, '
  'POST /closing/row/{id}/refile). NULL = never re-filed. Never overwritten by a resubmit: a '
  'correction updates the amounts, it does not un-move the row.';
comment on column commcalc.daily_closing.refiled_at is
  'When the re-file happened. With refiled_by and refile_note this is the whole audit of the move; '
  'the rep''s original attempt stays recorded, unaltered, in commcalc.closing_attempt.';

-- Finding the re-filed rows is a review question ("which store-days were corrected this month"), so
-- the index is partial — it costs nothing on the 3,583 rows that have never been moved.
create index if not exists daily_closing_refiled_idx
  on commcalc.daily_closing (org_id, refiled_at desc)
  where refiled_at is not null;

notify pgrst, 'reload schema';
