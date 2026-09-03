-- 947_commission_discrepancy_hub.sql — Incentive-dashboard restructure + Commission Discrepancy hub
-- (owner directive 2026-09-03, verbatim: "incentive Dashboard start with 2 broad categories,
-- Carrier Commission - anything to do with the way the tenant gets paid from the carrier should be
-- under that and that should have commission received and reconciliation tiles, the other tile
-- under incentive dashboard will be employee incentive, that is where the employees get paid. new
-- tile for Commission Discrepancy which will include any reports or query for the commission not
-- received and the appeals which need to be done. ... Commission legs to be renamed as Commission
-- received over M1-M12").
--
-- Additive + idempotent + safe to re-run. THREE pieces, NO new table (duplicate-check gate):
--
--   a. APPEAL STATE on the EXISTING commcalc.discrepancy_results rows (mig 312 canonical DDL) —
--      four nullable columns so management can mark a commission-not-received row
--      "appeal filed / appeal won / appeal denied / written off" with who/when. The rows stay the
--      two engines' output (source='boost'/'ma'); the appeal workflow only ANNOTATES them.
--      State machine + patch builder are PURE in backend/app/modules/commcalc/discrepancy_appeals.py
--      (proof backend/harness_discrepancy_appeals.py); endpoints GET /commcalc/discrepancy-appeals +
--      PATCH /commcalc/discrepancy-appeals/{row_id}. The DENIED-appeal claw-back pipeline
--      (mig 098 appeal_recovery/appeal_claim, /recovery/*) is a different lifecycle and is REUSED
--      (the hub links its open-claims chase list), never re-derived.
--
--   b. HOUSE tile layout for the Incentives dashboard (dashboard-builder D1 storage, mig 068
--      scope='tiles') — the owner's two broad categories + the Commission Discrepancy tile, shipped
--      as the PLATFORM-DEFAULT row every tenant inherits and may override in the Dashboard Designer
--      (tenant row > house row, tile_layout.resolve_tile_layout). Config, not code: no frontend
--      layout is hardcoded for this. ON CONFLICT DO NOTHING — a house-admin's later design in the
--      Designer is never clobbered by a re-run.
--
--   c. RELABEL "Commission Legs" -> "Commission received over M1-M12" as label DATA (mig 068/945
--      display-label machinery): a HOUSE preset row under the NEW scope 'nav_default' (house-org
--      platform default nav label; tenant scope='nav' rows override per mig 068's existing
--      per-tenant nickname path — resolution wired in router.get_nav_config, tenant > house >
--      built-in). Same preset-at-house pattern as mig 945's 'report_col:<carrier>' scopes.
--
-- RULE TWO: no carrier or tenant name appears in any tile title, state name, or label below.
-- 💰 MONEY POSTURE: no payout is computed or mutated. The appeal columns are workflow annotations;
-- the tile/label rows are display config. Display config, not a data feed → NO lineage-registry
-- entry (same posture as mig 945 / scope='tiles').
--
-- REVERT (paste and run to undo):
--   ALTER TABLE commcalc.discrepancy_results
--     DROP COLUMN IF EXISTS appeal_status, DROP COLUMN IF EXISTS appeal_note,
--     DROP COLUMN IF EXISTS appealed_by,  DROP COLUMN IF EXISTS appealed_at;
--   DELETE FROM commcalc.ui_label_override
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND ((scope = 'tiles' AND key = 'incentives')
--        OR (scope = 'nav_default' AND key = '/commcalc/commission-legs'));
--   NOTIFY pgrst, 'reload schema';

-- ── a. appeal state on discrepancy_results ────────────────────────────────────────────────────────
ALTER TABLE commcalc.discrepancy_results
  ADD COLUMN IF NOT EXISTS appeal_status TEXT,
  ADD COLUMN IF NOT EXISTS appeal_note   TEXT,
  ADD COLUMN IF NOT EXISTS appealed_by   TEXT,
  ADD COLUMN IF NOT EXISTS appealed_at   TIMESTAMPTZ;

COMMENT ON COLUMN commcalc.discrepancy_results.appeal_status IS
  'Commission Discrepancy hub (mig 947): appeal workflow state — appeal_filed | appeal_won | '
  'appeal_denied | written_off; NULL = no appeal activity (the honest default on every engine-'
  'written row). Transitions validated by discrepancy_appeals.validate_transition (pure, proof '
  'harness_discrepancy_appeals.py); written only by PATCH /commcalc/discrepancy-appeals/{row_id}.';
COMMENT ON COLUMN commcalc.discrepancy_results.appeal_note IS
  'Commission Discrepancy hub: free-text note attached with the last appeal transition (max 2000).';
COMMENT ON COLUMN commcalc.discrepancy_results.appealed_by IS
  'Commission Discrepancy hub: auth uid of the person who set the current appeal state '
  '(_caller_uid; ''web'' when unresolved). NULL when no appeal state.';
COMMENT ON COLUMN commcalc.discrepancy_results.appealed_at IS
  'Commission Discrepancy hub: when the current appeal state was set (UTC).';

CREATE INDEX IF NOT EXISTS discrepancy_results_org_appeal
  ON commcalc.discrepancy_results (org_id, appeal_status)
  WHERE appeal_status IS NOT NULL;

-- ── b. house Incentives tile layout (2 broad categories + Commission Discrepancy tile) ───────────
-- Shape = tile_layout.sanitize_tile_layout's canonical {version:1, tiles:[{title, icon?, desc?,
-- items:[{href}]}]}. Items carry NO label override: each tile link renders its NAV label, so the
-- 'Commission received over M1-M12' relabel (piece c + rbac.ts built-in) flows through here too,
-- and later tenant nicknames keep working. Pages a tenant's RBAC hides drop from the tile at
-- render; a page shipped later auto-appends to a trailing 'More' tile (mergeUnplacedItems).
INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
VALUES ('00000000-0000-0000-0000-000000000001', 'tiles', 'incentives', '{"version":1,"tiles":[
  {"title":"Carrier Commission - Received","icon":"📡",
   "desc":"How the company is paid by the carrier - commission received, month-of-life legs, ledger and rebates",
   "items":[{"href":"/commcalc/ma-commission"},{"href":"/commcalc/commission-legs"},
            {"href":"/commcalc/commission-ledger"},{"href":"/commcalc/expected-commission"},
            {"href":"/commcalc/imei-rebates"},{"href":"/commcalc/ma-handsets"},
            {"href":"/commcalc/device-cost-recon"}]},
  {"title":"Carrier Commission - Reconciliation","icon":"🧾",
   "desc":"Carrier-payment reconciliation - sold vs paid cross-checks and clawbacks",
   "items":[{"href":"/commcalc/ma-overview-recon"},{"href":"/commcalc/carrier-recon"},
            {"href":"/commcalc/sales-recon"},{"href":"/commcalc/epay-fee-recon"},
            {"href":"/commcalc/imei-recon"},{"href":"/commcalc/chargebacks"}]},
  {"title":"Employee Incentive","icon":"🧑‍💼",
   "desc":"How employees get paid - rep incentives, daily pay, simulators and pay review queues",
   "items":[{"href":"/commcalc/reports"},{"href":"/commcalc/daily-commission"},
            {"href":"/commcalc/pay-simulator"},{"href":"/commcalc/comp-trend"},
            {"href":"/commcalc/flags"},{"href":"/commcalc/accessory-flags"},
            {"href":"/commcalc/accessory-cost-audit"},{"href":"/commcalc/coaching"},
            {"href":"/training"}]},
  {"title":"Commission Discrepancy","icon":"⚖️",
   "desc":"Commission not received - discrepancy reports, appeals to file and the recovery chase list",
   "items":[{"href":"/commcalc/commission-discrepancy"},{"href":"/commcalc/discrepancy"},
            {"href":"/commcalc/recovery"}]},
  {"title":"Sales & Performance","icon":"📈",
   "desc":"Sales reporting and analytics feeding the incentive numbers",
   "items":[{"href":"/commcalc"},{"href":"/commcalc/sales-report"},{"href":"/commcalc/sales-comparison"},
            {"href":"/commcalc/custom-report"},{"href":"/commcalc/exec"},{"href":"/commcalc/exec/mtd"},
            {"href":"/commcalc/activations"},{"href":"/commcalc/kpi"},{"href":"/commcalc/device-history"},
            {"href":"/commcalc/productivity"},{"href":"/commcalc/productivity-insights"},
            {"href":"/commcalc/sales-analyzer"},{"href":"/commcalc/whatif"}]},
  {"title":"Setup & Tools","icon":"🧭",
   "desc":"Module setup, schematic and directories",
   "items":[{"href":"/commcalc/onboarding"},{"href":"/commcalc/schematic"},
            {"href":"/commcalc/reports-index"},{"href":"/commcalc/agency"}]}
]}')
ON CONFLICT (org_id, scope, key) DO NOTHING;

-- ── c. house nav-label preset: Commission Legs -> Commission received over M1-M12 ────────────────
-- scope 'nav_default' at the HOUSE org = the platform-default label for one nav href; a tenant's
-- own scope='nav' nickname (mig 068) still overrides it. Resolution: router.get_nav_config.
INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
VALUES ('00000000-0000-0000-0000-000000000001', 'nav_default', '/commcalc/commission-legs',
        'Commission received over M1-M12')
ON CONFLICT (org_id, scope, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 947 complete — discrepancy appeal columns + house Incentives tile layout + Commission received over M1-M12 label preset' AS status;
