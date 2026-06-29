-- 058_distributors.sql — distributors as a configurable category + a universal payment ledger.
--
-- WHY: "VIP" was hard-wired as THE supplier, but it's only one of (e.g.) six Boost distributors, and a
-- different tenant/carrier may use entirely different ones on different ARRANGEMENTS. A distributor is who
-- you source devices/inventory from, on one of:
--   • TERMS       — straight/net credit (14/21/30/45/60-day): pay the invoice within N days.
--   • CONSIGNMENT — devices lent + billed on a cycle, settled over 60+ days (VIP's weekly PayGo = the
--                   Asset Lending ledger). Only consignment distributors have asset lending.
--   • COD         — cash on delivery: paid up front, no credit.
-- This arrangement is chosen per distributor at ONBOARDING.
--
-- Also (universal, all companies): for every distributor payment we record HOW it was funded — from the
-- company's OWN account or a BORROWED/financed account — so cash-flow + financing exposure is visible.
--
-- Additive + idempotent. Seeds VIP as the house org's consignment distributor so today's VIP views keep
-- working under the new generic "Distributors" category.

CREATE TABLE IF NOT EXISTS commcalc.distributors (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  name              TEXT NOT NULL,
  carrier_id        UUID,                              -- commcalc.carrier.id this distributor supplies for
  arrangement       TEXT NOT NULL DEFAULT 'terms',     -- 'terms' | 'consignment' | 'cod'
  terms_days        INT  DEFAULT 30,                   -- net days for 'terms' (14/21/30/45/60); consignment horizon
  billing_cycle     TEXT NOT NULL DEFAULT 'net',       -- 'net' | 'weekly' | 'monthly'
  has_asset_lending BOOLEAN NOT NULL DEFAULT false,    -- lends devices billed on a cycle (consignment/VIP-style)
  default_funding   TEXT NOT NULL DEFAULT 'own',       -- 'own' | 'borrowed' — default payment account source
  portal_provider   TEXT,                              -- optional connector provider_key (e.g. 'vip')
  is_active         BOOLEAN NOT NULL DEFAULT true,
  notes             TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, name)
);
CREATE INDEX IF NOT EXISTS distributors_org ON commcalc.distributors (org_id, is_active);

-- Universal distributor PAYMENT ledger — records each payment + how it was FUNDED (own vs borrowed
-- account). Works for any company/distributor/arrangement (the VIP weekly PayGo bills come from the
-- sweep; this records how those — or any net-terms / COD invoice — were paid).
CREATE TABLE IF NOT EXISTS commcalc.distributor_payments (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  distributor_id  UUID REFERENCES commcalc.distributors(id) ON DELETE SET NULL,
  pay_date        DATE,
  period          TEXT,                                -- e.g. 'June 2026'
  amount          NUMERIC NOT NULL DEFAULT 0,
  funding_source  TEXT NOT NULL DEFAULT 'own',         -- 'own' | 'borrowed'
  account_label   TEXT,                                -- which account (e.g. 'Operating', 'Amex LOC')
  ref             TEXT,                                -- external ref / vip_payment_id / invoice #
  notes           TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS distributor_payments_org ON commcalc.distributor_payments (org_id, distributor_id, pay_date);

-- Seed VIP for the house org (consignment, weekly-billed lent devices = the existing PayGo / Asset Lending).
INSERT INTO commcalc.distributors (org_id, name, arrangement, terms_days, billing_cycle, has_asset_lending, default_funding, portal_provider, notes)
VALUES ('00000000-0000-0000-0000-000000000001', 'VIP', 'consignment', 60, 'weekly', true, 'own', 'vip',
        'VIP Wireless — devices on consignment, billed weekly via the PayGo portal (Asset Lending).')
ON CONFLICT (org_id, name) DO NOTHING;

ALTER TABLE commcalc.distributors        ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.distributor_payments ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='distributors' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.distributors FOR ALL USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='distributor_payments' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.distributor_payments FOR ALL USING (true) WITH CHECK (true); END IF;
END $$;
