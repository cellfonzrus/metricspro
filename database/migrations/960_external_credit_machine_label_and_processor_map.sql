-- 960_external_credit_machine_label_and_processor_map.sql — the EXTERNAL CREDIT MACHINE field's
-- per-tenant LABEL preset + the declared-tender → processor-role map the settlement tally reads
-- (owner directive 2026-09-04).
--
-- ── OWNER, VERBATIM ────────────────────────────────────────────────────────────────────────────
-- "need to add another field on daily closing as external credit machine - (label should be changed
--  to be renamed as White machine for these tenants but remain as external credit card for other
--  tenants)"
--
-- ── DUPLICATE CHECK (build gate, CLAUDE.md 2026-09-02 / owner 2026-09-04 "check against index if we
--    already have anything made to avoid duplicity") ──────────────────────────────────────────────
-- 1. THE FIELD ALREADY EXISTS. `commcalc.daily_closing.t_ext_cc` — "External Credit Card (separate
--    terminal)" — has been a physical column since mig `103_closing_tender_recon.sql`, is written by
--    the closing submit form / POST /closing/row / POST /closing/attempt, read by
--    `closing/router._row_display_tenders`, summed into `/closing/summary` totals + `/closing/
--    submissions`, and is ALREADY inside the card base of the mig-939 bill-pay coverage recon
--    (`commcalc.router._closing_collected_by_store_day`) and the mig-944 3-way recon
--    (`closing/router.cash_recon_management`). NO NEW COLUMN IS CREATED HERE, and NO booked total is
--    moved — see the money note below.
-- 2. THE LABEL MECHANISM ALREADY EXISTS. Migs `945` (report_col/report_banner) + `953` (report_term)
--    built the carrier→label PRESET machinery on the EXISTING `commcalc.ui_label_override` store
--    (mig 068), resolved by `commcalc/report_labels.py` as
--    tenant override > house carrier preset > built-in default, auto-assigned LAZILY off the org's
--    `commcalc.carrier` rows (mig 038 — the onboarding "Carrier Selection" step). This migration
--    REUSES it verbatim: one NEW registry key on the EXISTING `report_col` scope family. No second
--    labelling mechanism, no new table, no code branch on a tenant or carrier name (RULE TWO).
-- 3. THE STORE↔MERCHANT-ID RESOLUTION ALREADY EXISTS: `storeops.store_merchant_id` (mig `902`,
--    free-form `processor` key) — the settlement tally resolves a scraped row's merchant id to a
--    store through it, exactly as the ePay/VidaPay feeds do. No new mapping table.
-- 4. THE SCRAPED-FEED LOCATION ALREADY HAS A REGISTRY: `commcalc.report_pull_map` (mig `207` —
--    report_key → target_table + column_map, org row overriding the house row). The tally resolves
--    the settlement feed's table/columns through it, so no table name is hardcoded anywhere and the
--    portal-scrape side (sibling work, migs 955-959) registers its own row.
--
-- ── WHAT THIS MIGRATION ADDS ───────────────────────────────────────────────────────────────────
-- (A) HOUSE CARRIER LABEL PRESETS for the NEW labelable column key `closing_t_ext_cc`
--     (registered in `report_labels.LABELABLE_COLUMNS`, built-in default 'External Credit Card').
--     Boost and Total tenants inherit 'White machine' the moment their carrier row exists; every
--     other carrier — and any org with no carrier row — renders the built-in 'External Credit Card',
--     byte-identical to today. A tenant overrides its own wording via
--     PUT /commcalc/report-labels {columns:{closing_t_ext_cc:'…'}} ("they can change if they want").
--     Vocabulary safety: 'White machine' is carrier-NEUTRAL wording, so it crosses no side under the
--     mig-953 rule (harness_carrier_vocab_guard.py).
--
-- (B) `commcalc.closing_tender_def.processor_key` — WHICH processor role's scraped settlement totals
--     a declared tender field tallies against. Values are neutral ROLE slugs, never brands:
--       'external_cc'  — the standalone third-party credit machine (the field above)
--       'pos_merchant' — the POS-integrated card processor
--     NULL / no row  ⇒ the house default map in
--     `closing/external_credit_recon.DEFAULT_TENDER_PROCESSOR` ({'ext_cc':'external_cc',
--     'credit':'pos_merchant'}), so an org that never opens the tender editor tallies correctly with
--     zero config. The BRAND behind a role ('PayAnywhere', the POS provider) is data — it lives in
--     `commcalc.data_source.processor` / `storeops.store_merchant_id.processor` /
--     `report_pull_map`, never in code and never in this column.
--
-- ── MONEY ──────────────────────────────────────────────────────────────────────────────────────
-- NONE. (A) is display terminology; (B) is a recon-routing slug. No P&L line, no balance-sheet line,
-- no payout and no closing total changes value because of this migration. Specifically NOT changed:
-- `t_ext_cc` stays inside the card/credit base of the mig-939 coverage recon and the mig-944 3-way
-- recon exactly as it is today (verified 2026-09-04: `_closing_collected_by_store_day` card =
-- t_credit|store_cc + t_ext_cc + epay_cc; `cash_recon_management` credit = the same expression) —
-- excluding it would move a booked comparison base, so it is deliberately left alone.
--
-- Additive + idempotent. RLS: `ui_label_override` carries ENABLE ROW LEVEL SECURITY + policy from
-- mig 068; `closing_tender_def` from mig 111. Display/recon config, not a data feed → NO
-- data_lineage_registry / 925 seed entry (same posture as migs 945 / 953).
--
-- OPTIONAL per-org TOLERANCE for the tally — the EXISTING mig-923 config table, no schema change.
-- Left COMMENTED (house default 0.00 = exact match, applied in code) for owner approval:
--   -- INSERT INTO commcalc.metric_source_of_truth (org_id, metric, source, enabled, tolerance)
--   -- VALUES ('<tenant org uuid>', 'card_settlement', 'closing_declared', true, 1.00)
--   -- ON CONFLICT (org_id, metric) DO UPDATE SET tolerance = EXCLUDED.tolerance;
--
-- REVERT (paste and run to undo — drops only what this migration owns):
--   DELETE FROM commcalc.ui_label_override
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope IN ('report_col:boost','report_col:total')
--      AND key = 'closing_t_ext_cc';
--   ALTER TABLE commcalc.closing_tender_def DROP COLUMN IF EXISTS processor_key;
--   NOTIFY pgrst, 'reload schema';

-- ── (A) house carrier label presets for the external-credit-machine closing field ────────────────
INSERT INTO commcalc.ui_label_override (org_id, scope, key, label) VALUES
  ('00000000-0000-0000-0000-000000000001', 'report_col:boost', 'closing_t_ext_cc', 'White machine'),
  ('00000000-0000-0000-0000-000000000001', 'report_col:total', 'closing_t_ext_cc', 'White machine')
ON CONFLICT (org_id, scope, key) DO NOTHING;

-- ── (B) declared tender → processor ROLE map (per-org override of the house default in code) ─────
ALTER TABLE commcalc.closing_tender_def
  ADD COLUMN IF NOT EXISTS processor_key TEXT;

COMMENT ON COLUMN commcalc.closing_tender_def.processor_key IS
  'Neutral processor ROLE slug whose scraped settlement totals this declared tender is tallied '
  'against by GET /closing/external-credit-recon: ''external_cc'' (standalone third-party credit '
  'machine) | ''pos_merchant'' (POS-integrated card processor). NULL = the house default map in '
  'closing/external_credit_recon.DEFAULT_TENDER_PROCESSOR. Never a processor BRAND — the brand '
  'lives in data_source.processor / store_merchant_id.processor / report_pull_map (RULE TWO).';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 960 complete — external-credit-machine label presets seeded per carrier + closing_tender_def.processor_key' AS status;
