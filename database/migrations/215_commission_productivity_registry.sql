-- 215_commission_productivity_registry.sql  (band 200-299 · mod-commission)
-- The ONE unified per-org item registry powering BOTH the stack ranker (count_in_stack_ranker) AND the
-- performance review (count_in_review) in the Productivity module. NON-money / display-analytics; the
-- commission tie-in (perf KPI keys) is INERT until a Commission Plan references them AND the owner recalcs.
--
-- Code-default + org-override pattern (like report_pull): the module seeds its placeholder items IN CODE
-- (productivity.DEFAULT_ITEMS); this table only stores a tenant's EDITS/additions. So a brand-new tenant
-- needs ZERO seed rows and still sees the default items — no SEED_VERSION bump required.
--
-- Additive + idempotent — safe to re-run. Until this runs the module DEGRADES to the code defaults
-- (read-only registry, no persisted edits); no other page is affected.

create table if not exists commcalc.productivity_item (
    id                    uuid primary key default gen_random_uuid(),
    org_id                uuid not null,
    item_key              text not null,          -- unique per org; overrides a code default or a custom item
    label                 text,
    source_key            text,                   -- from productivity.SOURCE_CATALOG (pick-don't-type)
    standard              numeric,                -- the definable target/threshold (null ⇒ rank relative)
    standard_type         text default 'number',  -- number | dollar | percent | score
    weight                numeric not null default 1,
    count_in_stack_ranker boolean not null default true,
    count_in_review       boolean not null default false,
    enabled               boolean not null default true,
    hidden                boolean not null default false,  -- a "deleted" default is a hidden override
    is_seed_default       boolean not null default false,
    sort                  integer not null default 500,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),
    unique (org_id, item_key)
);

create index if not exists productivity_item_org_idx on commcalc.productivity_item (org_id);
