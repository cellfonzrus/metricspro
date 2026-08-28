-- 919_ssot_alias_backfill.sql   (storeops schema; SSOT Phase 1 — backfill/seed, follows 916/917/918)
--
-- IDEMPOTENT SEED for the unified alias tables (design blueprint Part 3b). Depends on 916/917/918.
-- Every INSERT is `ON CONFLICT DO NOTHING` on the (org_id, alias_kind, lower(trim(alias_value)))
-- unique index, so re-running inserts ZERO new rows. PURELY ADDITIVE: no reader consumes these rows
-- in Phase 1, so this changes NO computed money and NO market-filtered dollar.
--
-- Mirrors the backend seed builders in app/core/identity_backfill.py. The ONE thing this SQL does NOT
-- do is fold the asset-router MARKET_OVERRIDES Python dict or the NORMALIZED-address twin pairing
-- (Rd/Road, 26th/26TH) — those need the app's address normalizer; run `identity_backfill.seed()` (or
-- the harness-proven builder) for them. This SQL covers the exact-match seeds, which include the
-- 1115-Liberty requirement (a stores row with no store_mapping row still gets its entity_id from
-- migration 916 and its code/address aliases here) and stages carrier/LUX twins by IDENTICAL address.
--
-- Runs single-line-safe in the Supabase SQL editor. REVERT: delete the seeded rows (they all carry a
-- known `source`) — e.g.  delete from storeops.store_alias where source in
--   ('stores','store_mapping','store_aliases','store_merchant_id');
--   delete from storeops.employee_alias where source in ('employees','name_map','rep_aliases');
--   delete from storeops.store_alias_proposal where source = 'twin_pairing';

-- ══ STORE ALIASES ═════════════════════════════════════════════════════════════════════════════════
-- code + address from the stores anchor (this is what gives 1115 Liberty its aliases with no mapping row)
insert into storeops.store_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select s.org_id, 'code', s.store_code, s.entity_id, 'stores', 'seeded'
  from storeops.stores s where nullif(btrim(s.store_code), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

insert into storeops.store_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select s.org_id, 'address', s.address, s.entity_id, 'stores', 'seeded'
  from storeops.stores s where nullif(btrim(s.address), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

-- address + salesforce_id from store_mapping, matched to the entity by store_code
insert into storeops.store_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select sm.org_id, 'address', sm.store_address, s.entity_id, 'store_mapping', 'seeded'
  from commcalc.store_mapping sm
  join storeops.stores s on s.org_id = sm.org_id and upper(btrim(s.store_code)) = upper(btrim(sm.store_code))
 where nullif(btrim(sm.store_address), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

insert into storeops.store_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select sm.org_id, 'salesforce_id', sm.salesforce_id, s.entity_id, 'store_mapping', 'seeded'
  from commcalc.store_mapping sm
  join storeops.stores s on s.org_id = sm.org_id and upper(btrim(s.store_code)) = upper(btrim(sm.store_code))
 where nullif(btrim(sm.salesforce_id), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

-- sales-file spellings from commcalc.store_aliases (target code must be a REAL store — the join enforces it)
insert into storeops.store_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select sa.org_id, 'sales_file_spelling', sa.alias, s.entity_id, 'store_aliases', 'seeded'
  from commcalc.store_aliases sa
  join storeops.stores s on s.org_id = sa.org_id and upper(btrim(s.store_code)) = upper(btrim(sa.store_code))
 where nullif(btrim(sa.alias), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

-- merchant ids from storeops.store_merchant_id
insert into storeops.store_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select mi.org_id, 'merchant_id', mi.merchant_id, s.entity_id, 'store_merchant_id', 'seeded'
  from storeops.store_merchant_id mi
  join storeops.stores s on s.org_id = mi.org_id and upper(btrim(s.store_code)) = upper(btrim(mi.store_code))
 where nullif(btrim(mi.merchant_id), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

-- ══ EMPLOYEE ALIASES ══════════════════════════════════════════════════════════════════════════════
insert into storeops.employee_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select e.org_id, 'business_id', e.employee_id, e.entity_id, 'employees', 'seeded'
  from storeops.employees e where nullif(btrim(e.employee_id), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

-- numeric id, ONLY when it is not some OTHER employee's business id (collision guard)
insert into storeops.employee_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select e.org_id, 'numeric_id', e.id::text, e.entity_id, 'employees', 'seeded'
  from storeops.employees e
 where e.id is not null and e.id::text <> coalesce(btrim(e.employee_id), '')
   and not exists (select 1 from storeops.employees o
                    where o.org_id = e.org_id and btrim(o.employee_id) = e.id::text)
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

insert into storeops.employee_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select e.org_id, 'epay_login', e.epay_login, e.entity_id, 'employees', 'seeded'
  from storeops.employees e where nullif(btrim(e.epay_login), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

insert into storeops.employee_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select e.org_id, 'pos_name', e.epay_salesperson, e.entity_id, 'employees', 'seeded'
  from storeops.employees e where nullif(btrim(e.epay_salesperson), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

insert into storeops.employee_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select e.org_id, 'name_variant', e.name, e.entity_id, 'employees', 'seeded'
  from storeops.employees e where nullif(btrim(e.name), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

-- name_map: a POS salesperson / login IS a roster name → entity whose name matches storeops_name
insert into storeops.employee_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select nm.org_id, 'pos_name', nm.epay_salesperson, e.entity_id, 'name_map', 'seeded'
  from commcalc.name_map nm
  join storeops.employees e on e.org_id = nm.org_id
   and lower(btrim(e.name)) = lower(btrim(nm.storeops_name))
 where nullif(btrim(nm.epay_salesperson), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

insert into storeops.employee_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select nm.org_id, 'epay_login', nm.epay_login, e.entity_id, 'name_map', 'seeded'
  from commcalc.name_map nm
  join storeops.employees e on e.org_id = nm.org_id
   and lower(btrim(e.name)) = lower(btrim(nm.storeops_name))
 where nullif(btrim(nm.epay_login), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

-- rep_aliases: an alias name IS the canonical rep → entity whose name matches canonical
insert into storeops.employee_alias (org_id, alias_kind, alias_value, entity_id, source, confidence)
select ra.org_id, 'name_variant', ra.alias, e.entity_id, 'rep_aliases', 'seeded'
  from commcalc.rep_aliases ra
  join storeops.employees e on e.org_id = ra.org_id
   and lower(btrim(e.name)) = lower(btrim(ra.canonical))
 where nullif(btrim(ra.alias), '') is not null
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

-- ══ TWIN PROPOSALS (STAGED, NEVER MERGED) ═════════════════════════════════════════════════════════
-- Two DISTINCT store codes sharing the SAME lower(trim) address = a candidate one-physical-store pair
-- (the identical-address 1:1 join migration 511 proved). The carrier/LUX-looking code is proposed as
-- an alias of the plain code's entity. NOTHING is attached — the owner confirms in Phase 2+.
insert into storeops.store_alias_proposal
  (org_id, proposal_kind, alias_kind, alias_value, entity_id, primary_code, twin_code, shared_address, source)
select p.org_id, 'carrier_twin', 'carrier_code', p.twin_code, p.primary_entity,
       p.primary_code, p.twin_code, p.shared_address, 'twin_pairing'
from (
  select a.org_id,
         lower(btrim(a.address)) as akey,
         b.entity_id  as primary_entity,
         b.store_code as primary_code,
         a.store_code as twin_code,
         a.address    as shared_address,
         row_number() over (partition by a.org_id, lower(btrim(a.address)), a.store_code
                            order by b.store_code) as rn
    from storeops.stores a
    join storeops.stores b
      on b.org_id = a.org_id
     and lower(btrim(b.address)) = lower(btrim(a.address))
     and b.store_code <> a.store_code
   where nullif(btrim(a.address), '') is not null
     -- the twin side looks like a carrier/LUX variant; the primary (b) does not
     and (upper(a.store_code) like 'B-%' or upper(a.store_code) like 'T-%' or upper(a.store_code) like 'LUX-%')
     and not (upper(b.store_code) like 'B-%' or upper(b.store_code) like 'T-%' or upper(b.store_code) like 'LUX-%')
) p
where p.rn = 1
on conflict (org_id, alias_kind, (lower(btrim(alias_value)))) do nothing;

notify pgrst, 'reload schema';

select 'Migration 919 complete — SSOT alias tables seeded (idempotent); twins STAGED for owner review' as status;
