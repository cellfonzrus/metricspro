-- 711_core_auth_2fa.sql — two-factor authentication: per-user channels/phone + per-tenant policy +
-- "remember this device" markers.
--
-- WHY: after a password sign-in, a tenant/user that requires 2FA gets an OTP (email/WhatsApp, SMS-ready)
-- and must verify before reaching data. A per-tenant policy governs off/optional/required (default OFF
-- for every existing tenant → NO lockout on deploy). Enforcement is additive in tenant_middleware and
-- gated behind BOTH the per-tenant policy AND the global break-glass env TWOFA_ENFORCE.
--
-- SAFE: additive + idempotent. New columns default to NULL/false; a missing table/column is caught by
-- best-effort reads → until this runs, 2FA is entirely inert (no user has a phone/channels, no tenant
-- has a policy, twofa_device is unread) and enforcement is a no-op.

-- Per-user 2FA config (a login's row per tenant, but phone/channels are login-level so kept per row and
-- resolved for the active membership).
alter table storeops.app_users add column if not exists phone            text;
alter table storeops.app_users add column if not exists phone_verified   boolean not null default false;
alter table storeops.app_users add column if not exists twofa_channels   jsonb;   -- e.g. ["email"] or ["email","whatsapp"]
alter table storeops.app_users add column if not exists twofa_enabled    boolean not null default false;

-- Per-tenant 2FA policy (RULE TWO: config, never hard-coded). Shape:
--   {"mode":"off"|"optional"|"required", "channels":["email","whatsapp"], "required_roles":[...]}
-- NULL / absent = OFF (the safe default for every existing tenant).
alter table storeops.tenants add column if not exists twofa_policy jsonb;

comment on column storeops.tenants.twofa_policy is
  '2FA policy (auth-hardening 2026-07-17): {mode:off|optional|required, channels:[...], required_roles:[...]}. '
  'NULL = off (default for existing tenants; no deploy lockout).';

-- "Remember this device 30 days" markers. The middleware verifies the STATELESS signed x-2fa-token
-- (no DB read); this table is the audit/revocation surface for issued device sessions.
create table if not exists core.twofa_device (
  id          uuid primary key default gen_random_uuid(),
  auth_id     uuid not null,
  org_id      uuid,
  device_id   text not null,                          -- opaque client device id
  label       text,                                   -- optional UA/label for the audit UI
  expires_at  timestamptz not null,
  revoked_at  timestamptz,
  created_at  timestamptz not null default now()
);
create index if not exists twofa_device_authid on core.twofa_device (auth_id, created_at desc);

alter table core.twofa_device enable row level security;
do $$ begin
  create policy open_all on core.twofa_device for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on core.twofa_device to anon, authenticated, service_role;

notify pgrst, 'reload schema';
select '711 complete — 2FA: app_users phone/channels + tenants.twofa_policy + core.twofa_device' as status;
