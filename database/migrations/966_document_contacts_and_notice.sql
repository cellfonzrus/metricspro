-- 966_document_contacts_and_notice.sql — multiple notification contacts per lease / policy, and the
-- document's OWN notice requirement (owner directive 2026-09-05): "B[o]th of these will have a
-- multiple contact information to be send a notification when a coi is expir[ing] or th[e] lease is
-- getting over at least 60 days in advance or as per lease requirement in case of an extension."
--
-- ── WHY A TABLE AND NOT MORE COLUMNS ────────────────────────────────────────────────────────────
-- storeops.store_lease (mig 946) already holds the landlord + site contact — ONE of each, and they
-- are the people you PAY, not necessarily the people you TELL. The owner asked for MULTIPLE
-- contacts, on both a lease and a policy, purely for expiry notification. One shared table serves
-- both subjects (subject_kind + subject_ref) rather than two parallel ones; the landlord/site
-- contact columns stay exactly where they are and are NOT duplicated here.
--
-- ── THE NOTICE WINDOW (RULE TWO all the way down) ───────────────────────────────────────────────
-- Resolution, implemented once in backend doc_intel.resolve_notice_days and proved by
-- backend/harness_doc_intel.py:
--
--     notice_days = MAX( the document's own requirement, the org floor )
--
--   document's own requirement : store_lease.lease_notice_days (below) or
--                                insurance_policy.notice_days (mig 964) — typically what the AI
--                                extraction found in the notice clause, accepted by a human.
--   org floor                  : storeops.tenants.doc_expiry_notice_days {"lease":60,"insurance":60}
--                                (mig 964) — the owner's "at least 60 days".
--   house fallback             : 60 (doc_intel.HOUSE_NOTICE_DAYS) when a tenant row/column is absent.
--
-- MAX, not "override": a lease demanding 90 or 180 days must beat the 60-day floor, and a lease
-- demanding 30 must NOT drop us below the owner's floor. Nothing is hard-coded per tenant.
--
-- ALERT LOG: notifications dedupe through the EXISTING storeops.alert_log (mig 433) — scopes
-- 'doc_expiry_lease' / 'doc_expiry_insurance', ref_key '<subject>:<ref>:<milestone>' — so one
-- milestone notifies once. No second alert-state table.
--
-- MONEY: none. Contacts + a notice window. Additive + idempotent.

CREATE TABLE IF NOT EXISTS storeops.document_contact (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  subject_kind   TEXT NOT NULL CHECK (subject_kind IN ('lease', 'insurance_policy')),
  subject_ref    TEXT NOT NULL,        -- store_code for a lease, insurance_policy.id for a policy
  name           TEXT,
  email          TEXT,
  phone          TEXT,
  role           TEXT,                 -- free text ('Landlord', 'Broker', 'Owner', ...) — RULE TWO
  notify_expiry  BOOLEAN NOT NULL DEFAULT TRUE,
  notice_days    INT,                  -- optional per-contact lead time; NULL = the resolved window
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by     TEXT
);
CREATE INDEX IF NOT EXISTS document_contact_subject
  ON storeops.document_contact (org_id, subject_kind, subject_ref);

-- The lease's own accepted narrative fields (owner 2026-09-05 asked for these to be "filled out"):
-- the notice requirement, the notice address, the exit clause, the termination liabilities, and the
-- accepted critical-clause list with clause numbers + plain-English translations. Every one of them
-- is written ONLY by a human accepting an extraction (mig 965) or typing it in — never by the model
-- directly. NONE of them is read by account/liabilities_due.py or any P&L / balance-sheet path.
ALTER TABLE storeops.store_lease
  ADD COLUMN IF NOT EXISTS lease_notice_days             INT,
  ADD COLUMN IF NOT EXISTS notice_address                TEXT,   -- where notice must be served
  ADD COLUMN IF NOT EXISTS lease_exit_clause             TEXT,   -- "Exit clause" (owner)
  ADD COLUMN IF NOT EXISTS lease_termination_liabilities TEXT,   -- "Lease termination liabilities"
  ADD COLUMN IF NOT EXISTS lease_critical_clauses        JSONB,  -- accepted [{clause_number,title,category,plain_english,source_page}]
  -- when THIS STORE's certificate of insurance expires (the COI is per store; the policy behind it
  -- is one row in storeops.insurance_policy, mig 964). Drives the COI expiry notification.
  ADD COLUMN IF NOT EXISTS coi_expires                   DATE;

COMMENT ON TABLE storeops.document_contact IS
  'Multiple expiry-notification contacts per lease (subject_ref = store_code) or insurance policy '
  '(subject_ref = insurance_policy.id) — mig 966, owner 2026-09-05. Separate from store_lease''s '
  'landlord/site contact, which are the people you pay. Notifications dedupe via the existing '
  'storeops.alert_log (mig 433).';
COMMENT ON COLUMN storeops.document_contact.notice_days IS
  'Optional per-contact lead time in days. NULL = the resolved window '
  '(max of the document''s own requirement and tenants.doc_expiry_notice_days; house 60).';
COMMENT ON COLUMN storeops.store_lease.lease_notice_days IS
  'This lease''s OWN advance-notice requirement in days (renewal / termination notice clause). NULL '
  '= the org floor. Resolution takes the LARGER of the two — 90 or 180 beats the 60-day floor, and '
  '30 never drops below it (doc_intel.resolve_notice_days).';
COMMENT ON COLUMN storeops.store_lease.notice_address IS
  'Address at which notice must be served under the lease (owner 2026-09-05 "Notice address"). Not '
  'a money column — no reader in account/liabilities_due.py touches it.';
COMMENT ON COLUMN storeops.store_lease.lease_critical_clauses IS
  'Critical clauses a human ACCEPTED from an AI extraction (mig 965): [{clause_number,title,'
  'category,plain_english,source_page}]. The draft lives in storeops.document_extraction.clauses; '
  'this column only ever holds what someone signed off on.';
COMMENT ON COLUMN storeops.store_lease.coi_expires IS
  'Expiry of THIS store''s certificate of insurance. Drives the COI expiry notification (mig 967) '
  'alongside insurance_policy.coverage_end for the master policy. Not a money column.';

DO $$
BEGIN
  EXECUTE 'ALTER TABLE storeops.document_contact ENABLE ROW LEVEL SECURITY';
  BEGIN EXECUTE 'REVOKE ALL ON storeops.document_contact FROM anon, authenticated'; EXCEPTION WHEN others THEN NULL; END;
  BEGIN EXECUTE 'GRANT ALL ON storeops.document_contact TO service_role'; EXCEPTION WHEN others THEN NULL; END;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 966 complete — storeops.document_contact (multi-contact expiry notification) + store_lease.lease_notice_days / notice_address' AS status;

-- REVERT:
--   DROP TABLE IF EXISTS storeops.document_contact;
--   ALTER TABLE storeops.store_lease DROP COLUMN IF EXISTS lease_notice_days,
--                                    DROP COLUMN IF EXISTS notice_address,
--                                    DROP COLUMN IF EXISTS lease_exit_clause,
--                                    DROP COLUMN IF EXISTS lease_termination_liabilities,
--                                    DROP COLUMN IF EXISTS lease_critical_clauses,
--                                    DROP COLUMN IF EXISTS coi_expires;
--   (Expiry notifications then fall back to the org floor with no contacts — the sweep sends
--    nothing rather than erroring, and no booked number is involved either way.)
