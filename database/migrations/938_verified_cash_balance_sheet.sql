-- 938_verified_cash_balance_sheet.sql — DM-verified store cash on the Balance Sheet
-- (owner directive 2026-09-02, item 4).
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "all cash collected in the store must be added to the balance sheet as cash
-- collected after it has been verified by the DM, either the cash is deposited in the bank or it
-- is used in expenses, everything needs to be updated in the financials as appropriate."
--
-- One per-org knob on commcalc.account_config (mig 611 — the finance config table), resolved by
-- account/balance_sheet.load_bs_config and consumed by account/statement_engine.build_inputs_full:
--
--   cash_on_hand_basis   'off' (default)  — the Balance Sheet books NO store cash-on-hand line;
--                                           byte-identical statements for every org.
--                        'verified'       — the NEW 'Cash on hand — stores (undeposited)' asset
--                                           line (auto_opt, store grain) books each store's AS-OF
--                                           balance: DM-VERIFIED store-days' declared cash (the
--                                           DM-corrected figure, TKT-1030 overlay) MINUS
--                                           everything that left the envelope (cash pickups /
--                                           deposits + approved envelope expenses/withdrawals) —
--                                           the closing module's OWN `_cash_position_core`
--                                           movement, so the BS can never disagree with the Cash
--                                           Position / Store Cash on Hand pages. Unverified
--                                           declared cash is EXCLUDED and reported in the
--                                           statement meta (never silently dropped).
--                        'all'            — every declared day counts (the operational number,
--                                           = GET /closing/store-cash-on-hand), verification not
--                                           required.
--
-- The lifecycle ties out in the statements: the line is CASH in the derived Cash Flow statement
-- (cash & cash equivalents = manual 'Cash / bank' + this line — statement_engine.CF_CASH_KEYS),
-- a bank deposit moves dollars from this line to the manual bank line (owner keys the bank
-- balance; the deposit relief flows automatically), and a cash-paid expense both relieves this
-- line AND lands on the P&L through the existing closing-expense → store_expenses sweep.
--
-- RULE TWO: per-org config, house default 'off', no tenant name in code.
--
-- MONEY-TOUCHING when a tenant opts in — the org seed below is COMMENTED OUT behind the owner
-- gate (mig-622/933 precedent). The schema change itself moves no number for any org.
-- Additive + idempotent. Run in the Supabase SQL editor.

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS cash_on_hand_basis TEXT
    CHECK (cash_on_hand_basis IS NULL OR cash_on_hand_basis IN ('off', 'verified', 'all'));

COMMENT ON COLUMN commcalc.account_config.cash_on_hand_basis IS
  'Balance-Sheet store cash-on-hand line: NULL/''off'' = not booked (historical behaviour); '
  '''verified'' = DM-verified store-days'' declared cash minus envelope outflows, as of period '
  'end (owner rule: cash counts as collected only after DM verification); ''all'' = every '
  'declared day counts (the operational cash-position number).';

-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- ⛔ MONEY SEED — COMMENTED OUT. Owner GO uncomments and runs this block (mig-622/933 precedent).
--
-- LuxeLink (org 854f6d7b-…), measured live 2026-09-02: August 2026 has 320 daily_closing rows
-- declaring $92,225.64 cash, but only SIX store-days are DM-verified (2026-08-13 → 2026-08-18) —
-- under 'verified' the BS line starts small and grows as the DM verification habit lands (the
-- statement meta reports the excluded unverified dollars so the gap is visible, and the
-- entry-quality/ops-chargeback machinery already nudges the verification habit). 'all' would
-- book the full operational balance immediately. The owner picks the basis; 'verified' is his
-- stated rule.
--
-- INSERT INTO commcalc.account_config (org_id, cash_on_hand_basis)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'verified')
-- ON CONFLICT (org_id) DO UPDATE
--   SET cash_on_hand_basis = EXCLUDED.cash_on_hand_basis,
--       updated_at         = now();
--
-- After running: recompute the open periods (POST /account/compute/{period}, or wait for
-- /account/run-due) so the stored statements pick the config up.
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 938 complete — account_config.cash_on_hand_basis (default off; org seed gated)' AS status;

-- REVERT:
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS cash_on_hand_basis;
--   (load_bs_config degrades to ''off'' — no store cash line, byte-identical books.)
