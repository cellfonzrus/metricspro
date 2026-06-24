-- 036_chargeback_review.sql
-- Unified "chargeback bucket" — candidates from multiple sources that get ASSIGNED to the rep who
-- did the sale, then flow into the employee chargeback file (commcalc.chargeback_items). Sources:
--   vip_file    — VIP Asset/Chargebacks export (Dealer-NNNNN-Chargebacks.xlsx), per-ESN clawback
--   fraud_email — fake / reused customer email across activations (critical)            [increment 2]
--   fraud_dupe  — same customer name / id on multiple activations (needs mgmt review)   [increment 2]
-- Assign-first workflow: a candidate sits here OPEN until assigned (fraud_dupe also needs an
-- approve/disapprove). Assigning writes the chargeback_items row for that rep+period.

CREATE TABLE IF NOT EXISTS commcalc.chargeback_review (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  source        TEXT NOT NULL,                 -- vip_file | fraud_email | fraud_dupe
  severity      TEXT DEFAULT 'warning',        -- critical | warning
  status        TEXT DEFAULT 'open',           -- open | assigned | dismissed
  needs_review  BOOLEAN DEFAULT false,         -- true for fraud_dupe (mgmt approve/disapprove)
  review        TEXT,                          -- approved | disapproved | NULL
  reviewed_by   TEXT,
  reviewed_at   TIMESTAMPTZ,
  -- evidence
  store_code      TEXT,
  store_address   TEXT,
  period          TEXT,                        -- "Month YYYY" (drives which rep report it hits)
  occurred_date   TEXT,                        -- activation / chargeback processing date
  customer_name   TEXT,
  email           TEXT,
  customer_no     TEXT,
  phone_number    TEXT,
  esn             TEXT,
  imei            TEXT,
  brand           TEXT,
  plan            TEXT,
  amount          NUMERIC DEFAULT 0,           -- VIP file: chargeback amount; fraud: editable at assign
  detail          TEXT,
  reason          TEXT,
  -- assignment
  suggested_rep   TEXT,
  assigned_rep    TEXT,
  assigned_by     TEXT,
  assigned_at     TIMESTAMPTZ,
  chargeback_item_ref TEXT,                     -- source_ref of the chargeback_items row created
  dedupe_key      TEXT NOT NULL DEFAULT '',     -- stable per-source key (dedupe on re-detect/re-sweep)
  raw             JSONB,
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS chargeback_review_status ON commcalc.chargeback_review (org_id, status);
CREATE INDEX IF NOT EXISTS chargeback_review_source ON commcalc.chargeback_review (org_id, source);

-- raw_sales: capture the customer identity columns from the 78-col Sales Transaction Details
-- (Customer / Email / Customer #) so the email-reuse + duplicate-id fraud detectors have data.
-- Populates on the NEXT sales upload (existing rows stay NULL until re-uploaded).
ALTER TABLE commcalc.raw_sales ADD COLUMN IF NOT EXISTS customer   TEXT;
ALTER TABLE commcalc.raw_sales ADD COLUMN IF NOT EXISTS email      TEXT;
ALTER TABLE commcalc.raw_sales ADD COLUMN IF NOT EXISTS customer_no TEXT;

-- VIP sweep toggle for the chargebacks export.
ALTER TABLE commcalc.vip_sweep_config ADD COLUMN IF NOT EXISTS sweep_chargebacks BOOLEAN DEFAULT true;

-- RLS open_all (match sibling commcalc tables).
ALTER TABLE commcalc.chargeback_review ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON commcalc.chargeback_review;
CREATE POLICY open_all ON commcalc.chargeback_review FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON commcalc.chargeback_review TO anon, authenticated, service_role;
