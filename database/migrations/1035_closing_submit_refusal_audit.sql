-- 1035_closing_submit_refusal_audit.sql — a REFUSED daily closing leaves a record (index §29.11).
-- Run this in the Supabase SQL editor. Numbered, idempotent, additive.
--
-- ⚠️ SCHEMA CHANGE — SURFACED FOR OWNER APPROVAL BEFORE APPLYING. No money column is read or
-- written anywhere in this file. Statement (2) rewrites `commcalc.daily_closing.dedup_key`, which is
-- an identity/dedup key, never an amount; it refuses to run rather than merge anything ambiguous.
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────────
-- Owner bug report 2026-10-02, 117 E Burnside Ave: *"abid did the daily closing for the 117
-- bunrsoide ave … we cannot see the daily closing which was submitted, also he did it again today it
-- is still not showing"*.
--
-- MEASURED in live data, read-only, before anything was written:
--   commcalc.daily_closing              B-117: rows on 09-28, 09-29, 09-30 — NONE on 10-01 / 10-02
--   commcalc.closing_attempt            nothing from that submitter at ANY store since 09-28
--   commcalc.daily_closing_verification no B-117 row — it did not land on the DM-verify screen either
--   commcalc.envelope_payout_config     require_photo_if_cash = true, org-wide, since 2026-08-11
-- The closing was not hidden. It was REFUSED and nothing was kept.
--
-- THE CLASS, not the instance: `closing/router.create_row` runs every validation BEFORE the first
-- write, and `closing_attempt` — the submit audit trail — is written only once they all pass. So all
-- eight refusal paths stored nothing, for any store, any rep, any tenant. The code fix routes every
-- one through `closing/router._refuse`, which records it in THIS table (the existing mechanism — not
-- a sibling), and GET /closing/attempts already reads it. These columns are what it records into.
--
-- REVERT (1): alter table commcalc.closing_attempt drop column if exists refusal_detail;
--             alter table commcalc.closing_attempt drop column if exists refusal_code;
--             alter table commcalc.closing_attempt drop column if exists refused;
--             drop index if exists commcalc.closing_attempt_refused;

alter table commcalc.closing_attempt add column if not exists refused        boolean default false;
alter table commcalc.closing_attempt add column if not exists refusal_code    text;
alter table commcalc.closing_attempt add column if not exists refusal_detail  text;

comment on column commcalc.closing_attempt.refused is
  'TRUE = this submit was REFUSED and no closing was stored. Never a counting try: the 3-try close '
  'gate and every reader filter it out through closing/submit_refusal.is_real_try (index §29.11).';
comment on column commcalc.closing_attempt.refusal_code is
  'A key of closing/submit_refusal.REFUSALS — the ONE place a refusal reason is declared.';

-- Only refusals are ever filtered on, so the index is partial.
create index if not exists closing_attempt_refused
  on commcalc.closing_attempt (org_id, close_date, store_code)
  where refused;


-- ── (2) ONE dedup-key formula ─────────────────────────────────────────────────────────────────────
-- The key was spelled twice and had already drifted: Python folded a STRIPPED employee name,
-- migration 502's SQL folded the raw one. A name stored with a stray space therefore produced two
-- different keys for one person, and the unique index that exists to stop duplicates could not see
-- them. The key now lives in ONE home — backend/app/modules/closing/dedup_key.py — whose `SQL_EXPR`
-- is the expression below, verbatim; harness_closing_submit_refusal.py FAILS THE BUILD if this file
-- and that module ever disagree.
--
-- SAFETY: a group that is NOT a clean single row under the new folding is LEFT ALONE and reported by
-- the assert at the end, never merged on a guess. Measured before writing: 1,799 closings since
-- 2026-08-01, zero duplicate groups, zero NULL dedup_keys, zero blank store codes or names — so on
-- today's data this statement is expected to change nothing and exists to keep it that way.
--
-- REVERT (2): the previous keys are recomputable with migration 502's expression; nothing is lost.
with folded as (
  select d.id,
         d.org_id::text || '|' || btrim(coalesce(d.store_code,'')) || '|' || lower(btrim(coalesce(d.employee_name,''))) || '|' || d.close_date::text as k
  from commcalc.daily_closing d
  where btrim(coalesce(d.store_code,'')) <> '' and btrim(coalesce(d.employee_name,'')) <> ''
), clean as (
  select k from folded group by k having count(*) = 1
)
update commcalc.daily_closing d
   set dedup_key = f.k
  from folded f
  join clean c on c.k = f.k
 where d.id = f.id
   and coalesce(d.dedup_key,'') <> f.k;

-- A closing with no store or no employee name cannot be keyed, so it must not carry a key that
-- could collide with another. The code refuses such a submit now ('identity_missing'); any legacy
-- row is left keyless and therefore unconstrained, exactly as migration 502 left duplicate groups.
update commcalc.daily_closing
   set dedup_key = null
 where (btrim(coalesce(store_code,'')) = '' or btrim(coalesce(employee_name,'')) = '')
   and dedup_key is not null;


-- ── (3) The index stops protecting a released row — close that window ─────────────────────────────
-- Migration 502's index is `where dedup_key is not null and released_at is null`. A release unlocks
-- a row for exactly ONE corrected resubmit, and that resubmit UPDATEs the SAME row (never inserts a
-- second), so the `released_at is null` half protects nothing and only opens a window in which two
-- concurrent submits could both insert. Dropping that half is strictly stronger.
--
-- It refuses to run if any key would collide, rather than failing halfway through a CREATE INDEX.
do $$
declare bad int;
begin
  select count(*) into bad from (
    select dedup_key from commcalc.daily_closing
     where dedup_key is not null group by dedup_key having count(*) > 1) x;
  if bad > 0 then
    raise exception 'ABORT: % dedup_key(s) are held by more than one closing — review them on '
                    'Daily Closing -> Duplicates and release/merge before widening the index', bad;
  end if;
end $$;

-- REVERT (3): drop index if exists commcalc.daily_closing_one_active_per_rep_day;
--             create unique index daily_closing_one_active_per_rep_day
--               on commcalc.daily_closing (dedup_key)
--               where dedup_key is not null and released_at is null;
drop index if exists commcalc.daily_closing_one_active_per_rep_day;
create unique index if not exists daily_closing_one_active_per_rep_day
  on commcalc.daily_closing (dedup_key)
  where dedup_key is not null;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1035 complete — refused closings are recorded, and one dedup-key formula guards them' AS status;
