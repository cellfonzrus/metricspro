-- 100_hr_accounting_forward.sql — HR onboarding: review/approve + forward completed paperwork to accounting
-- Adds a CUSTOMIZABLE accounting-forward destination (per org) so HR can, once a new hire's paperwork is
-- complete + approved, forward it to the accounting office. Recipients + subject/message are config (SAP
-- doctrine: destination is DATA, not hard-coded). Per-hire forward is stamped on the onboarding profile so
-- the Completed board shows what's already gone out. Idempotent — safe to re-run.

create table if not exists storeops.hr_onboarding_settings (
  org_id               uuid primary key,
  accounting_emails    text[] not null default '{}',   -- where completed paperwork is forwarded (email)
  accounting_whatsapps text[] not null default '{}',    -- optional WhatsApp recipients (future)
  forward_subject      text,                            -- optional subject template ({name} = employee)
  forward_message      text,                            -- optional intro message in the forward email
  include_portal_link  boolean not null default true,   -- include a MetricsPro reference line
  updated_at           timestamptz not null default now()
);

-- Per-hire forward stamp (so the Completed board can show "↗ forwarded to accounting on DATE").
alter table storeops.employee_onboarding_profile
  add column if not exists accounting_forwarded_at timestamptz,
  add column if not exists accounting_forwarded_to text;
