-- 964_insurance_policy_multi_store.sql — ONE insurance policy covering MANY stores + policy config
-- (owner directive 2026-09-05, verbatim): "there should be a link to upload the insurance policy and
-- assign that policy to multiple stores as one insurance policy can cover multiple stores ... Please
-- to upload the certificate of insurance of respective stores".
--
-- ── WHY A NEW ENTITY (and what it deliberately does NOT replace) ────────────────────────────────
-- mig 946 gave every store its own lease row (storeops.store_lease) with insurance_company /
-- insurance_policy_number / insurance_premium columns, and per-store append-only documents
-- (storeops.store_document, doc_kind 'lease' | 'insurance_coi'). That shape is correct for a COI —
-- a certificate is issued PER STORE — but wrong for the POLICY itself: a BOP or workers-comp policy
-- is ONE contract with ONE premium and ONE coverage period covering N locations. Recording it N
-- times on N store_lease rows would (a) multiply one premium into N premiums for anyone summing
-- them and (b) drift the moment one store's copy is edited.
--
-- So: the POLICY becomes its own row here, stores are ATTACHED to it, and the per-store COI upload
-- from mig 946 stays exactly as it is (the owner asked for BOTH). store_lease's insurance_* columns
-- are untouched and keep their meaning + their §14 read contract
-- (account/liabilities_due.insurance_due_rows computes premium recurrence from them) — this
-- migration adds NOTHING that any money reader consumes today. A policy premium becomes a booked
-- number only when a human accepts it onto a store_lease row (mig 965's review flow).
--
-- ── RULE TWO (config, never code) ───────────────────────────────────────────────────────────────
-- coverage_type is FREE TEXT with NO database CHECK and no code enum: the vocabulary lives in
-- storeops.tenants.insurance_coverage_types (house default seeded as a column DEFAULT below —
-- BOP / workers comp / general liability / property / umbrella / cyber / EPLI / auto). The owner
-- named BOP and workers comp; another tenant will have others, and neither a CHECK constraint nor a
-- Python enum may be the thing that has to change for them. Same for the expiry notice window:
-- tenants.doc_expiry_notice_days, house default 60 days (the owner's floor), per-document override
-- in mig 966.
--
-- ── DOCUMENTS: store_document is EXTENDED, not forked ───────────────────────────────────────────
-- A second documents table would be a duplicate data path (CLAUDE.md build gate). Instead
-- store_document gains a nullable `policy_id` and a third doc_kind, 'insurance_policy':
--   store_code NOT NULL + policy_id NULL   -> a per-store document (lease, insurance_coi) — every
--                                             existing row, every existing reader, unchanged.
--   store_code NULL     + policy_id NOT NULL -> the master policy document.
-- Existing readers all filter `.eq("store_code", <code>)`, so a policy row can never appear in a
-- per-store list; the append-only contract (every upload INSERTs, current = newest uploaded_at) and
-- the private `store-docs` bucket + signed-URL-by-id download path are unchanged and shared.
--
-- MONEY: no booked number moves. Capture/config only; the premium recorded here is NOT read by
-- account/liabilities_due.py or any P&L / balance-sheet path. Additive + idempotent.

-- ── the policy: one row = one contract, covering N stores ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.insurance_policy (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  policy_number       TEXT,
  insurer             TEXT,                 -- carrier / insurance company on the policy
  coverage_type       TEXT,                 -- free text; vocabulary = tenants.insurance_coverage_types
  coverage_start      DATE,
  coverage_end        DATE,                 -- drives the expiry notifications (mig 966/967)
  premium             NUMERIC,              -- INFORMATIONAL on this table — no money reader reads it
  premium_frequency   TEXT,                 -- 'annual' | 'semiannual' | 'quarterly' | 'monthly' (free text; store_lease keeps the booked recurrence)
  premium_due         DATE,
  inclusions_summary  TEXT,                 -- "summary of inclusions" (owner)
  extra_items         JSONB,                -- "extra items needed as per ai" — [{label, value, note}]
  notice_days         INT,                  -- this policy's OWN notice requirement, when one exists; NULL = org default
  notes               TEXT,
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by          TEXT
);
CREATE INDEX IF NOT EXISTS insurance_policy_org ON storeops.insurance_policy (org_id, coverage_end);

-- ── the assignment: which stores this ONE policy covers ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.insurance_policy_store (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  policy_id    UUID NOT NULL REFERENCES storeops.insurance_policy(id) ON DELETE CASCADE,
  store_code   TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by   TEXT,
  UNIQUE (org_id, policy_id, store_code)
);
CREATE INDEX IF NOT EXISTS insurance_policy_store_by_store
  ON storeops.insurance_policy_store (org_id, store_code);

-- ── store_document EXTENDED for the master policy document (see header) ─────────────────────────
ALTER TABLE storeops.store_document
  ADD COLUMN IF NOT EXISTS policy_id UUID REFERENCES storeops.insurance_policy(id) ON DELETE SET NULL;

DO $$
BEGIN
  -- a policy document belongs to no single store
  BEGIN
    EXECUTE 'ALTER TABLE storeops.store_document ALTER COLUMN store_code DROP NOT NULL';
  EXCEPTION WHEN others THEN NULL;
  END;
  -- widen doc_kind: the mig-946 inline CHECK is auto-named store_document_doc_kind_check
  BEGIN
    EXECUTE 'ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_doc_kind_check';
  EXCEPTION WHEN others THEN NULL;
  END;
  BEGIN
    EXECUTE 'ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_doc_kind_ck';
  EXCEPTION WHEN others THEN NULL;
  END;
  EXECUTE $ck$ALTER TABLE storeops.store_document
             ADD CONSTRAINT store_document_doc_kind_ck
             CHECK (doc_kind IN ('lease', 'insurance_coi', 'insurance_policy'))$ck$;
  -- exactly one owner: a store document, or a policy document
  BEGIN
    EXECUTE 'ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_owner_ck';
  EXCEPTION WHEN others THEN NULL;
  END;
  EXECUTE $ck2$ALTER TABLE storeops.store_document
              ADD CONSTRAINT store_document_owner_ck
              CHECK (store_code IS NOT NULL OR policy_id IS NOT NULL)$ck2$;
END $$;

CREATE INDEX IF NOT EXISTS store_document_policy
  ON storeops.store_document (org_id, policy_id, uploaded_at DESC);

-- ── per-org config (RULE TWO — mig 946 tenants-column precedent, house default as a COLUMN DEFAULT)
-- The owner named BOP and workers comp; the rest are house starting vocabulary. A tenant edits this
-- list; NOTHING in code branches on any of these values.
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS insurance_coverage_types JSONB NOT NULL DEFAULT
    '[{"key":"bop","label":"Business Owner''s Policy (BOP)"},
      {"key":"workers_comp","label":"Workers'' Compensation"},
      {"key":"general_liability","label":"General Liability"},
      {"key":"property","label":"Property"},
      {"key":"umbrella","label":"Umbrella / Excess"},
      {"key":"cyber","label":"Cyber Liability"},
      {"key":"epli","label":"Employment Practices (EPLI)"},
      {"key":"auto","label":"Commercial Auto"}]'::jsonb,
  -- "at least 60 days in advance, or as per lease requirement" (owner) — the FLOOR, per document
  -- kind; a document's own longer requirement wins (doc_intel.resolve_notice_days).
  ADD COLUMN IF NOT EXISTS doc_expiry_notice_days JSONB NOT NULL DEFAULT
    '{"lease":60,"insurance":60}'::jsonb;

COMMENT ON TABLE storeops.insurance_policy IS
  'One insurance POLICY (mig 964, owner 2026-09-05) covering many stores via '
  'storeops.insurance_policy_store. The master document is a storeops.store_document row with '
  'doc_kind=''insurance_policy'' and policy_id set (store_code NULL). Per-store COIs stay exactly '
  'as mig 946 shipped them (doc_kind=''insurance_coi''). premium here is INFORMATIONAL: no money '
  'reader reads this table — account/liabilities_due.py books insurance from store_lease only.';
COMMENT ON COLUMN storeops.insurance_policy.coverage_type IS
  'Free text. Vocabulary = storeops.tenants.insurance_coverage_types (RULE TWO — no CHECK, no code '
  'enum; the owner''s BOP / workers comp are two house rows among many a tenant can edit).';
COMMENT ON COLUMN storeops.insurance_policy.notice_days IS
  'This policy''s OWN advance-notice requirement in days when one exists. NULL = the org floor '
  '(tenants.doc_expiry_notice_days). Resolution takes the LARGER of the two — a document demanding '
  '90 or 180 days beats the 60-day floor, never the other way round (doc_intel.resolve_notice_days).';
COMMENT ON COLUMN storeops.store_document.policy_id IS
  'Set (with store_code NULL) for the master insurance-policy document; NULL for per-store lease / '
  'COI documents. Existing per-store readers filter on store_code, so policy rows never appear in '
  'a store''s document lists.';
COMMENT ON COLUMN storeops.tenants.insurance_coverage_types IS
  'Per-org insurance coverage-type vocabulary [{key,label}] — house default seeded as this column '
  'DEFAULT (BOP, workers comp, GL, property, umbrella, cyber, EPLI, auto). RULE TWO.';
COMMENT ON COLUMN storeops.tenants.doc_expiry_notice_days IS
  'Per-org FLOOR for expiry notifications, per document kind {"lease":60,"insurance":60} (owner '
  '2026-09-05: "at least 60 days in advance"). A lease/policy with a longer own requirement wins.';

-- RLS: same posture as mig 946's sensitive tables — no open_all policy, service_role only.
DO $$
BEGIN
  EXECUTE 'ALTER TABLE storeops.insurance_policy ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE storeops.insurance_policy_store ENABLE ROW LEVEL SECURITY';
  BEGIN EXECUTE 'REVOKE ALL ON storeops.insurance_policy FROM anon, authenticated'; EXCEPTION WHEN others THEN NULL; END;
  BEGIN EXECUTE 'REVOKE ALL ON storeops.insurance_policy_store FROM anon, authenticated'; EXCEPTION WHEN others THEN NULL; END;
  BEGIN EXECUTE 'GRANT ALL ON storeops.insurance_policy TO service_role'; EXCEPTION WHEN others THEN NULL; END;
  BEGIN EXECUTE 'GRANT ALL ON storeops.insurance_policy_store TO service_role'; EXCEPTION WHEN others THEN NULL; END;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 964 complete — storeops.insurance_policy + insurance_policy_store (one policy, many stores), store_document extended with policy_id + doc_kind insurance_policy, tenants.insurance_coverage_types + doc_expiry_notice_days' AS status;

-- REVERT:
--   ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_owner_ck;
--   ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_doc_kind_ck;
--   ALTER TABLE storeops.store_document ADD CONSTRAINT store_document_doc_kind_check
--     CHECK (doc_kind IN ('lease','insurance_coi'));      -- delete policy rows first
--   ALTER TABLE storeops.store_document DROP COLUMN IF EXISTS policy_id;
--   -- store_code NOT NULL can only be restored once every policy-document row is gone.
--   DROP TABLE IF EXISTS storeops.insurance_policy_store;
--   DROP TABLE IF EXISTS storeops.insurance_policy;
--   ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS insurance_coverage_types,
--                                DROP COLUMN IF EXISTS doc_expiry_notice_days;
--   (Uploaded files in the private store-docs bucket survive a table drop — manual bucket cleanup.)
