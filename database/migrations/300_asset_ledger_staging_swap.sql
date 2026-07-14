-- 300_asset_ledger_staging_swap.sql — asset-2: transactional staging-swap for the asset_ledger upload
--
-- WHY: today's upload (process_asset_ledger_bytes in backend/app/modules/asset/router.py) does a plain
-- DELETE-then-batched-INSERT of ~43.8k rows straight into the LIVE commcalc.asset_ledger, with no
-- try/except around the insert loop. PostgREST executes each .insert() call as its own statement, so if
-- (say) batch 3 of 9 fails, the live ledger is left with rows from batches 1-2 and rows from batches 3-9
-- GONE — a PARTIAL ledger, silently, because the org's old rows were already deleted first. That directly
-- violates this module's own guardrail ("never an empty/partial ledger") and PLATFORM §5(c).
--
-- FIX (staging + atomic swap): ingest the parsed rows into a SCRATCH table
-- (commcalc.asset_ledger_staging) using the SAME batched-insert code path as today — if any batch fails,
-- only the scratch table is dirty; the live commcalc.asset_ledger is never touched. Once every row is
-- confirmed staged, ONE Postgres function call (commcalc.asset_ledger_swap_from_staging) does the
-- delete-old + insert-new for that org's rows in a SINGLE transaction. PostgREST wraps each RPC call in
-- one transaction, so if anything inside the function raises, Postgres rolls the whole call back and the
-- live ledger is left exactly as it was. The swap has to be a Postgres function/RPC (not more
-- application-side calls) precisely because PostgREST calls can't span a transaction — "one transaction"
-- only exists if the delete+insert both happen inside a single function body.
--
-- ORG-SCOPE: both the staging table and the swap function are org_id-scoped (multi-tenant rule). The
-- function only ever deletes/inserts rows for the p_org_id passed in — it can never touch another
-- tenant's ledger, and a race between two tenants' concurrent uploads never crosses org boundaries.
--
-- DEGRADE GRACEFULLY (contract §5): backend/app/modules/asset/router.py probes for
-- commcalc.asset_ledger_staging before using this path (_staging_available()). If this migration hasn't
-- run yet, upload falls back to TODAY'S exact delete+insert-direct code path (not atomic, but
-- unchanged/already-proven-in-prod — so the asset page doesn't break over an unrun migration) and logs a
-- loud warning (core.failure_log category 'asset_upload_degraded_mode' + a Railway log line) on every
-- such upload, so the missing migration doesn't go unnoticed. The same fallback fires if the TABLE exists
-- but the FUNCTION specifically doesn't (a partially-applied migration) — detected from the RPC call's
-- own error rather than assumed.
--
-- NON-GOAL: two uploads racing for the SAME org (interleaved staging inserts before either swap fires)
-- is a pre-existing risk — today's direct-write path already races the same way against the live table —
-- and is out of scope here. This migration only fixes atomicity against a mid-batch FAILURE, not
-- concurrent WRITERS for one tenant. Flagged for a future advisory-lock follow-up if it ever bites.
--
-- Safe to run against the live project: commcalc.asset_ledger already exists there (it predates the
-- checked-in migration history — see asset-6 for capturing that gap separately). `LIKE ... INCLUDING ALL`
-- copies its CURRENT live column list/types/defaults/indexes 1:1, so the staging table can never silently
-- drift out of sync with the real ledger schema. (LIKE never copies foreign keys — asset_ledger has none
-- that this needs.) Additive + idempotent: safe to re-run.

create table if not exists commcalc.asset_ledger_staging (
  like commcalc.asset_ledger including all
);

-- asset_ledger itself has no org_id index today (030_perf_indexes.sql predates org-scoping there), but
-- the staging table is specifically org-scoped scratch space shared across every tenant's uploads, so it
-- needs one.
create index if not exists ix_asset_ledger_staging_org on commcalc.asset_ledger_staging (org_id);

alter table commcalc.asset_ledger_staging enable row level security;
do $$ begin
  create policy open_all on commcalc.asset_ledger_staging for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on commcalc.asset_ledger_staging to anon, authenticated, service_role;

-- Atomic swap: delete the org's live rows + insert the org's staged rows, in ONE function call
-- (= one PostgREST transaction). Refuses — i.e. raises, so NOTHING is touched, delete included, because
-- the raise happens before the delete — if staging is empty for the org, or if p_expected_rows is given
-- and doesn't match what's actually staged. Both guard against swapping in a partial/empty stage.
create or replace function commcalc.asset_ledger_swap_from_staging(
  p_org_id uuid,
  p_expected_rows integer default null
)
returns table(rows_swapped bigint)
language plpgsql
as $$
declare
  v_staged_count  bigint;
  v_swapped_count bigint;
begin
  select count(*) into v_staged_count
  from commcalc.asset_ledger_staging
  where org_id = p_org_id;

  if v_staged_count = 0 then
    raise exception 'asset_ledger_swap_from_staging: no staged rows for org_id % — refusing to touch the live ledger', p_org_id;
  end if;

  if p_expected_rows is not null and v_staged_count <> p_expected_rows then
    raise exception 'asset_ledger_swap_from_staging: staged % rows but caller expected % — refusing (partial stage?)', v_staged_count, p_expected_rows;
  end if;

  delete from commcalc.asset_ledger where org_id = p_org_id;

  insert into commcalc.asset_ledger
  select * from commcalc.asset_ledger_staging where org_id = p_org_id;

  get diagnostics v_swapped_count = row_count;

  delete from commcalc.asset_ledger_staging where org_id = p_org_id;

  return query select v_swapped_count;
end;
$$;

grant execute on function commcalc.asset_ledger_swap_from_staging(uuid, integer) to anon, authenticated, service_role;

notify pgrst, 'reload schema';
select '300 complete — commcalc.asset_ledger_staging + asset_ledger_swap_from_staging (atomic upload swap)' as status;
