-- 1128_credential_reveal_audit.sql — WHO VIEWED A STORED CREDENTIAL (index §CORE-46, §COMMCALC-82)
--
-- OWNER, 2026-11-20, choosing how the per-store download screen should hand over each store's back
-- office password: "Show it on screen". The ask is legitimate — the back office refuses automated
-- clients (§COMMCALC-74/75), so a PERSON logs in and types the password — and it is also the first
-- time this platform hands a stored credential back to a browser. Every other read strips it.
--
-- WHAT THIS TABLE IS FOR. Not a gate: the person who can open the settings screen can already change
-- that login. It is the ANSWER to "who saw this password, and when", which is the one question that
-- becomes unanswerable the moment a credential can be displayed. The code refuses to display one
-- unless this row is written first (app/modules/core/credential_reveal.py), so an un-run migration
-- means no reveal — never a silent unrecorded one.
--
-- WHAT IT DELIBERATELY DOES NOT HOLD: the credential. Not the value, not a hash, not a prefix. The
-- row names the relation, the column, the row id, who asked and when. A table that stored the secret
-- to prove it was shown would be a second copy of every password in the platform.
--
-- Universal by construction (owner's 2026-10-30 rule): it is keyed by relation + column, read from
-- the credential registry, so it serves any tenant and any credential this platform stores — nothing
-- here names a screen, a feed, a carrier or a tenant.
--
-- Idempotent and additive. No figure in any report moves; nothing reads this table but an audit query.
--
-- REVERT:
--   drop table if exists core.credential_reveal_audit;

create schema if not exists core;

create table if not exists core.credential_reveal_audit (
    id            uuid primary key default gen_random_uuid(),
    org_id        uuid not null,
    relation      text not null,          -- the registry's relation name, e.g. 'data_source'
    "column"      text not null,          -- the credential column that was shown
    row_id        text,                   -- which row's credential; text, so any key shape fits
    revealed_to   text,                   -- who asked, as the request identified them
    context       text,                   -- which surface asked, for reading the trail later
    created_at    timestamptz not null default now()
);

create index if not exists credential_reveal_audit_org_created_idx
    on core.credential_reveal_audit (org_id, created_at desc);

create index if not exists credential_reveal_audit_org_row_idx
    on core.credential_reveal_audit (org_id, relation, row_id);

-- CORE-13: a migration grants what it creates. service_role only — the browser never reads this
-- table; the API writes it and scopes every read by org.
grant select, insert, update, delete on core.credential_reveal_audit to service_role;
