-- 710_core_auth_otp.sql — one-time codes for password reset · 2FA sign-in · phone verification.
--
-- WHY: self-serve "forgot password" and email/WhatsApp 2FA need short-lived, hashed, attempt-capped,
-- rate-limited codes. The code itself is NEVER stored — only code_hash = HMAC-SHA256(email:code) under
-- a server pepper — so a DB read alone can't brute a 6-digit code offline. All lifecycle logic (expiry,
-- attempts, rate-limit) is decided by the pure helpers in core/auth_security.py.
--
-- ZERO ENUMERATION: a row is created for a reset ONLY when the email resolves to an account, but the
-- endpoint's RESPONSE is identical (and same-timing) whether or not a row was created — the caller
-- never learns from the API whether an account exists.
--
-- SAFE: additive + idempotent. One brand-new table in the already-PostgREST-exposed `core` schema. Every
-- backend read/write is best-effort try/except → until this runs, forgot-password / 2FA return a clean
-- "temporarily unavailable" (503-style), never a stack trace, and no other page is affected.

create table if not exists core.auth_otp (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid,                                  -- tenant context where one applies (reset/2fa); nullable
  email        text not null,                         -- lower-cased subject email (2fa/reset key)
  auth_id      uuid,                                  -- the auth account, when known (2fa/phone-verify)
  purpose      text not null,                         -- 'reset' | '2fa' | 'phone_verify'
  channel      text not null default 'email',         -- 'email' | 'whatsapp' | 'sms'
  code_hash    text not null,                         -- HMAC-SHA256(email:code) under the server pepper
  dest         text,                                  -- masked destination shown to the UI (masked email/phone)
  attempts     int  not null default 0,               -- verify attempts so far
  max_attempts int  not null default 5,
  expires_at   timestamptz not null,                  -- ~10 min TTL (set by the backend)
  consumed_at  timestamptz,                           -- set when a code is successfully used (single-use)
  request_ip   text,                                  -- issuing IP (rate-limit context; not shown)
  created_at   timestamptz not null default now()
);

-- Hot path: "the newest live code for (email, purpose)" + issue-side rate-limit window count.
create index if not exists auth_otp_email_purpose on core.auth_otp (lower(email), purpose, created_at desc);
create index if not exists auth_otp_authid on core.auth_otp (auth_id, purpose, created_at desc);
create index if not exists auth_otp_created on core.auth_otp (created_at);

-- RLS: open_all (backend uses the service key + is the real guard; matches core.auth_event /
-- failure_log / job_run). NEVER read by the anon client directly — only the token-verified backend,
-- which scopes reads to the caller's own identity / email.
alter table core.auth_otp enable row level security;
do $$ begin
  create policy open_all on core.auth_otp for all to anon, authenticated using (true) with check (true);
exception when others then null; end $$;
grant all on core.auth_otp to anon, authenticated, service_role;

notify pgrst, 'reload schema';
select '710 complete — core.auth_otp (reset / 2fa / phone-verify one-time codes)' as status;
