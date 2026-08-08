-- 286_commission_flags_store_code_backfill.sql
-- mod-commission · band 200–299 · additive + idempotent + safe to re-run
-- REQUIRES 285_commission_flags_store_code.sql (the column + the resolver function).
--
-- Backfills `commcalc.flags.store_code` for rows written BEFORE the write-side stamping existed.
-- Every statement is guarded by `store_code is null`, so re-running is a no-op and a value already
-- resolved (by the app or by an earlier run) is never overwritten.
--
-- 💰 MOVES NO MONEY. It writes ONE new visibility-only column. It does not touch `store_address`,
-- `amount`, `rebate_lost`, `reviewed_by`, `reviewed_at` or any other field, it inserts and deletes
-- nothing, and no payout, rate, plan, tier or classifier reads `store_code`.
--
-- ALL ORGS. No org_id literal appears anywhere below — every join carries org_id through, so each
-- tenant is resolved against its OWN store vocabulary and cross-tenant resolution is impossible.

-- ── STEP 1 · the string chain (the span keyset's own vocabulary) ─────────────────────────────────
-- store_address → store_code via store_mapping / storeops.stores / store_aliases. Rows whose spelling
-- the org has never recorded stay NULL on purpose — see the unresolved-spelling report at the bottom.
-- `as materialized` on EVERY CTE below is load-bearing, not style. Inlined, the planner turns these
-- joins into a nested loop that re-aggregates commcalc.raw_mi per probe and the statement dies on
-- Supabase's statement timeout (measured: >120 s inlined vs 0.94 s materialized).
with cand as materialized (
  select f.id,
         commcalc.flag_store_code_for(f.org_id, f.store_address) as code
    from commcalc.flags f
   where f.store_code is null
     and coalesce(btrim(f.store_address), '') <> ''
)
update commcalc.flags f
   set store_code = c.code
  from cand c
 where c.id = f.id
   and c.code is not null;

-- ── STEP 2 · MI-derived flags with NO store string at all ────────────────────────────────────────
-- Port-out / transfer-out / involuntary-suspension flags take their store from a SALES match on the
-- customer's MDN (`portout_flags.calc_portout_flags`), so a line sold in an earlier month — i.e. most
-- of the subscriber base — lands with a BLANK store_address and reaches nobody.
--
-- The MI row those flags are built from carries `salesforce_id`, and `commcalc.store_mapping` already
-- maps salesforce_id → store_code (26 doors, house). That is not a new matcher and not a guess: it is
-- the dealer door that owns the line, read out of the same config table the keyset uses. It was
-- validated against the rows where BOTH answers exist: of the 1,122 mi_report flags that DID get a
-- store from the sales match, the salesforce_id path agrees on 1,067 (95.1%) — and every one of the
-- disagreements is a real "sold at A, residual door B" case, not a resolution error. It independently
-- reproduces all three store_aliases rows ("3 Palisade Ave Yonkers"→B-3PL, "2778 Ephraim Ave"→B-1598,
-- "2509 Bergenline Ave Ste A"→B-2509) without ever reading store_aliases.
--
-- The SALES answer therefore stays authoritative (STEP 1 runs first and is never overwritten); this
-- only fills rows that had NOTHING.
--
-- Joined on (org_id, period_month, period_year, mdn) — NOT on `period`. `raw_sales`/`flags` store
-- 'June 2026' while other surfaces use '2026-06'; joining on the spelling silently returns zero rows
-- (the recurring period-spelling bug class). Both tables have period_month/period_year fully
-- populated, so the numeric join is spelling-proof.
--
-- AMBIGUITY IS REFUSED: an MDN that appears at two different doors in the same period (89 house rows)
-- is left NULL rather than assigned to one of them.
with sf as materialized (
  select org_id,
         upper(btrim(salesforce_id)) as sfid,
         min(btrim(store_code))      as store_code
    from commcalc.store_mapping
   where coalesce(btrim(salesforce_id), '') <> ''
     and coalesce(btrim(store_code), '') <> ''
   group by 1, 2
  having min(upper(btrim(store_code))) = max(upper(btrim(store_code)))   -- one door, one store
), mi as materialized (
  select org_id, period_month, period_year,
         replace(btrim(phone_number), '.0', '')     as mdn,
         min(upper(btrim(salesforce_id)))           as sfid
    from commcalc.raw_mi
   where coalesce(btrim(phone_number), '') <> ''
     and coalesce(btrim(salesforce_id), '') <> ''
   group by 1, 2, 3, 4
  having min(upper(btrim(salesforce_id))) = max(upper(btrim(salesforce_id)))  -- unambiguous only
), fl as materialized (
  select f.id, f.org_id, f.period_month, f.period_year,
         replace(btrim(f.mdn), '.0', '') as mdn
    from commcalc.flags f
   where f.store_code is null
     and f.source = 'mi_report'
     and coalesce(btrim(f.mdn), '') <> ''
), cand as materialized (
  select fl.id, sf.store_code
    from fl
    join mi on mi.org_id       = fl.org_id
           and mi.period_month = fl.period_month
           and mi.period_year  = fl.period_year
           and mi.mdn          = fl.mdn
    join sf on sf.org_id = fl.org_id
           and sf.sfid   = mi.sfid
)
update commcalc.flags f
   set store_code = c.store_code
  from cand c
 where c.id = f.id
   and c.store_code is not null;

-- ── WHAT IS LEFT, AND WHY (run these to see it; they change nothing) ─────────────────────────────
-- (a) rows still unrouted, by source:
--       select org_id, source, count(*) from commcalc.flags
--        where store_code is null group by 1,2 order by 3 desc;
-- (b) the store spellings the org has NOT recorded — every one of these becomes routable the moment
--     an operator adds the matching commcalc.store_aliases row through the Store-Matching UI:
--       select org_id, btrim(store_address) spelling, count(*) rows_blocked
--         from commcalc.flags
--        where store_code is null and coalesce(btrim(store_address),'') <> ''
--        group by 1,2 order by 3 desc;
-- (c) MI flags that carry NO identifier at all (no mdn, no imei, blank plan, $0 MRC) cannot be
--     resolved from what was persisted — the MI row's subscriber_id was never written to the flag.
--     They are fixed FORWARD (the write path now stamps store_code from salesforce_id at calc time);
--     until the period is next recalculated they stay in the admin-visible unrouted queue:
--       select org_id, count(*) from commcalc.flags
--        where store_code is null and source='mi_report'
--          and coalesce(btrim(mdn),'')='' and coalesce(btrim(imei),'')='' group by 1;
