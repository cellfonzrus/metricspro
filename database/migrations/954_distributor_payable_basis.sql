-- 954_distributor_payable_basis.sql — the DISTRIBUTOR-PAYABLE tenant mapping (basis +
-- target line + open-status vocabulary) and the carrier presets a new tenant inherits.
-- Owner directives, 2026-09-04, verbatim:
--   (A) "balance sheet in cellfonz rus is showing a wrong figure it should be open balance owed
--        358221.13 not as a hard coded figure but derived, accounts payable for total should come
--        from the open balance Owed to distributor (outstanding) $281,674.04 as of 2026-09-04."
--   (B) "it does show in luxelink but in a different line which is acceptable as all companies have
--        a different way of assigning their cost center."
--   (C) "these should be mapped when setting up the new tenant for proper reporting."
--
-- ── WHAT WAS WRONG (measured live 2026-09-04, both orgs) ───────────────────────────────────────
-- Consolidated Balance Sheet, September 2026 (computed 2026-09-04T04:15Z):
--   house org 00000000-…-0001  owed_vip = $0.00            ← WRONG.  Correct: $358,221.13
--   LuxeLink  854f6d7b-…       handset_payable = $281,674.04 ← already correct, and on a DIFFERENT
--                              line, which is the tenant's own cost-centre choice (directive B).
-- ROOT CAUSE of the $0.00: account/coa.build_inputs books the Boost-side device payable from
-- `asset_ledger` rows whose STATUS reads 'on inventory' — but `status` on that feed only ever
-- carries 'Open' / 'Paid In Full' / NULL (34,015 live rows checked; ZERO match). "On Inventory" is
-- a CATEGORY value ('On Inventory. NET60'), not a status. The predicate can never fire, so the only
-- other contributor to the line — PENDING PayGo batches, also $0.00 today — left it at zero. The
-- $358,221.13 the owner named is Σ owed_to_vip over the 2,505 rows whose status IS 'Open', which is
-- exactly what the asset dashboard's "Open Balance Owed" card has always shown
-- (asset/router.get_asset_summary → total_open_balance). One derivation, two readers.
--
-- ── WHAT THIS MIGRATION ADDS (no new table, no new mechanism) ──────────────────────────────────
-- 1. THREE per-org columns on commcalc.account_config (the mig-611/933/938 finance-config table),
--    resolved by account/balance_sheet.load_bs_config and consumed by account/statement_engine:
--      distributor_payable_basis   'off' | 'asset_ledger' | 'marketplace_due'.  NULL = NOT DECLARED
--                                  ⇒ resolve from the org's CARRIER preset (row 2 below), then from
--                                  a declared mig-933 marketplace family, then 'off'.
--      distributor_payable_line    which BS liability line the basis books to. NULL = the basis
--                                  default ('asset_ledger'→owed_vip, 'marketplace_due'→
--                                  handset_payable) = today's live placement on both orgs, so
--                                  directive B is honoured by DEFAULT: LuxeLink's line does not move.
--      asset_ledger_open_statuses  the ledger status vocabulary meaning "still owed". NULL ⇒ the
--                                  house default ["Open"]. Deliberately a POSITIVE vocabulary: one
--                                  live house row (id 889865) carries status NULL with $117,730.73
--                                  and no dates/store/category — a ledger-upload artifact that a
--                                  "not settled" negation would have swept into the books.
-- 2. CARRIER PRESETS for the basis, as HOUSE-org rows in the EXISTING commcalc.ui_label_override
--    scope-multiplexed store (mig 068), scope 'finance_basis:<carrier code>', key
--    'distributor_payable' — the SAME preset mechanism and the SAME carrier normalizer
--    (report_labels.normalize_carrier_code / carrier_codes) that mig 945 and mig 953 use. This is
--    what makes directive C automatic: a NEW tenant that picks its carrier in the onboarding
--    "Carrier Selection" step (commcalc.carrier, mig 038) resolves to the right basis the first
--    time a statement is built — no setup hook, no code branch, tenant-overridable at any time.
--    Precedence (pure, proven in harness_balance_sheet_truths.py):
--        org column  >  carrier preset  >  declared mig-933 families  >  'off'.
--
-- AS-OF (owner directive A): ONE function per side, parameterized by date. The Balance Sheet asks
-- it for `statement_engine.period_as_of(period)` (the period's last day, capped at today — so the
-- CURRENT period asks for today and a CLOSED period asks for that period end) and the
-- liabilities-due tile asks the SAME function for today. There is no second formula.
-- The asset-ledger side reports its own limitation instead of hiding it: that feed is a
-- wipe-and-reinsert CURRENT snapshot with no settlement date, so an as-of in the past is a
-- snapshot-basis estimate and the statement meta says so (basis 'status_snapshot', snapshot_lag).
--
-- ⚠ MONEY-TOUCHING for the HOUSE org: applying the seeds below moves consolidated `owed_vip` from
--   $0.00 to $358,221.13 (2,505 open ledger rows; $29,839.62 of it already past its own due date,
--   $328,381.51 not yet due) once the open periods are recomputed. LuxeLink's seed is a PIN, not a
--   change: it states explicitly the basis and line that org already resolves to and already books
--   ($281,674.04 on `handset_payable`, byte-identical). Both orgs are seeded here rather than left
--   to the lazy carrier preset precisely because directive C asks for the mapping to be EXPLICIT at
--   tenant setup — the seed pins what should already be true, so a preset edit later cannot move a
--   live tenant's books by accident.
--
-- NOT IN THIS MIGRATION, deliberately: the owner's cash-at-bank GRAIN directive of the same day
-- ("an option to enter the cash per store or cash per company or overall total to each tenant")
-- needs NO schema. The three grains are the three ways commcalc.journal_entries can already be
-- addressed (store_address / company_id / neither); what shipped alongside this migration is the
-- ROLLUP rule that keeps them honest — balance_sheet.journal_grain_entries nets a coarser entry by
-- the finer entries nested inside it, so a tenant total keyed next to per-store rows is counted
-- once, not twice. One grain in use (every live row on both orgs today) is byte-identical.
--
-- Additive + idempotent. Config columns + display-scope preset rows, no new data feed ⇒ no
-- lineage-registry entry (the mig-945/953 posture).
--
-- REVERT (paste and run to undo):
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS distributor_payable_basis;
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS distributor_payable_line;
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS asset_ledger_open_statuses;
--   DELETE FROM commcalc.ui_label_override
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope IN ('finance_basis:boost','finance_basis:total')
--      AND key = 'distributor_payable';
--   NOTIFY pgrst, 'reload schema';

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS distributor_payable_basis TEXT
    CHECK (distributor_payable_basis IS NULL
           OR distributor_payable_basis IN ('off', 'asset_ledger', 'marketplace_due'));

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS distributor_payable_line TEXT;

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS asset_ledger_open_statuses JSONB;

COMMENT ON COLUMN commcalc.account_config.distributor_payable_basis IS
  'Which tenant feed answers "what do we still owe the distributor for devices, as of a date": '
  '''asset_ledger'' = the consignment ledger''s OPEN rows (commcalc.asset_ledger.owed_to_vip); '
  '''marketplace_due'' = the marketplace handset feed inside the vendor''s own due-date window '
  '(mig 933); ''off'' = book nothing. NULL = not declared -> resolve from the org''s carrier preset '
  '(ui_label_override scope ''finance_basis:<carrier>''), then from a declared mig-933 family, then off.';
COMMENT ON COLUMN commcalc.account_config.distributor_payable_line IS
  'Balance-Sheet LIABILITY line key the distributor payable books to — a per-tenant cost-centre '
  'choice. NULL = the basis default (asset_ledger -> owed_vip, marketplace_due -> handset_payable), '
  'which is today''s live placement on every org.';
COMMENT ON COLUMN commcalc.account_config.asset_ledger_open_statuses IS
  'commcalc.asset_ledger.status values that mean STILL OWED, for the ''asset_ledger'' basis. '
  'NULL/[] = the house default ["Open"]. A positive vocabulary on purpose — negating the settled '
  'set would sweep in undated/statusless ledger artifacts.';

-- ── carrier PRESETS (house-org rows; the mig-945/953 preset family) ───────────────────────────
INSERT INTO commcalc.ui_label_override (org_id, scope, key, label) VALUES
  ('00000000-0000-0000-0000-000000000001', 'finance_basis:boost', 'distributor_payable', 'asset_ledger'),
  ('00000000-0000-0000-0000-000000000001', 'finance_basis:total', 'distributor_payable', 'marketplace_due')
ON CONFLICT (org_id, scope, key) DO NOTHING;

-- ── the two LIVE tenants, mapped EXPLICITLY (directive C) ─────────────────────────────────────
-- House / CellfonzRUs (carrier 'Boost Mobile'): the asset-ledger open balance, on the existing
-- owed_vip line. MOVES MONEY on recompute: $0.00 -> $358,221.13.
INSERT INTO commcalc.account_config (org_id, distributor_payable_basis, asset_ledger_open_statuses)
VALUES ('00000000-0000-0000-0000-000000000001', 'asset_ledger', '["Open"]'::jsonb)
ON CONFLICT (org_id) DO UPDATE
  SET distributor_payable_basis  = EXCLUDED.distributor_payable_basis,
      asset_ledger_open_statuses = EXCLUDED.asset_ledger_open_statuses,
      updated_at                 = now();

-- LuxeLink (carrier 'Total Wireless'): the marketplace due-date window, on the handset_payable
-- line it already uses. PIN ONLY — byte-identical ($281,674.04 as of 2026-09-04).
INSERT INTO commcalc.account_config (org_id, distributor_payable_basis, distributor_payable_line)
VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', 'marketplace_due', 'handset_payable')
ON CONFLICT (org_id) DO UPDATE
  SET distributor_payable_basis = EXCLUDED.distributor_payable_basis,
      distributor_payable_line  = EXCLUDED.distributor_payable_line,
      updated_at                = now();

-- After running: recompute the OPEN periods so the stored statements pick it up —
-- POST /account/compute/{period} for September 2026 (both orgs) and August 2026 (house org, whose
-- owed_vip line was $0.00 there too), or wait for the /account/run-due sweep.

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 954 complete — distributor_payable basis/line/open-status mapping + boost/total carrier presets; house org owed_vip becomes the $358,221.13 asset-ledger open balance on recompute' AS status;
