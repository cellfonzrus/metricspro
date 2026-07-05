-- 098_appeal_recovery.sql — Denied-Appeal Commission Recovery / Claw-back
-- A denied commission appeal (Missing 1st MRC / Failed Activation / …) is RECOVERABLE when the line
-- eventually paid or activated AFTER the denial — then the carrier owes the commission, and there is a
-- limited window (default 45d) to claim it back. This module scans denied appeals (asset_ledger appeal
-- categories), looks for later payment / active-status evidence on that MDN/IMEI (ePay raw_payment_detail
-- + raw_mi + raw_sales), buckets each device, and generates a WEEKLY claim (60d look-back) with rebuttals.
-- Idempotent — safe to re-run. SAP doctrine: window/look-back/evidence/categories/source are all config.

-- ── Per-org config (all user-editable) ─────────────────────────────────────────────────────────────
create table if not exists commcalc.appeal_recovery_config (
  org_id uuid primary key,
  clawback_window_days int not null default 45,   -- days from denial we can still claim
  lookback_days int not null default 60,          -- the weekly claim's look-back window
  evidence_mode text not null default 'payment_or_active',  -- payment_or_active | payment_only | any
  match_mdn boolean not null default true,        -- match a later payment by phone number
  match_imei boolean not null default true,       -- …and/or by IMEI/serial
  recoverable_categories text[] not null default '{}',  -- empty = ALL appeal categories
  weekly_day_of_week int not null default 1,      -- 0=Sun … 6=Sat (1=Mon)
  weekly_hour int not null default 8,
  enabled boolean not null default true,
  recipients jsonb not null default '[]'::jsonb,  -- [{name,email,whatsapp}] for the weekly claim
  payment_source jsonb not null default '{}'::jsonb,  -- configurable confirmation-source override
  next_run_at timestamptz,
  last_run_at timestamptz,
  updated_at timestamptz not null default now()
);

-- ── Materialized recovery ledger (rebuilt by POST /recovery/rebuild) ───────────────────────────────
create table if not exists commcalc.appeal_recovery (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  imei text, mdn text, store text, market text, device_model text,
  category text,                    -- the denial reason (asset_ledger.category)
  denied_date date,
  owed_amount numeric,
  paid_later boolean not null default false,
  evidence jsonb,                   -- {source,type,date,amount,detail} of the later payment/activation
  status text not null default 'not_recoverable',  -- recoverable | expired | not_recoverable | needs_data
  claim_id uuid,                    -- set when rolled into a weekly claim batch
  checked_at timestamptz not null default now()
);
create index if not exists appeal_recovery_org_status on commcalc.appeal_recovery (org_id, status);
create index if not exists appeal_recovery_org_imei on commcalc.appeal_recovery (org_id, imei);

-- ── Weekly claim batches (the carrier submission) ──────────────────────────────────────────────────
create table if not exists commcalc.appeal_claim (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  generated_at timestamptz,
  period_label text,                -- e.g. "week of 2026-07-06 (60d look-back)"
  lookback_days int,
  device_count int,
  total_amount numeric,
  status text not null default 'draft',  -- draft | submitted | paid | rejected
  form_ref text,
  notes text,
  created_at timestamptz not null default now()
);
create index if not exists appeal_claim_org on commcalc.appeal_claim (org_id, created_at desc);

insert into commcalc.appeal_recovery_config (org_id)
values ('00000000-0000-0000-0000-000000000001')
on conflict (org_id) do nothing;
