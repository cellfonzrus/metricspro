-- 933_finance_balance_sheet_truths.sql — per-org Balance-Sheet config (owner report 2026-09-02):
-- inventory basis (all the unsold phones) + handset payables per the vendor's own due dates.
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "in balance sheet, make sure the inventory shows all the unsold phones, it
-- should also reconcile against the inventory report being pulled in the email in the
-- reconciliation tab, the money owed for the phones is not being uploaded, which has already been
-- defined as per the due dates in the handset report in total and the asset landing in boost for
-- now, again nothing is hardcoded but built on a logic to be used by other prospective tenants."
--
-- Two per-org knobs on commcalc.account_config (mig 611 — the finance config table), resolved by
-- account/balance_sheet.load_bs_config and consumed by account/statement_engine:
--
--   inventory_basis              'report' (default) — the Balance-Sheet inventory line keeps
--                                today's source: the emailed Inventory Aging per-store $ totals
--                                (commcalc.inventory_value), manual override winning. Byte-identical.
--                                'devices' — the line reads the UNSOLD-PHONE ledger instead:
--                                commcalc.inventory_aging_device rows on_hand at each store's
--                                current snapshot, summed at unit_cost (manual override still
--                                wins; a store with no device rows keeps the report value so
--                                coverage never regresses). GET /account/inventory-recon shows the
--                                per-store tie-out either way.
--
--   handset_payable_order_types  JSONB list of raw_ma_daily_tx.order_type families that are
--                                marketplace HANDSET purchases. Rows in these families book the
--                                NEW 'Handset payables (devices due to distributor)' liability
--                                while inside the vendor's OWN due-date window (tx_date <= as-of
--                                < due_date — the feed's due_date column, populated on every row;
--                                see mig 620's verification note). DEFAULT [] books NOTHING, so
--                                every org is byte-identical until it opts in. The Boost-side
--                                device payable stays on asset_ledger.owed_to_vip (the existing
--                                owed_vip line) — "the asset [ledger] in boost for now" — and the
--                                two sources can never double-count: this line reads ONLY
--                                raw_ma_daily_tx, that one ONLY asset_ledger/PayGo.
--
-- No tenant, carrier or company name appears in code (RULE TWO): both knobs are per-org config
-- with house defaults, reusable by any future tenant.
--
-- MONEY-TOUCHING when a tenant opts in — the org seeds below are COMMENTED OUT behind the owner
-- gate (the mig-622 precedent). The schema change itself moves no number for any org.
--
-- Additive + idempotent. Run in the Supabase SQL editor.

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS inventory_basis TEXT
    CHECK (inventory_basis IS NULL OR inventory_basis IN ('report', 'devices'));

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS handset_payable_order_types JSONB;

COMMENT ON COLUMN commcalc.account_config.inventory_basis IS
  'Balance-Sheet inventory source: NULL/''report'' = emailed Inventory Aging per-store totals '
  '(inventory_value, historical behaviour); ''devices'' = the unsold-phone ledger '
  '(inventory_aging_device on_hand at the current snapshot, at unit_cost). Manual overrides win '
  'under either basis.';
COMMENT ON COLUMN commcalc.account_config.handset_payable_order_types IS
  'raw_ma_daily_tx.order_type families that are marketplace handset purchases; rows in these '
  'families book the handset_payable BS liability while tx_date <= as-of < due_date (the vendor''s '
  'own terms). NULL/[] books nothing (default).';

-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- ⛔ MONEY SEEDS — COMMENTED OUT. Owner GO uncomments and runs this block (mig-622 precedent).
--
-- LuxeLink (org 854f6d7b-…), measured live 2026-09-02:
--   • inventory: report totals $173,057.07 / 20 stores vs unsold-phone ledger $166,020.16 /
--     938 phones at the current snapshot (delta $7,036.91 — report lines the device upsert
--     dropped; visible per store on GET /account/inventory-recon). 556 further on_hand rows carry
--     store=NULL + July as_of dates ($129,454.66) — ghosts the per-store off-hand flip can never
--     retire; the 'devices' basis excludes and REPORTS them instead of overstating inventory.
--   • handset payables: order_type 'Postpaid Branded MarketPlace' = the marketplace phone
--     purchases (2,093 rows, $696,585.25 lifetime); outstanding inside the due-date window as of
--     2026-09-02 = $169,013.57 (643 rows). Today the BS carries NO handset liability at all.
--
-- INSERT INTO commcalc.account_config (org_id, inventory_basis, handset_payable_order_types)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'devices',
--         '["Postpaid Branded MarketPlace"]'::jsonb)
-- ON CONFLICT (org_id) DO UPDATE
--   SET inventory_basis             = EXCLUDED.inventory_basis,
--       handset_payable_order_types = EXCLUDED.handset_payable_order_types,
--       updated_at                  = now();
--
-- After running: recompute the open periods (POST /account/compute/{period}, or wait for
-- /account/run-due) so the stored statements pick the config up.
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 933 complete — account_config.inventory_basis + handset_payable_order_types (defaults byte-identical; org seeds gated)' AS status;

-- REVERT:
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS inventory_basis;
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS handset_payable_order_types;
--   (Dropping the columns restores the pre-933 behaviour everywhere: balance_sheet.load_bs_config
--    degrades to the 'report' basis + empty payable config, byte-identical books.)
