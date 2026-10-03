-- 1042_billpay_fee_product_desc.sql — PER-ORG vocabulary for the customer bill-payment SERVICE FEE.
--
-- OWNER ASK 2026-10-03, verbatim: "diff of the pos data and th rep defined data needs to be
-- investigated why those errors take place".
--
-- WHAT THE INVESTIGATION FOUND (read-only, September 2026, measured through the system's own shared
-- helpers). Of 535 house-org store-days, only NINE agreed between the rep's declared bill-payment
-- cash and the POS figure. The dominant cause is not rep error: the customer service fee is rung as
-- its own sales line — department 'Bill Payments', category 'Other Charge', product_desc
-- 'ePay Service Charge', 4,176 lines / $16,592.00 in the month — and the exec `bill_payment` rule
-- carries `exclude_category: ['other charge']`, so the fee is excluded from the POS bill-pay figure.
-- That exclusion is CORRECT for the bill-payment metric (a service charge is not a bill payment) and
-- WRONG for the drawer, because the fee is cash the rep took from the customer and must declare.
-- Adding the fee cash back took agreement from 9 store-days to 342 within a dollar (111 to the cent)
-- and closed $7,721 of the $18,657 total gap. The 188 store-days still disagreeing are genuine
-- exceptions worth $10,834 — which is what a district manager should be chasing.
--
-- WHY THIS IS A CONFIG ROW AND NOT A CODE CONSTANT (RULE TWO, and the no-patchwork directive). The
-- house org words its fee 'ePay Service Charge'. LuxeLink (854f6d7b) rings NO separate fee line at
-- all — its bill-pay department 'Rtr' holds only 'Total Wireless RTR Wallet' and plan lines, and its
-- own $57,905 September gap therefore has a DIFFERENT cause, which this migration does not claim to
-- fix. Hard-coding the Boost wording would have repaired one tenant and left the other exactly as
-- wrong: the patchwork the house rules forbid. So the vocabulary is per-org, with the house default
-- living in ONE place in code (`commcalc/epay_fee_recon.HOUSE_FEE_DESCS`) and every caller
-- dereferencing `metric_recon.pos_billpay_cash` / `pos_billpay_total` rather than re-adding the legs.
--
-- EMPTY DEFAULT = BYTE-IDENTICAL. `resolve_fee_descs` falls back to the house tuple for a blank
-- list, a non-list, a missing row or a pre-1042 schema (`_billpay_fee_tokens` has its own defensive
-- read, the mig-313 / mig-944 posture), so nothing changes for a tenant that configures nothing and
-- the report works before this migration is applied.
--
-- Additive, idempotent, no backfill, no data movement, no money moved.
-- REVERT: ALTER TABLE commcalc.accessory_config DROP COLUMN IF EXISTS billpay_fee_product_desc;
--         (Dropping it restores the house default for every org, which is the shipped behaviour.)

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS billpay_fee_product_desc JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN commcalc.accessory_config.billpay_fee_product_desc IS
  'Per-org product_desc tokens identifying the customer bill-payment SERVICE FEE line, matched '
  'lower-cased by CONTAINMENT. Empty (the default) resolves to the house vocabulary in '
  'commcalc/epay_fee_recon.HOUSE_FEE_DESCS. This fee is CASH IN THE DRAWER that the rep declares, '
  'and is deliberately NOT part of the bill-payment metric (the exec bill_payment rule excludes its '
  'category on purpose). Read by commcalc/router._billpay_fee_tokens; the two legs are added in ONE '
  'home, metric_recon.pos_billpay_cash / pos_billpay_total — never at a call site.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 1042 complete — commcalc.accessory_config.billpay_fee_product_desc installed '
       '(per-org; empty default = the house ePay-service-charge vocabulary, byte-identical).' AS status;
