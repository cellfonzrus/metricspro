-- 707_core_account_link_invite.sql — CONSENT-BASED account linking with ZERO cross-tenant disclosure.
--
-- WHY (platform-core-11, OWNER DIRECTIVE 2026-07-14): when an admin provisions a user whose email
-- ALREADY has a MetricsPro login in ANOTHER tenant, the system must NOT (a) create a second login,
-- (b) mint a mig-088 tenant-alias, or (c) silently bind a shared cross-tenant membership (the wave-4
-- default). All three either leak that the email exists elsewhere or attach a person to a tenant
-- without their consent. Instead we enter a PENDING-CONNECTION state: the admin is told the SAME thing
-- they'd see for a brand-new email (anti-enumeration), and the user themselves — after signing in with
-- their EXISTING credentials — chooses to CONNECT the new tenant onto their login (mig 706 shared
-- membership) or DISABLE the old login and take a fresh one. See core/router.py account-linking block.
--
-- ZERO DISCLOSURE (hard requirement): an invite row is addressed to an EMAIL and owned by the INVITING
-- tenant (org_id = the inviting/target tenant). The login-time lookup is keyed on the AUTHENTICATED
-- caller's OWN email and returns ONLY the inviting tenant's name — never the caller's other tenants,
-- never anything about who else the email belongs to. No response, shape, or field reveals whether an
-- email exists in any other tenant.
--
-- SAFE: additive + idempotent. Two brand-new tables in the (already PostgREST-exposed) `core` schema,
-- plus indexes. No existing table is altered. Every backend write to these tables is best-effort
-- try/except, so until this runs the account-linking flow degrades to: fresh emails keep the direct
-- create-login path (unchanged), and an email that already exists elsewhere is REFUSED a silent bind
-- (the create-login endpoint reports "invite could not be recorded — run migration 707") rather than
-- falling back to the rejected alias/shared-bind behaviour.

-- ── core.account_link_invite: one PENDING invite = (email, inviting tenant) ─────────────────────────
-- The Tenant-B admin provisioned <email> (assigned a role → an app_users row with auth_id NULL) and
-- an invite is recorded here instead of a login being minted. The user resolves it on next sign-in.
create table if not exists core.account_link_invite (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,                       -- the INVITING (target) tenant — the invite belongs to it
  email          text not null,                       -- lower-cased email the invite is addressed to
  connect_token  text not null,                       -- the access code the admin hands out; required to accept (consent)
  invited_by     text,                                -- email/role of the admin who created it (audit only)
  role           text,                                -- the role the Tenant-B app_users row was assigned (informational)
  status         text not null default 'pending',     -- 'pending' | 'accepted' | 'disabled_switch' | 'revoked' | 'expired'
  resolved_auth_id uuid,                               -- the auth account that ultimately took this tenant (accepted/disabled)
  created_at     timestamptz not null default now(),
  expires_at     timestamptz not null default (now() + interval '30 days'),
  resolved_at    timestamptz
);
-- At most ONE live invite per (email, tenant): re-provisioning the same person just refreshes it.
create unique index if not exists account_link_invite_email_org_uidx
  on core.account_link_invite (lower(email), org_id) where status = 'pending';
-- Hot path: "does this authenticated email have any pending invite" (login-time detection).
create index if not exists account_link_invite_email_status
  on core.account_link_invite (lower(email), status);
create index if not exists account_link_invite_org
  on core.account_link_invite (org_id, status);

-- ── core.auth_event: audit trail for every connect / disable / reinstate / invite action ────────────
-- Identity-level (a connect/disable spans tenants), so org_id is nullable — it records the tenant
-- CONTEXT of the action where one applies (the inviting tenant, the disabled login's tenant, ...).
create table if not exists core.auth_event (
  id          uuid primary key default gen_random_uuid(),
  event       text not null,                          -- 'invite_created' | 'connect' | 'disable_switch' | 'reinstate' | 'invite_revoked'
  email       text,                                   -- subject email (lower-cased)
  auth_id     uuid,                                   -- the auth account acted on (connected/disabled/reinstated)
  org_id      uuid,                                   -- tenant context (inviting tenant / disabled login's tenant), may be null
  actor       text,                                   -- who performed it: 'self:<email>' | 'admin:<email>' | 'super_admin:<email>'
  detail      jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists auth_event_email on core.auth_event (lower(email), created_at desc);
create index if not exists auth_event_authid on core.auth_event (auth_id, created_at desc);
create index if not exists auth_event_kind on core.auth_event (event, created_at desc);

-- RLS: open_all (backend uses the service key + is the real guard; matches core.failure_log/job_run
-- and every other module table). These tables are NEVER read by the anon client directly — only by the
-- token-verified backend, which scopes every read to the caller's own identity.
alter table core.account_link_invite enable row level security;
alter table core.auth_event enable row level security;
do $$ begin
  create policy open_all on core.account_link_invite for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
do $$ begin
  create policy open_all on core.auth_event for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on core.account_link_invite to anon, authenticated, service_role;
grant all on core.auth_event to anon, authenticated, service_role;

notify pgrst, 'reload schema';
select '707 complete — core.account_link_invite + core.auth_event (consent-based account linking)' as status;
