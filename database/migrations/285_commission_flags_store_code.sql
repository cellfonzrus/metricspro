-- 285_commission_flags_store_code.sql
-- mod-commission · band 200–299 · additive + idempotent + safe to re-run
--
-- WHY
-- ───
-- Owner directive 2026-08-07: "all flags need to be fed thru the dm, so yes route it thru the dm and
-- then visible to the scoped user." A flag can only reach a district manager if the span filter can
-- match it. `commcalc.flags` carries only `store_address` — a free-text POS/report spelling — and
-- 27,428 of the house org's 31,033 rows carry it BLANK, so they match no span keyset at all and reach
-- NO manager. Owner ruling 2026-08-08 ("flags - go with option a"): RESOLVE THE STORE ON WRITE into a
-- real `store_code` column. (Option (b), resolving 31k rows on every page load, was rejected.)
--
-- WHAT THIS DOES — three things, none of which touches a payout number.
--   1. `commcalc.flags.store_code text` — the RESOLVED store, NULL when unresolvable.
--   2. `commcalc.flag_store_code_for(org, key)` — the resolver. It is deliberately the SPAN KEYSET's
--      vocabulary and NOTHING ELSE (see the note below).
--   3. A BEFORE INSERT trigger that fills `store_code` when the writer did not.
--
-- WHY A TRIGGER AND NOT JUST APPLICATION CODE
-- ───────────────────────────────────────────
-- `commcalc.flags` is written by FIVE modules with FIVE different owners: commcalc (this module),
-- asset, account, payables and closing. Under the agent contract mod-commission may not edit the other
-- four trees, so a Python-only fix would leave every asset/appeal/RMA/recon/ops flag permanently
-- store_code-less and therefore permanently unroutable. The invariant belongs to the TABLE, which is in
-- this module's schema and this module's migration band. Application code still stamps the column
-- explicitly (visible + unit-testable, and it can use identifiers the trigger cannot see); the trigger
-- only fires when the value is still NULL, so a stamped row passes through untouched.
--
-- RESOLVER VOCABULARY — the KEYSET's, not store-unmatched's
-- ────────────────────────────────────────────────────────
-- `GET /commcalc/store-unmatched` resolves store strings with LEADING-NUMBER matching; the span keyset
-- (`app.core.scope.widen_codes_to_keys`) does a raw upper/trim string compare over
-- {store_code} ∪ {store_mapping.store_address} ∪ {storeops.stores.address} ∪ {store_aliases.alias}.
-- The two disagree. This resolver follows the KEYSET, exactly, with no fuzzy step — because its whole
-- purpose is to produce a value that the keyset will match. A spelling the org has not recorded stays
-- UNRESOLVED (NULL) rather than being guessed at: mis-routing a flag to the wrong district manager is
-- strictly worse than leaving it in the admin-visible unrouted queue.
--
-- Alias rows are honoured only when their `store_code` is a REAL store (present in store_mapping or
-- storeops.stores), the same validation `_store_maps()` applies, so a stale/typo alias code can never
-- hijack resolution.
--
-- 💰 MOVES NO MONEY. `store_code` is a new, additive, VISIBILITY-ONLY column. Nothing reads it to pay
-- anyone; no existing column (including `store_address`) is written, cleared or reinterpreted; no
-- classifier, rate, tier, plan or payout basis is touched. The span filter that consumes it matches on
-- store_code OR store_address, so it is a strict SUPERSET of today's visibility — it can only reveal a
-- row that was wrongly hidden, never hide one.
--
-- MULTI-TENANT: every lookup is filtered on the caller-supplied `p_org_id`, and the trigger passes the
-- row's OWN `org_id`. A store from another tenant is unreachable by construction.

-- ── 1. the column ────────────────────────────────────────────────────────────────────────────────
alter table commcalc.flags add column if not exists store_code text;

comment on column commcalc.flags.store_code is
  'RESOLVED store_code for span/DM routing (mig 285). NULL = the store string could not be resolved '
  'against this org''s store_mapping / storeops.stores / store_aliases vocabulary; such a row stays '
  'admin-visible in the unrouted queue and is never silently dropped. Visibility only — pays nothing.';

-- The span filter reads (org_id, period) and matches store_code; the unrouted queue reads
-- (org_id, period) where store_code is null. One composite index serves both.
create index if not exists flags_org_period_store_code_idx
  on commcalc.flags (org_id, period, store_code);

-- ── 2. the resolver ──────────────────────────────────────────────────────────────────────────────
-- Priority, highest first:
--   1  the key already IS a store_code (store_mapping, then storeops.stores)
--   2  an EXPLICIT store_aliases synonym (the Store-Matching UI's source of truth)
--   3  commcalc.store_mapping.store_address
--   4  storeops.stores.address
-- Ties inside a priority resolve by store_code so the answer is deterministic.
create or replace function commcalc.flag_store_code_for(p_org_id uuid, p_key text)
returns text
language sql
stable
set search_path = ''
as $$
  select c.store_code
  from (
    select 1 as pri, sm.store_code
      from commcalc.store_mapping sm
     where sm.org_id = p_org_id
       and coalesce(btrim(sm.store_code), '') <> ''
       and upper(btrim(sm.store_code)) = upper(btrim(coalesce(p_key, '')))
    union all
    select 1, so.store_code
      from storeops.stores so
     where so.org_id = p_org_id
       and coalesce(btrim(so.store_code), '') <> ''
       and upper(btrim(so.store_code)) = upper(btrim(coalesce(p_key, '')))
    union all
    select 2, sa.store_code
      from commcalc.store_aliases sa
     where sa.org_id = p_org_id
       and coalesce(btrim(sa.store_code), '') <> ''
       and upper(btrim(sa.alias)) = upper(btrim(coalesce(p_key, '')))
       and (exists (select 1 from commcalc.store_mapping m
                     where m.org_id = p_org_id
                       and upper(btrim(coalesce(m.store_code, ''))) = upper(btrim(sa.store_code)))
         or exists (select 1 from storeops.stores t
                     where t.org_id = p_org_id
                       and upper(btrim(coalesce(t.store_code, ''))) = upper(btrim(sa.store_code))))
    union all
    select 3, sm.store_code
      from commcalc.store_mapping sm
     where sm.org_id = p_org_id
       and coalesce(btrim(sm.store_code), '') <> ''
       and coalesce(btrim(sm.store_address), '') <> ''
       and upper(btrim(sm.store_address)) = upper(btrim(coalesce(p_key, '')))
    union all
    select 4, so.store_code
      from storeops.stores so
     where so.org_id = p_org_id
       and coalesce(btrim(so.store_code), '') <> ''
       and coalesce(btrim(so.address), '') <> ''
       and upper(btrim(so.address)) = upper(btrim(coalesce(p_key, '')))
  ) c
  where btrim(coalesce(p_key, '')) <> ''
  order by c.pri, c.store_code
  limit 1
$$;

comment on function commcalc.flag_store_code_for(uuid, text) is
  'Resolve a flag''s store string to a store_code using EXACTLY the span keyset''s vocabulary '
  '(store_code / store_aliases.alias / store_mapping.store_address / storeops.stores.address, upper+trim, '
  'no fuzzy or leading-number step). NULL when the org has not recorded that spelling. mig 285.';

-- ── 3. the safety-net trigger ────────────────────────────────────────────────────────────────────
-- Fires ONLY when the writer left store_code NULL/blank, so an application-stamped value always wins.
-- Wrapped in an exception block: a resolver failure must degrade to NULL (unrouted, admin-visible),
-- never abort a flag INSERT and take a recalculation down with it (contract §5).
create or replace function commcalc.flags_fill_store_code()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.store_code is null or btrim(new.store_code) = '' then
    begin
      new.store_code := commcalc.flag_store_code_for(new.org_id, new.store_address);
    exception when others then
      new.store_code := null;
    end;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_flags_fill_store_code on commcalc.flags;
create trigger trg_flags_fill_store_code
  before insert on commcalc.flags
  for each row
  execute function commcalc.flags_fill_store_code();

-- NOTE: no GRANT to anon/authenticated (contract §5). All access is via the backend service role.
