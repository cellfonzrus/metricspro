-- 965_document_extraction_draft.sql — AI document extraction lands as a REVIEWABLE DRAFT, never as
-- a booked number (owner directive 2026-09-05): "the uploaded policy should then be interpreted by
-- the system using ai and the fields filled for the following: Coverage period ... Premium ...
-- Policy number ... Summary of inclusions ... Extra items needed as per ai"; and for leases "lease
-- term, Rents as per lease in the years coming up, Exit clause, Lease termination liabilities,
-- Contact information, Notice address, Any other critical clauses with clause number and translated
-- in[to] plain English".
--
-- ── THE MONEY RULE THIS TABLE EXISTS TO ENFORCE ─────────────────────────────────────────────────
-- account/liabilities_due.py books rent and insurance premiums FROM storeops.store_lease
-- (rent_for_month / resolve_rent_due / rent_due_window — the §14 read contract). The house posture,
-- stated in account/engine.py, is that the AI "never originates a dollar amount that ships". So an
-- extracted premium / rent / escalation NEVER writes to store_lease. It lands HERE, in `fields`,
-- with per-field provenance, and a HUMAN moves it across:
--
--   upload -> extract (status 'draft') -> a person reviews each field against its own quoted
--   source snippet + page -> accepts the ones they believe -> ONLY THEN does the accepted subset
--   patch storeops.store_lease / storeops.insurance_policy, and money-guarded fields additionally
--   require an explicit money confirmation (backend doc_intel.apply_plan; proof
--   backend/harness_doc_intel.py).
--
-- A field is money-guarded when a money reader consumes its target column: current_rent,
-- rent_schedule, escalation_pct, rent_due, insurance_premium, insurance_premium_due. The guard list
-- lives in ONE place (doc_intel.MONEY_GUARDED) and is asserted by the harness.
--
-- ── SHAPE ───────────────────────────────────────────────────────────────────────────────────────
-- One row per extraction RUN over one storeops.store_document version. Re-extracting appends a new
-- row (the document versions are append-only; so is their interpretation) — "current" = newest
-- created_at per document_id. `fields` is a JSONB array, one entry per extracted field:
--   {"key":"coverage_end", "label":"Coverage period (end)", "value":"2027-03-31",
--    "value_type":"date", "confidence":0.94, "source_text":"...verbatim from the document...",
--    "source_page":3, "money_guarded":false, "target":"insurance_policy.coverage_end"}
-- `clauses` is the lease's critical-clause list, each with its clause NUMBER and a plain-English
-- translation:  {"clause_number":"14.3","title":"Early termination","plain_english":"...",
--                "source_text":"...","source_page":9,"category":"exit"}
-- `extra_items` is the model's open "things that matter here" list (the owner's "extra items needed
-- as per ai"), and `contacts` the contact block it found (fed into storeops.document_contact, mig
-- 966, only when a human accepts them).
--
-- The document text/snippets stay tenant data: they live in this org-scoped table behind the same
-- fail-closed management gate as the documents themselves. ACH/bank columns are NEVER part of an
-- extraction prompt or an extraction row (doc_intel strips them; store_lease.ACH_FIELDS).
--
-- MONEY: no booked number moves — this table is the quarantine that keeps it that way. Additive +
-- idempotent.

CREATE TABLE IF NOT EXISTS storeops.document_extraction (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  document_id   UUID REFERENCES storeops.store_document(id) ON DELETE CASCADE,
  subject_kind  TEXT NOT NULL,      -- 'lease' | 'insurance_policy' | 'insurance_coi'
  subject_ref   TEXT,               -- store_code for lease/COI, policy id for a policy
  status        TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'accepted', 'partially_accepted', 'rejected', 'failed', 'not_extracted')),
  model         TEXT,               -- which model produced it, or 'not_extracted'
  fields        JSONB NOT NULL DEFAULT '[]'::jsonb,
  clauses       JSONB NOT NULL DEFAULT '[]'::jsonb,
  extra_items   JSONB NOT NULL DEFAULT '[]'::jsonb,
  contacts      JSONB NOT NULL DEFAULT '[]'::jsonb,
  applied       JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{key,target,value,accepted_by,accepted_at,money_confirmed}]
  error         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by    TEXT,
  reviewed_by   TEXT,
  reviewed_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS document_extraction_current
  ON storeops.document_extraction (org_id, document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS document_extraction_subject
  ON storeops.document_extraction (org_id, subject_kind, subject_ref, created_at DESC);

COMMENT ON TABLE storeops.document_extraction IS
  'AI extraction of an uploaded lease / insurance policy / COI, held as a REVIEWABLE DRAFT (mig 965, '
  'owner 2026-09-05). Nothing here is a booked number: account/liabilities_due.py reads store_lease '
  'only, and an extracted value reaches store_lease / insurance_policy solely through an explicit '
  'human accept (money-guarded fields need an extra money confirmation). Every field carries its own '
  'confidence and the verbatim source snippet + page so a reviewer can verify without reopening the PDF.';
COMMENT ON COLUMN storeops.document_extraction.fields IS
  'Array of {key,label,value,value_type,confidence,source_text,source_page,money_guarded,target}. '
  'money_guarded = true when the target column feeds a money reader (rent, escalation, rent due, '
  'insurance premium + premium due). Guard list: backend doc_intel.MONEY_GUARDED.';
COMMENT ON COLUMN storeops.document_extraction.clauses IS
  'Critical lease clauses: {clause_number,title,category,plain_english,source_text,source_page} — '
  'the owner''s "any other critical clauses with clause number and translated into plain English".';
COMMENT ON COLUMN storeops.document_extraction.applied IS
  'Audit trail of what a human actually accepted onto the live record, and whether the money '
  'confirmation was given. Empty on every draft.';

-- RLS: mig 946 sensitive posture — no open_all policy, service_role only (extraction rows quote
-- lease text verbatim).
DO $$
BEGIN
  EXECUTE 'ALTER TABLE storeops.document_extraction ENABLE ROW LEVEL SECURITY';
  BEGIN EXECUTE 'REVOKE ALL ON storeops.document_extraction FROM anon, authenticated'; EXCEPTION WHEN others THEN NULL; END;
  BEGIN EXECUTE 'GRANT ALL ON storeops.document_extraction TO service_role'; EXCEPTION WHEN others THEN NULL; END;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 965 complete — storeops.document_extraction (AI drafts with per-field provenance/confidence; money fields quarantined until a human accepts)' AS status;

-- REVERT:
--   DROP TABLE IF EXISTS storeops.document_extraction;
--   (Endpoints degrade: extraction returns "not available" and the review panel shows nothing; no
--    lease/policy value ever depended on this table, so no booked number changes either way.)
