-- 946_store_lease_insurance.sql — store lease / landlord / insurance capture at store setup
-- (owner directive 2026-09-03, verbatim): "when setting up the store the tenant should be able to
-- add the landlord information, rent payment links, or ACH information, rent payment due, Site
-- contact, site contact phone number, landlord email address and phone number, current rent,
-- annual escalations, in percentage or add monthly rents, insurance information, premium due, due
-- date company, also add a place to upload the current lease and insurance COI which can be
-- downloaded at any given time." And: "by default the rents are due in the 1st week of the month,
-- should not be hard coded but defined for stores when setting up the store."
--
-- ── SHAPE (documented for the sibling finance build that computes "rents due this week /
--    recurring expenses due" per store — read these columns, do not re-derive) ──────────────────
--
-- storeops.store_lease — ONE row per (org_id, store_code). Rent-for-a-month resolution
-- (pure twin: backend/app/modules/storeops/store_lease.py `rent_for_month`, proof
-- backend/harness_store_lease.py):
--   1. `rent_schedule` (JSONB array [{"effective_from":"YYYY-MM-DD","monthly_rent":N}, ...]) —
--      the EXPLICIT per-period monthly-rent option ("or add monthly rents"): the entry with the
--      latest effective_from <= first-of-month wins.
--   2. else `current_rent` escalated by `escalation_pct` percent (compounded) once per WHOLE year
--      elapsed from `rent_effective_from` (anniversary-date arithmetic) to the first of the month.
--   3. else `current_rent` as-is. NULL current_rent + empty schedule = rent unknown (no row shows
--      in a rents-due report; never 0).
-- Rent DUE resolution (pure twin `resolve_rent_due` + `rent_due_window`):
--   store_lease.rent_due JSONB {"kind":"week"|"day","value":N}   (week 1-5 of month | day 1-31)
--   -> NULL falls back to storeops.tenants.rent_due_default (per-org config)
--   -> NULL/garbage falls back to the HOUSE default {"kind":"week","value":1} — the owner's
--      "first week of the month", shipped as a COLUMN DEFAULT + code fallback, never a hardcode
--      a tenant can't change (RULE TWO). week N = days 7N-6..min(7N, month end); day d clamps to
--      month end (day 31 in Feb = Feb 28/29).
-- Insurance recurrence: `insurance_premium` due on `insurance_premium_due`, repeating per
-- `insurance_premium_frequency` ('annual' default | 'semiannual' | 'quarterly' | 'monthly') —
-- enough for a recurring-expenses-due report without a second table.
--
-- storeops.store_document — APPEND-ONLY document versions per (org, store, kind): every upload
-- INSERTS; "current" = newest uploaded_at per (org_id, store_code, doc_kind); prior versions are
-- kept and stay downloadable. Files live in the PRIVATE Supabase storage bucket `store-docs`
-- (envelope-photo precedent, closing module) — `storage_path` is the raw private path; download is
-- an on-demand signed URL via the org-scoped, permission-gated endpoints (never a public URL).
--
-- ── SENSITIVE — ACH/banking + lease documents are money-adjacent ────────────────────────────────
-- Reads/writes are gated server-side at management level (mig-434 posture, fail-closed):
-- backend store_lease.can_see_lease — allow-list storeops.tenants.lease_visible_roles below,
-- NULL = pay_visibility.DEFAULT_VISIBLE_ROLES ("market manager and above"); scope-'all' roles and
-- the `store_lease_docs` data grant always pass. No employee-level endpoint selects these tables
-- (GET /storeops/stores is untouched — separate table, nothing to echo).
-- RLS: DELIBERATELY NOT the storeops open_all convention — RLS enabled with NO open_all policy and
-- grants to service_role only, so anon/authenticated PostgREST keys can never read ACH details or
-- document paths; the backend (service key) is the only reader, behind its gate.
--
-- MONEY: no booked number moves — capture/config only. Additive + idempotent. Run in the Supabase
-- SQL editor.

CREATE TABLE IF NOT EXISTS storeops.store_lease (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                      UUID NOT NULL,
  store_code                  TEXT NOT NULL,
  -- landlord + site contact
  landlord_name               TEXT,
  landlord_email              TEXT,
  landlord_phone              TEXT,
  site_contact_name           TEXT,
  site_contact_phone          TEXT,
  -- rent payment rails ("rent payment links, or ACH information") — ACH columns are SENSITIVE
  rent_payment_links          TEXT[],
  ach_bank_name               TEXT,
  ach_routing_number          TEXT,
  ach_account_number          TEXT,
  ach_notes                   TEXT,
  -- rent amount + escalation (percentage OR explicit schedule — see header for resolution order)
  current_rent                NUMERIC,
  rent_effective_from         DATE,          -- the date current_rent took effect (escalation anniversary basis)
  escalation_pct              NUMERIC,       -- annual escalation %, compounded on the anniversary
  rent_schedule               JSONB,         -- [{"effective_from":"YYYY-MM-DD","monthly_rent":N}, ...]
  rent_due                    JSONB,         -- {"kind":"week"|"day","value":N}; NULL = tenant default
  lease_start                 DATE,
  lease_end                   DATE,
  -- insurance
  insurance_company           TEXT,
  insurance_policy_number     TEXT,
  insurance_premium           NUMERIC,
  insurance_premium_due       DATE,
  insurance_premium_frequency TEXT NOT NULL DEFAULT 'annual'
    CHECK (insurance_premium_frequency IN ('annual', 'semiannual', 'quarterly', 'monthly')),
  insurance_notes             TEXT,
  notes                       TEXT,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by                  TEXT,
  UNIQUE (org_id, store_code)
);
CREATE INDEX IF NOT EXISTS store_lease_org ON storeops.store_lease (org_id, store_code);

CREATE TABLE IF NOT EXISTS storeops.store_document (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  store_code    TEXT NOT NULL,
  doc_kind      TEXT NOT NULL CHECK (doc_kind IN ('lease', 'insurance_coi')),
  storage_path  TEXT NOT NULL,               -- private bucket `store-docs` path; signed on demand
  file_name     TEXT,
  content_type  TEXT,
  size_bytes    BIGINT,
  uploaded_by   TEXT,
  uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS store_document_current
  ON storeops.store_document (org_id, store_code, doc_kind, uploaded_at DESC);

-- per-org config (RULE TWO — mig 434/942 tenants-column precedent)
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS rent_due_default JSONB NOT NULL DEFAULT '{"kind":"week","value":1}'::jsonb,
  ADD COLUMN IF NOT EXISTS lease_visible_roles TEXT[];

COMMENT ON TABLE storeops.store_lease IS
  'Per-store landlord / rent / insurance capture (owner 2026-09-03, mig 946). Rent-for-month + '
  'rent-due resolution: backend/app/modules/storeops/store_lease.py (rent_schedule wins, else '
  'current_rent escalated by escalation_pct per whole year since rent_effective_from). ACH columns '
  'are sensitive — reads gated management-level (can_see_lease, mig-434 posture, fail-closed).';
COMMENT ON COLUMN storeops.store_lease.rent_due IS
  'Per-store rent-due override {"kind":"week"|"day","value":N}. NULL = tenants.rent_due_default = '
  'house default first week of the month. Resolved by store_lease.resolve_rent_due; the window for '
  'a month by rent_due_window (week N = days 7N-6..7N clamped; day d clamped to month end).';
COMMENT ON COLUMN storeops.tenants.rent_due_default IS
  'Org-wide default rent due window {"kind":"week"|"day","value":N} for stores with no per-store '
  'rent_due override (owner 2026-09-03: default first week of the month, defined not hardcoded).';
COMMENT ON COLUMN storeops.tenants.lease_visible_roles IS
  'Roles allowed to read/write store lease/landlord/ACH/insurance details + documents. NULL = house '
  'default "market manager and above" (pay_visibility.DEFAULT_VISIBLE_ROLES); scope-all roles and '
  'the store_lease_docs data grant always pass. Fail-closed (store_lease.can_see_lease).';
COMMENT ON TABLE storeops.store_document IS
  'Append-only store document versions (lease / insurance_coi), private bucket store-docs. Current '
  '= newest uploaded_at per (org, store, kind); prior versions kept + downloadable via signed URL '
  '(GET /storeops/store-lease/doc-url, gated + org-scoped).';

-- RLS: sensitive tables — NO open_all policy on purpose (see header). Service role only.
DO $$
BEGIN
  EXECUTE 'ALTER TABLE storeops.store_lease ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE storeops.store_document ENABLE ROW LEVEL SECURITY';
  EXECUTE 'REVOKE ALL ON storeops.store_lease FROM anon, authenticated';
  EXECUTE 'REVOKE ALL ON storeops.store_document FROM anon, authenticated';
  EXECUTE 'GRANT ALL ON storeops.store_lease TO service_role';
  EXECUTE 'GRANT ALL ON storeops.store_document TO service_role';
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 946 complete — storeops.store_lease + store_document (append-only versions), tenants.rent_due_default (house: first week) + lease_visible_roles' AS status;

-- REVERT:
--   DROP TABLE IF EXISTS storeops.store_document;
--   DROP TABLE IF EXISTS storeops.store_lease;
--   ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS rent_due_default,
--                                DROP COLUMN IF EXISTS lease_visible_roles;
--   (Endpoints degrade: GET /storeops/store-lease returns empty lease + no documents on a pre-946
--    schema; writes surface a schema-missing error instead of writing. Uploaded files in the
--    store-docs bucket survive a table drop and would need manual bucket cleanup.)
