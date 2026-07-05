-- 097_remediation.sql — Helpdesk Auto-Remediation Agent (Phase 1 MVP)
-- An AI ops agent that, for the DATA/config issue class, proposes a fix from a WHITELISTED playbook
-- catalog, sends the assignee an "issue + one-line fix + dry-run preview" with a signed magic-link
-- Approve/Reject, and on Approve executes that ONE bounded playbook + reports back. Code-class issues
-- are diagnosed + escalated, never auto-patched. Idempotent — safe to re-run.

-- ── Playbook catalog (SAP config): each row = one whitelisted, bounded action ──────────────────────
create table if not exists commcalc.remediation_playbook (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  key text not null,                        -- stable action key; must match a function in playbooks.py
  name text not null,
  description text,
  risk_level text not null default 'low',   -- low | medium | high
  requires_approval boolean not null default true,
  auto_approve boolean not null default false,  -- future: skip the human gate for low-risk actions
  enabled boolean not null default true,
  params_schema jsonb not null default '{}'::jsonb,   -- human-doc of expected params
  created_at timestamptz not null default now(),
  unique (org_id, key)
);

-- ── Every proposed / approved / executed remediation (full audit trail) ────────────────────────────
create table if not exists commcalc.remediation_request (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  playbook_key text,
  title text,
  issue text,                               -- the raw reported issue
  diagnosis text,                           -- AI (or manual) diagnosis
  proposed_action text,                     -- human-readable one-liner shown for approval
  params jsonb not null default '{}'::jsonb,-- bounded action params
  preview text,                             -- dry-run preview (no mutation) shown for approval
  issue_class text not null default 'data', -- data | code  (code → escalate, never auto-fix)
  status text not null default 'proposed',  -- proposed|awaiting_approval|approved|rejected|executed|failed|escalated|expired
  approval_token text,                      -- unguessable magic-link token (approve/reject)
  assignee_contact jsonb not null default '{}'::jsonb,  -- {name,email,whatsapp}
  source text not null default 'manual',    -- manual | helpdesk | health
  requested_by text,
  result jsonb,
  error text,
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  decided_by text,
  executed_at timestamptz
);
create index if not exists remediation_request_org_status
  on commcalc.remediation_request (org_id, status, created_at desc);

-- ── Seed the starter catalog for the house org ─────────────────────────────────────────────────────
-- dedupe_timeoff + add_store_alias are IMPLEMENTED in playbooks.py (enabled). recalc_period +
-- rerun_sweep are on the roadmap → seeded DISABLED so the catalog shows the vision but they can't run
-- until wired. A new tenant seeds its own via seed_tenant_defaults / the catalog admin UI.
insert into commcalc.remediation_playbook (org_id, key, name, description, risk_level, enabled, params_schema) values
 ('00000000-0000-0000-0000-000000000001','dedupe_timeoff',
    'Fix voided time-off blocking scheduling',
    'Deny a duplicate approved/pending time-off row that survived a void and still blocks scheduling the rep.',
    'low', true,  '{"employee_id":"optional","date":"optional YYYY-MM-DD"}'::jsonb),
 ('00000000-0000-0000-0000-000000000001','add_store_alias',
    'Map a store spelling to a store code',
    'Add a store alias so a sales-file store spelling attaches to the right store (Daily Targets / P&L).',
    'low', true,  '{"alias":"required","store_code":"required"}'::jsonb),
 ('00000000-0000-0000-0000-000000000001','recalc_period',
    'Recalculate commissions for a period',
    'Re-run the commission calculation for a month (roadmap — not yet executable via the agent).',
    'medium', false, '{"period":"required e.g. June 2026"}'::jsonb),
 ('00000000-0000-0000-0000-000000000001','rerun_sweep',
    'Re-run a data sweep',
    'Re-run a connector / email sweep to re-ingest data (roadmap — not yet executable via the agent).',
    'medium', false, '{"kind":"required e.g. email"}'::jsonb)
on conflict (org_id, key) do nothing;
