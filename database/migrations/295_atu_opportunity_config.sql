-- 295 — ATU (autopay) opportunity: the tenant's own savings + commission-rate assumptions.
--
-- WHY (owner, 2026-08-12): "the logic is if the customer is paying with a credit card then the customer
-- can save $9 per month for doing auto pay and the store makes 5% extra ATU commission on the boost side
-- and 8.5% on the total side — note the saving numbers and the income numbers will be entered by the
-- user as they will change."
--
-- So the four numbers are INPUTS, not constants. Hard-coding 9 / 5 / 8.5 anywhere in the engine would
-- violate the SAP-configurable rule and would silently go stale the first time a carrier restates its
-- rate — while continuing to render a confident dollar figure. They live here, per tenant, editable
-- from the page.
--
-- NOT MONEY-TOUCHING in the payout sense: nothing in this table is read by the commission engine, no
-- rep is paid from it, and no ledger row derives from it. It parameterises an OPPORTUNITY report — a
-- statement about revenue NOT collected. Changing a rate here restates that report and nothing else.
--
-- Defaults carry the owner's stated figures so the page is useful on first open, but they are DEFAULTS:
-- a tenant that never opens the settings still gets a coherent report, and one that edits them is never
-- overwritten by a later deploy.
--
-- total_recharge_base is deliberately a hand-entered number and defaults to 0. Total/VidaPay recharges
-- settle outside the POS export — MEASURED 2026-08-12: exactly 3 Total line items exist in the whole of
-- commcalc.raw_sales — so there is no honest way to derive the Total base from POS tender data today.
-- Defaulting it to 0 means the Total side reads $0 until someone types a number, which is the truthful
-- state; inventing a proxy would produce a plausible figure with nothing behind it.

CREATE TABLE IF NOT EXISTS commcalc.atu_config (
  org_id                uuid PRIMARY KEY,
  saving_per_month      numeric(12,2) NOT NULL DEFAULT 9.00,
  boost_rate_pct        numeric(6,3)  NOT NULL DEFAULT 5.000,
  total_rate_pct        numeric(6,3)  NOT NULL DEFAULT 8.500,
  total_recharge_base   numeric(14,2) NOT NULL DEFAULT 0.00,
  updated_at            timestamptz   NOT NULL DEFAULT now(),
  updated_by            text
);

COMMENT ON TABLE commcalc.atu_config IS
  'Per-tenant assumptions for the ATU (autopay) opportunity report: the customer''s monthly saving and '
  'the carrier ATU commission rates. Owner-entered because they change (directive 2026-08-12). Read ONLY '
  'by the ATU opportunity report — never by the commission engine, and no payout derives from it.';
COMMENT ON COLUMN commcalc.atu_config.saving_per_month IS
  'What the customer saves per month by enrolling in autopay. Owner default $9.';
COMMENT ON COLUMN commcalc.atu_config.boost_rate_pct IS
  'Extra ATU commission on the Boost side, as a PERCENT of the recharge (5 = 5%). Owner default 5.';
COMMENT ON COLUMN commcalc.atu_config.total_rate_pct IS
  'Extra ATU commission on the Total side, as a PERCENT of the recharge (8.5 = 8.5%). Owner default 8.5.';
COMMENT ON COLUMN commcalc.atu_config.total_recharge_base IS
  'Hand-entered monthly Total/VidaPay recharge base from customers not on autopay. Defaults to 0 because '
  'Total recharges do not reach the POS export (3 Total line items in all of raw_sales, measured '
  '2026-08-12) — so 0 is the honest value until a human supplies one.';

-- Seed the house org so the page has a row to edit on first open. ON CONFLICT DO NOTHING, never an
-- UPDATE: a re-run must not stamp a tenant's edited rates back to the defaults.
INSERT INTO commcalc.atu_config (org_id)
VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (org_id) DO NOTHING;
