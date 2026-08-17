-- 861 — mark PII reveals in the Customer-360 lookup audit (Security Controls Spec §2, item 8).
-- Customer phone/email are masked by default; a lookup that UNMASKS them is a distinct PII-access
-- event, recorded here. The backend tolerates this column being absent (it retries the audit insert
-- without it), so applying this migration simply turns the flag on in the trail.
ALTER TABLE core.crm_lookup_audit ADD COLUMN IF NOT EXISTS pii_revealed BOOLEAN NOT NULL DEFAULT false;
