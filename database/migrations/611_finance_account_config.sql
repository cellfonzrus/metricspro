-- 611_finance_account_config.sql — PER-ORG finance/accounting config (de-hardcode the P&L booking rates).
--
-- WHY: coa.py hard-codes ACCESSORY_COGS_PCT = 0.20 (accessory COGS booked as a flat 20% of gross
-- accessory sales). That is a per-TENANT accounting policy, not a universal constant — a different
-- tenant may carry a different accessory margin. Per AGENT_CONTRACT §3 (SAP-configurable) a booking
-- rate belongs in a config table with an admin UI, not a code literal.
--
-- WHAT: a single-row-per-org finance config table. First knob: accessory_cogs_pct. Future finance
-- knobs (other flat COGS/margin policies) extend this table rather than inventing parallel ones.
--
-- BOOST-SAFE (byte-identical): the DEFAULT is 0.20 and NO org row is seeded, so coa's per-org resolver
-- returns the historical 0.20 for EVERY tenant until someone edits it. No P&L/GP number moves on any
-- org (house or luxelink) until a config row is explicitly saved. Additive + idempotent + degrades
-- gracefully (coa falls back to the 0.20 code default if this table is absent).

CREATE TABLE IF NOT EXISTS commcalc.account_config (
  org_id             UUID PRIMARY KEY,
  accessory_cogs_pct NUMERIC NOT NULL DEFAULT 0.20   -- accessory COGS as a fraction of gross accessory sales
    CHECK (accessory_cogs_pct >= 0 AND accessory_cogs_pct <= 1),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE commcalc.account_config ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='account_config' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.account_config FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
  END IF;
END $$;
GRANT ALL ON commcalc.account_config TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 611 complete — commcalc.account_config installed (per-org; empty = 0.20 accessory COGS default, byte-identical)' AS status;
