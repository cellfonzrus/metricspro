-- 287_commission_flags_review_persistence.sql
-- mod-commission · band 200–299 · additive + idempotent + safe to re-run
--
-- WHY
-- ───
-- OWNER DIRECTIVE 2026-08-08, verbatim:
--     "DM review should not be erased and teh new data should only add the missing data if any"
--
-- Migration 285/286 routed a flag to the right district manager. That only means something if the
-- manager's DECISION survives the night — and today it does not. `_run_calculation` DELETES every
-- `commcalc.flags` row for the period and re-inserts the whole set, and `_do_dlar_sweep` recalculates
-- Boost DAILY, so `reviewed_by` / `reviewed_at` / `action_taken` are wiped within 24 hours. The owner
-- did NOT ask for the review state to be saved and restored around the wipe. He asked for the wipe to
-- STOP: recalculation must be ADDITIVE — add what is missing, leave what is already there.
--
-- WHAT THIS DOES — none of it touches a payout number.
--   1. A STABLE PER-FLAG IDENTITY (`flag_key` + `key_basis`), plus the two identifier columns the
--      writers never persisted (`subscriber_id`, `source_ref`) that give the identifier-less rows a key
--      at all.
--   2. A LIFECYCLE (`status` / `resolved_at` / `resolved_reason` / `last_seen_at` / `last_run_id`) so a
--      flag whose condition has cleared can leave the active queue WITHOUT being deleted — which is
--      exactly what erases review state today.
--   3. Two set-based RPCs that perform the additive merge in Postgres:
--      `commcalc.flags_sync_batch()` (insert-missing + refresh-existing) and
--      `commcalc.flags_resolve_stale()` (retire what the run no longer produces).
--
-- THE STALE-FLAG PROBLEM, AND WHY `status` IS THE ANSWER
-- ─────────────────────────────────────────────────────
-- "Only ever ADD" creates a problem the owner's rule does not by itself solve: a flag whose underlying
-- condition later resolves (the sale gets matched, the discrepancy is corrected, the port-out is
-- reversed) would otherwise persist forever as a false accusation against a rep, and the queue would
-- only grow. Hard-deleting those is precisely the behaviour being removed. So they are RETIRED in
-- place: `status` moves off 'open', `resolved_at` records when, `resolved_reason` records why, the row
-- keeps its `reviewed_by`/`reviewed_at`/`action_taken`, and the default queue filters it out. Nothing
-- is destroyed and the history stays auditable.
--
--   status = 'open'        the condition is still present in the latest run — the active queue
--          = 'resolved'    a PREVIOUS ADDITIVE RUN produced this flag and the latest one did not:
--                          the condition genuinely cleared
--          = 'superseded'  the row predates the additive era (`last_run_id is null`) or its identity
--                          changed; a keyed row now represents it. Bookkeeping, not an accusation.
--
-- The two are distinguished automatically by `last_run_id is null`, so nobody has to remember which is
-- which. A retired flag whose condition RETURNS is reopened by `flags_sync_batch` (status back to
-- 'open', resolved_* cleared) and KEEPS its review — re-accusing a manager who already ruled on it is
-- the erasure this package exists to stop.
--
-- IDENTITY — deterministic, and honest about what it cannot key
-- ────────────────────────────────────────────────────────────
--   material = 'v1|<YYYYMM>|<FLAG_TYPE>|<source>|<ident>|<REP>|<STORE>'
--   flag_key = md5(material || '#' || <ordinal within identical material>)
--
--   ident  = the first non-empty of  imei → mdn → subscriber_id → source_ref     (key_basis records
--            which one won). imei/mdn come FIRST deliberately: they are already persisted on today's
--            31,766 rows, so the backfill below produces the same key the write path will produce on
--            the next run and those rows are ADOPTED rather than churned.
--   REP    = epay_salesperson. It is part of the identity on purpose: a flag ACCUSES a person, so if
--            the accused changes the flag is a NEW accusation and a manager's ruling on the old one
--            does not carry over.
--   STORE  = participates ONLY when there is no row-level ident (store/rep-level aggregate flags such
--            as HIGH_PORT_OUT_RATE / MISSING_STORE_SALES). Keeping it out otherwise means adding a
--            store alias cannot silently re-key — and orphan — an existing flag.
--   amount, description, severity, coaching_note and every display column are NOT in the material, so
--            a changed AMOUNT refreshes the SAME row (owner: "the new data should only add the missing
--            data") instead of creating a second one.
--
--   key_basis = 'imei' | 'mdn' | 'subscriber' | 'ref' | 'rep' | 'store' | 'none'.
--   'none' means the row carries NO identifier of any kind and its key is an ordinal inside its
--   flag_type/source group — reproducible only from an unchanged source multiset. Those rows are
--   COUNTABLE by design rather than quietly pretended to be stable.
--
-- 💰 MOVES NO MONEY. Every column added here is a visibility/lifecycle column. Nothing writes an
-- amount basis, a rate, a tier, a plan, a schedule or a paid/earned column; no payout path reads any
-- of them; `commcalc.flags` pays nobody.
--
-- MULTI-TENANT: both RPCs take `p_org_id` and filter every read AND write on it; the insert stamps it.
-- A key computed for one tenant can never match another tenant's row because org_id is in the
-- predicate, not the hash.

-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- 1. COLUMNS
-- ══════════════════════════════════════════════════════════════════════════════════════════════════
alter table commcalc.flags add column if not exists flag_key        text;
alter table commcalc.flags add column if not exists key_basis       text;
alter table commcalc.flags add column if not exists subscriber_id   text;
alter table commcalc.flags add column if not exists source_ref      text;
alter table commcalc.flags add column if not exists status          text not null default 'open';
alter table commcalc.flags add column if not exists resolved_at     timestamptz;
alter table commcalc.flags add column if not exists resolved_reason text;
alter table commcalc.flags add column if not exists last_seen_at    timestamptz;
alter table commcalc.flags add column if not exists last_run_id     uuid;

comment on column commcalc.flags.flag_key is
  'Deterministic per-flag identity (mig 287) — md5 of the identity material plus an ordinal. Lets a '
  'recalculation ADD what is missing and REFRESH what exists instead of delete-then-reinsert, so a '
  'district manager''s review survives. Visibility only — pays nobody.';
comment on column commcalc.flags.key_basis is
  'Which identifier the flag_key was built from: imei|mdn|subscriber|ref|rep|store|none. ''none'' = the '
  'row carried no identifier at all and its key is only reproducible from an unchanged source set.';
comment on column commcalc.flags.subscriber_id is
  'The MI report''s subscriber_id, carried onto the flag (mig 287). 100%% of the raw_mi rows that have '
  'neither a phone_number nor a device_serial DO carry this — it is the only stable identity those '
  'flags can have.';
comment on column commcalc.flags.source_ref is
  'The producing writer''s own natural reference for the underlying record (a trans_id, a payment '
  'type, …) when there is no imei/mdn/subscriber_id. Identity only.';
comment on column commcalc.flags.status is
  'open | resolved | superseded (mig 287). A flag whose condition has cleared is RETIRED here, never '
  'deleted, so reviewed_by/reviewed_at/action_taken and the audit trail survive.';
comment on column commcalc.flags.resolved_reason is
  'Plain-English why a flag left the active queue (mig 287).';
comment on column commcalc.flags.last_run_id is
  'The recalculation run that last produced this flag (mig 287). NULL = the row predates the additive '
  'write path; that is how ''superseded'' is told apart from a genuinely ''resolved'' condition.';

-- Deliberately NOT a unique index. Two rows can legitimately share a key across the two period
-- spellings ('June 2026' / '2026-06'), and a unique index would turn that legacy data condition into a
-- hard INSERT failure inside a recalculation. Matching is (org_id, flag_key) in the RPCs.
create index if not exists flags_org_flag_key_idx     on commcalc.flags (org_id, flag_key);
create index if not exists flags_org_period_status_idx on commcalc.flags (org_id, period, status);

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'flags_status_chk') then
    alter table commcalc.flags
      add constraint flags_status_chk check (status in ('open', 'resolved', 'superseded'));
  end if;
end$$;

-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- 2. THE IDENTITY FUNCTIONS  (mirrored exactly by backend/app/modules/commcalc/flag_persist.py;
--    harness_flag_review_persistence.py section E proves they agree key-for-key over the REAL rows)
-- ══════════════════════════════════════════════════════════════════════════════════════════════════
create or replace function commcalc.flag_period_canon(p_period text, p_month int, p_year int)
returns text
language sql
immutable
set search_path = ''
as $$
  select case
           when p_month between 1 and 12 and coalesce(p_year, 0) > 0
             then to_char(p_year, 'FM0000') || to_char(p_month, 'FM00')
           else upper(btrim(coalesce(p_period, '')))
         end
$$;

create or replace function commcalc.flag_key_ident(
  p_imei text, p_mdn text, p_subscriber_id text, p_source_ref text)
returns text
language sql
immutable
set search_path = ''
as $$
  select coalesce(
    nullif(upper(btrim(coalesce(p_imei, ''))), ''),
    nullif(upper(btrim(coalesce(p_mdn, ''))), ''),
    nullif(upper(btrim(coalesce(p_subscriber_id, ''))), ''),
    nullif(upper(btrim(coalesce(p_source_ref, ''))), ''),
    '')
$$;

create or replace function commcalc.flag_key_basis(
  p_imei text, p_mdn text, p_subscriber_id text, p_source_ref text,
  p_rep text, p_store_address text, p_store_code text)
returns text
language sql
immutable
set search_path = ''
as $$
  select case
    when nullif(upper(btrim(coalesce(p_imei, ''))), '')          is not null then 'imei'
    when nullif(upper(btrim(coalesce(p_mdn, ''))), '')           is not null then 'mdn'
    when nullif(upper(btrim(coalesce(p_subscriber_id, ''))), '') is not null then 'subscriber'
    when nullif(upper(btrim(coalesce(p_source_ref, ''))), '')    is not null then 'ref'
    when nullif(upper(btrim(coalesce(p_rep, ''))), '')           is not null then 'rep'
    when coalesce(nullif(upper(btrim(coalesce(p_store_address, ''))), ''),
                  nullif(upper(btrim(coalesce(p_store_code, ''))), '')) is not null then 'store'
    else 'none'
  end
$$;

create or replace function commcalc.flag_key_material(
  p_period text, p_month int, p_year int, p_flag_type text, p_source text,
  p_imei text, p_mdn text, p_subscriber_id text, p_source_ref text,
  p_rep text, p_store_address text, p_store_code text)
returns text
language sql
immutable
set search_path = ''
as $$
  select 'v1|'
      || commcalc.flag_period_canon(p_period, p_month, p_year) || '|'
      || upper(btrim(coalesce(p_flag_type, ''))) || '|'
      || lower(btrim(coalesce(p_source, ''))) || '|'
      || commcalc.flag_key_ident(p_imei, p_mdn, p_subscriber_id, p_source_ref) || '|'
      || upper(btrim(coalesce(p_rep, ''))) || '|'
      || case
           when commcalc.flag_key_ident(p_imei, p_mdn, p_subscriber_id, p_source_ref) <> '' then ''
           else coalesce(nullif(upper(btrim(coalesce(p_store_address, ''))), ''),
                         upper(btrim(coalesce(p_store_code, ''))))
         end
$$;

comment on function commcalc.flag_key_material(text, int, int, text, text, text, text, text, text, text, text, text) is
  'The identity material behind commcalc.flags.flag_key (mig 287). Mirrored byte-for-byte by '
  'flag_persist._material() in Python; harness_flag_review_persistence.py section E asserts they agree.';

-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- 3. THE ADDITIVE MERGE  — insert what is missing, refresh what exists, never delete
-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- Set-based on purpose (CLAUDE.md: aggregate in Postgres, not Python). A 7,500-flag recalculation is
-- ~15 calls of two statements each instead of ~7,500 PostgREST round-trips — and a steady-state daily
-- DLAR sweep, where nothing has changed, still writes only the run stamp.
--
-- The UPDATE arm NEVER references reviewed_by, reviewed_at or action_taken. That omission IS the
-- feature; do not "tidy" it by adding them to the set list.
create or replace function commcalc.flags_sync_batch(p_org_id uuid, p_run_id uuid, p_rows jsonb)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_updated int := 0;
  v_inserted int := 0;
  v_reopened int := 0;
begin
  if p_org_id is null then
    raise exception 'flags_sync_batch: p_org_id is required (multi-tenant rule)';
  end if;
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) = 0 then
    return jsonb_build_object('updated', 0, 'inserted', 0, 'reopened', 0);
  end if;

  -- how many of the rows about to be refreshed are currently retired (for the caller's report)
  select count(*) into v_reopened
    from commcalc.flags f
    join jsonb_to_recordset(p_rows) as r(flag_key text) on r.flag_key = f.flag_key
   where f.org_id = p_org_id
     and coalesce(f.status, 'open') <> 'open';

  -- ── refresh existing ──────────────────────────────────────────────────────────────────────────
  with r as (
    select * from jsonb_to_recordset(p_rows) as x(
      flag_key text, key_basis text, flag_type text, source text, severity text,
      store_address text, store_code text, epay_salesperson text,
      mdn text, imei text, subscriber_id text, source_ref text,
      amount numeric, description text, coaching_note text,
      days_active int, phone_model text, customer_plan text, rebate_lost numeric,
      transaction_date text, activation_date text)
  )
  update commcalc.flags f
     set severity         = r.severity,
         amount           = r.amount,
         description      = r.description,
         coaching_note    = r.coaching_note,
         days_active      = r.days_active,
         phone_model      = r.phone_model,
         customer_plan    = r.customer_plan,
         rebate_lost      = r.rebate_lost,
         transaction_date = r.transaction_date,
         activation_date  = r.activation_date,
         store_address    = r.store_address,
         -- never UN-route a flag: a run that cannot resolve the store keeps the value that could
         store_code       = coalesce(nullif(btrim(r.store_code), ''), f.store_code),
         subscriber_id    = coalesce(nullif(btrim(r.subscriber_id), ''), f.subscriber_id),
         source_ref       = coalesce(nullif(btrim(r.source_ref), ''), f.source_ref),
         key_basis        = r.key_basis,
         status           = 'open',
         resolved_at      = null,
         resolved_reason  = null,
         last_run_id      = p_run_id,
         last_seen_at     = now()
    from r
   where f.org_id = p_org_id
     and f.flag_key = r.flag_key;
  get diagnostics v_updated = row_count;

  -- ── insert what is missing ────────────────────────────────────────────────────────────────────
  -- `distinct on (flag_key)` is belt-and-braces: assign_keys() already de-collides inside a batch.
  with r as (
    select distinct on (x.flag_key) x.* from jsonb_to_recordset(p_rows) as x(
      flag_key text, key_basis text, period text, period_month int, period_year int,
      flag_type text, source text, severity text,
      store_address text, store_code text, epay_salesperson text,
      mdn text, imei text, subscriber_id text, source_ref text,
      amount numeric, description text, coaching_note text,
      days_active int, phone_model text, customer_plan text, rebate_lost numeric,
      transaction_date text, activation_date text)
    order by x.flag_key
  )
  insert into commcalc.flags (
      org_id, period, period_month, period_year, flag_type, source, severity,
      store_address, store_code, epay_salesperson, mdn, imei, subscriber_id, source_ref,
      amount, description, coaching_note, days_active, phone_model, customer_plan, rebate_lost,
      transaction_date, activation_date, flag_key, key_basis, status, last_run_id, last_seen_at)
  select p_org_id, r.period, r.period_month, r.period_year, r.flag_type, r.source, r.severity,
         r.store_address, r.store_code, r.epay_salesperson, r.mdn, r.imei, r.subscriber_id,
         r.source_ref, r.amount, r.description, r.coaching_note, r.days_active, r.phone_model,
         r.customer_plan, r.rebate_lost, r.transaction_date, r.activation_date,
         r.flag_key, r.key_basis, 'open', p_run_id, now()
    from r
   where r.flag_key is not null
     and not exists (select 1 from commcalc.flags f
                      where f.org_id = p_org_id and f.flag_key = r.flag_key);
  get diagnostics v_inserted = row_count;

  return jsonb_build_object('updated', v_updated, 'inserted', v_inserted, 'reopened', v_reopened);
end;
$$;

comment on function commcalc.flags_sync_batch(uuid, uuid, jsonb) is
  'ADDITIVE flag merge (mig 287, owner 2026-08-08 "DM review should not be erased and teh new data '
  'should only add the missing data if any"). Inserts missing flags, refreshes the display/amount '
  'columns of existing ones, and NEVER touches reviewed_by / reviewed_at / action_taken.';

-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- 4. RETIRE THE STALE  — the other half of the owner's rule
-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- Scoped to the SOURCES the calling writer owns, so a commcalc recalculation can never retire an
-- asset / payables / closing / account flag it did not produce. (Today's wholesale DELETE does exactly
-- that — it wipes every module's flags for the period. This is strictly safer.)
create or replace function commcalc.flags_resolve_stale(
  p_org_id uuid, p_run_id uuid, p_periods text[], p_sources text[], p_reason text)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_resolved int := 0;
  v_superseded int := 0;
begin
  if p_org_id is null or p_run_id is null then
    raise exception 'flags_resolve_stale: p_org_id and p_run_id are required';
  end if;
  if p_periods is null or array_length(p_periods, 1) is null
     or p_sources is null or array_length(p_sources, 1) is null then
    return jsonb_build_object('resolved', 0, 'superseded', 0);
  end if;

  with upd as (
    update commcalc.flags f
       set status = case when f.last_run_id is null then 'superseded' else 'resolved' end,
           resolved_at = now(),
           resolved_reason = coalesce(nullif(btrim(p_reason), ''),
                                      'the condition was not present in the latest recalculation')
     where f.org_id = p_org_id
       and f.period = any(p_periods)
       and f.source = any(p_sources)
       and coalesce(f.status, 'open') = 'open'
       and f.last_run_id is distinct from p_run_id
    returning f.status
  )
  select count(*) filter (where status = 'resolved'),
         count(*) filter (where status = 'superseded')
    into v_resolved, v_superseded
    from upd;

  return jsonb_build_object('resolved', coalesce(v_resolved, 0),
                            'superseded', coalesce(v_superseded, 0));
end;
$$;

comment on function commcalc.flags_resolve_stale(uuid, uuid, text[], text[], text) is
  'Retire flags the latest run no longer produces — status only, NEVER a delete, so review state and '
  'the audit trail survive (mig 287). Scoped to the calling writer''s own `source` values.';

-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- 5. BACKFILL — idempotent, only touches rows with no key yet
-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- Gives today's rows the SAME key the write path will compute on the next run wherever an imei or an
-- mdn is present, so those flags are ADOPTED by the first additive run instead of being retired and
-- re-created. Rows with no identifier at all (the 17,662 MI rows with no MDN, no IMEI, blank plan and
-- $0 MRC) get an ordinal-only key and key_basis='none': they WILL be superseded once, on the first
-- additive run, when the write path finally carries their subscriber_id. That costs nothing today —
-- `select count(*) from commcalc.flags where reviewed_by is not null` is 0 — and it never repeats.
with base as (
  select f.id,
         f.org_id,
         commcalc.flag_key_material(f.period, f.period_month, f.period_year, f.flag_type, f.source,
                                    f.imei, f.mdn, f.subscriber_id, f.source_ref,
                                    f.epay_salesperson, f.store_address, f.store_code) as mat,
         commcalc.flag_key_basis(f.imei, f.mdn, f.subscriber_id, f.source_ref,
                                 f.epay_salesperson, f.store_address, f.store_code) as basis
    from commcalc.flags f
   where f.flag_key is null
), ord as (
  select id, org_id, mat, basis,
         row_number() over (partition by org_id, mat order by id) as rn
    from base
)
update commcalc.flags f
   set flag_key = md5(o.mat || '#' || o.rn::text),
       key_basis = o.basis
  from ord o
 where f.id = o.id;

update commcalc.flags set status = 'open' where status is null;

-- NOTE: no GRANT to anon/authenticated (contract §5). All access is via the backend service role.
