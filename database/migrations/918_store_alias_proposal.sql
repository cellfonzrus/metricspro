-- 918_store_alias_proposal.sql   (storeops schema; SSOT Phase 1 — twin staging, follows 916/917)
--
-- The ONE irreducibly human decision in the SSOT design (blueprint Part 2.2 + Owner Decision #1):
-- whether two carrier-variant / twin codes for one street address (`B-1115`/`T-1115`,
-- `957`/`LUX-NY-PENN`, `1800 Great Neck Rd`/`Road`) are ONE physical store (same entity_id, the other
-- code becomes an alias) or TWO distinct stores that share an address (two entity_ids).
--
-- THE BACKFILL MUST NOT AUTO-MERGE. It only PROPOSES the pairing — by the exact identical-store_address
-- 1:1 join migration 511 already proved for the Luxelink twins — and STAGES it here for the owner to
-- confirm. Nothing in this table is attached to any entity; a resolver never reads it. It is a review
-- queue, not a mapping. Confirming a proposal is a deliberate, later, owner-approved step (Phase 2+).
--
-- Additive + idempotent, single-line-safe. RLS enabled + GRANT ALL to service_role. No money touched.
--
-- REVERT (paste and run to undo):
--   drop table if exists storeops.store_alias_proposal;
--   notify pgrst, 'reload schema';

create table if not exists storeops.store_alias_proposal (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  -- 'carrier_twin' (B-/T- or LUX-* sharing an address) | 'address_variant' (Rd/Road spelling drift)
  proposal_kind  text not null,
  -- the value that WOULD become an alias, and the entity it would attach to if confirmed
  alias_kind     text not null,             -- carrier_code | address | sales_file_spelling
  alias_value    text not null,
  entity_id      uuid not null,             -- the entity the 1:1 address join points the alias at
  -- the two sides of the pairing, verbatim, so a human can eyeball the evidence
  primary_code   text,                      -- the canonical (kept) store_code for the entity
  twin_code      text,                      -- the other code sharing the address (the proposed alias)
  shared_address text,                       -- the store_address both sides matched on (the join key)
  source         text,                      -- which seed pass staged this
  status         text not null default 'proposed'
                   check (status in ('proposed', 'confirmed', 'rejected')),
  decided_by     text,
  decided_at     timestamptz,
  note           text,
  created_at     timestamptz default now()
);

-- One proposal per (org, alias_kind, spelling) — re-running the seed never stacks duplicates.
create unique index if not exists store_alias_proposal_uq
  on storeops.store_alias_proposal (org_id, alias_kind, lower(trim(alias_value)));
create index if not exists store_alias_proposal_status_idx
  on storeops.store_alias_proposal (org_id, status, created_at desc);

alter table storeops.store_alias_proposal enable row level security;
grant all on storeops.store_alias_proposal to service_role;

notify pgrst, 'reload schema';

select 'Migration 918 complete — storeops.store_alias_proposal (twins STAGED, never auto-merged)' as status;
