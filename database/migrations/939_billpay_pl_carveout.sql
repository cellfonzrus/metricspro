-- 939_billpay_pl_carveout.sql — bill-pay pass-through P&L presentation + settlement convention
-- (owner directive 2026-09-02, item 5).
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "the total of epay/vida pay for bill payments collected in the store should be
-- equal to or less than the total of cash and card collected in the store... a separate box
-- should assigned to the p&l to account for the cash / credit epay assignment. technically the
-- total cash collected and the credit collected comprises of the total revenue, then billpay is
-- deducted from it as it is not income and is offset by either the cash deposited in the bank to
-- cover the payments or by the commission received, different carriers do it in a different way,
-- boost pays commission and deducts payments separately, total deducts the payment for the phones
-- and bill payments from the commission earned."
--
-- Two per-org knobs on commcalc.commission_org_config (the pl_* config family), resolved by
-- account/billpay_pl.load_config and consumed by account/coa.build_inputs:
--
--   pl_billpay_presentation  'off' (default) — no bill-pay lines on the P&L; byte-identical
--                                              statements for every org.
--                            'carveout'      — the P&L shows the matched PASS-THROUGH pair
--                                              (billpay_collected +X / billpay_offset −X, both
--                                              auto_opt revenue lines, store grain) built from
--                                              the reps' declared ePay split on the daily closing
--                                              sheet (epay_on_cash + epay_on_credit; the DM's
--                                              VERIFIED corrections win at store-day grain).
--                                              The pair nets to ZERO by construction — gross
--                                              profit and net income never move; collected
--                                              volume becomes visible, bill-pay never
--                                              masquerades as income.
--
--   pl_billpay_settlement    'remit_separate' (default) — the offset line reads "Bill payments
--                                              remitted to processor (pass-through)": commission
--                                              is paid separately and collected payments are
--                                              remitted/auto-debited separately (the owner's
--                                              "boost style" — named here ONLY as provenance;
--                                              no carrier name appears in code, RULE TWO).
--                            'net_from_commission' — the offset line reads "Bill payments netted
--                                              from carrier commission (pass-through)": the
--                                              processor nets phone + bill-pay payments out of
--                                              the commission it owes (the owner's "total style").
--
-- The COVERAGE reconciliation (billpay ≤ cash + card per store/day) is code, not config:
-- GET /billpay-coverage/{period} (metric_recon.reconcile_billpay_coverage — pure), riding the
-- existing metric_source_of_truth processor resolution.
--
-- MONEY-TOUCHING when a tenant opts in (section subtotals move; NET INCOME DOES NOT) — the org
-- seed below is COMMENTED OUT behind the owner gate (mig-622/933/938 precedent).
-- Additive + idempotent. Run in the Supabase SQL editor.

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_billpay_presentation TEXT
    CHECK (pl_billpay_presentation IS NULL OR pl_billpay_presentation IN ('off', 'carveout'));

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_billpay_settlement TEXT
    CHECK (pl_billpay_settlement IS NULL OR pl_billpay_settlement IN ('remit_separate', 'net_from_commission'));

COMMENT ON COLUMN commcalc.commission_org_config.pl_billpay_presentation IS
  'P&L bill-pay pass-through: NULL/''off'' = not shown (historical); ''carveout'' = the matched '
  '± pair billpay_collected / billpay_offset from the daily-closing declared ePay split '
  '(DM-verified corrections win). Net income identical under both.';
COMMENT ON COLUMN commcalc.commission_org_config.pl_billpay_settlement IS
  'How this org''s processor settles the bill-pay pass-through — labels the offset line: '
  'NULL/''remit_separate'' = commission paid separately, payments remitted separately; '
  '''net_from_commission'' = payments (phones + bill pay) netted out of commission owed.';

-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- ⛔ MONEY SEED — COMMENTED OUT. Owner GO uncomments and runs this block (mig-622/933 precedent).
--
-- LuxeLink (org 854f6d7b-…), measured live 2026-09-02: August 2026 daily closings declare
-- $31,997.09 ePay-on-cash + $6,327.30 ePay-on-credit = $38,324.39 of bill-pay pass-through that
-- today appears NOWHERE on the P&L. Their processor (VidaPay/Total-Access) nets phone + bill-pay
-- payments from the commission earned — the owner's "total style" ⇒ 'net_from_commission'.
--
-- INSERT INTO commcalc.commission_org_config (org_id, pl_billpay_presentation, pl_billpay_settlement)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'carveout', 'net_from_commission')
-- ON CONFLICT (org_id) DO UPDATE
--   SET pl_billpay_presentation = EXCLUDED.pl_billpay_presentation,
--       pl_billpay_settlement   = EXCLUDED.pl_billpay_settlement,
--       updated_at              = now();
--
-- After running: recompute the open periods (POST /account/compute/{period}, or wait for
-- /account/run-due) so the stored statements pick the config up.
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 939 complete — commission_org_config.pl_billpay_presentation/_settlement (default off; org seed gated)' AS status;

-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_billpay_presentation;
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_billpay_settlement;
--   (billpay_pl.load_config degrades to ''off'' — no bill-pay lines, byte-identical books. The
--    coverage recon endpoint is read-only and unaffected.)
