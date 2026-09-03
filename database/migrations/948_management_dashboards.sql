-- 948_management_dashboards.sql — Management Overview Dashboard + Flags & Compliance Dashboard
-- (owner directive 2026-09-03, verbatim: "create a new category as the Management Overview
-- Dashboard, that will have Tiles like sales reports, Executive MTD, Sales Comparison, Owner
-- Overview tile relabeled as Rep Incentive … Create new report from the KPI for the failing KPI …
-- bring the Cash at hand report in this also as a copy of the same report, Current monetary
-- liabilities tile … Flags and Compliance should be a separate Dashboard and every flag and
-- compliance issue should be under that").
--
-- Additive + idempotent + safe to re-run. TILE DATA ONLY — no new table, no money change:
-- dashboard categories/tiles are dashboard-builder D1 CONFIG (mig 068 commcalc.ui_label_override,
-- scope='tiles'), shipped as HOUSE platform-default rows every tenant inherits and may override in
-- the Dashboard Designer (tenant row > house row, tile_layout.resolve_tile_layout). ON CONFLICT DO
-- NOTHING — a house-admin's later design in the Designer is never clobbered by a re-run (the
-- mig-947 Incentives-seed pattern verbatim).
--
--   a. scope='tiles' key='management-overview' — the Management Overview Dashboard
--      (/hub/management-overview): one tile per report, direct-navigating. The Owner Overview
--      item carries the LABEL OVERRIDE "Rep Incentive" as tile-layout DATA (the mig-068 display-
--      label doctrine — layout item label > NAV label; nothing hardcoded, tenant-editable).
--      "Cash at Hand" is the EXISTING /closing/store-cash-on-hand report surfaced here as a
--      second placement of the same page — same endpoint/component, no forked derivation.
--   b. scope='tiles' key='flags-compliance' — the Flags & Compliance Dashboard (/compliance):
--      every flag/exception/compliance queue grouped under one roof; counts come from
--      GET /commcalc/compliance-summary (a thin count pass over the same queues' own queries).
--
-- The two NAV groups themselves ship in rbac.ts (the D2 convention — groups live in NAV, tile
-- CONTENT is config); every item is a tileOnly duplicate keeping its original module + scopes, so
-- RBAC/gating is unchanged (zero-RBAC-change regroup, the Incentive Payout Plans precedent).
-- RULE TWO: no carrier/tenant name below. Display config, not a data feed → NO lineage entry.
--
-- REVERT (paste and run to undo):
--   DELETE FROM commcalc.ui_label_override
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope = 'tiles' AND key IN ('management-overview', 'flags-compliance');
--   NOTIFY pgrst, 'reload schema';

-- ── a. house Management Overview tile layout ─────────────────────────────────────────────────────
INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
VALUES ('00000000-0000-0000-0000-000000000001', 'tiles', 'management-overview', '{"version":1,"tiles":[
  {"title":"Sales Reports","icon":"🧾",
   "desc":"The daily sales report - counts, classification and drill-downs",
   "items":[{"href":"/commcalc/sales-report"}]},
  {"title":"Executive MTD","icon":"📅",
   "desc":"Month-to-date executive activation metrics per store and market",
   "items":[{"href":"/commcalc/exec/mtd"}]},
  {"title":"Sales Comparison","icon":"📈",
   "desc":"Month-over-month and year-over-year change per item sold",
   "items":[{"href":"/commcalc/sales-comparison"}]},
  {"title":"Rep Incentive","icon":"🏆",
   "desc":"The month at a glance - sales, incentives, targets and KPIs on one page",
   "items":[{"href":"/commcalc/exec","label":"Rep Incentive"}]},
  {"title":"Failing KPIs","icon":"🎯",
   "desc":"High-level overview of every KPI below target, with store and rep drill-down",
   "items":[{"href":"/commcalc/kpi-failing"}]},
  {"title":"Cash at Hand","icon":"🏦",
   "desc":"Cash sitting in each store - the Store Cash on Hand report",
   "items":[{"href":"/closing/store-cash-on-hand","label":"Cash at Hand"}]},
  {"title":"Current Monetary Liabilities","icon":"💳",
   "desc":"Owed to distributor, payments due this week, payroll and payroll tax due, rents and recurring expenses due",
   "items":[{"href":"/accounts/liabilities-due"}]}
]}')
ON CONFLICT (org_id, scope, key) DO NOTHING;

-- ── b. house Flags & Compliance tile layout ──────────────────────────────────────────────────────
INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
VALUES ('00000000-0000-0000-0000-000000000001', 'tiles', 'flags-compliance', '{"version":1,"tiles":[
  {"title":"Commission Flags","icon":"🚩",
   "desc":"Flag queues raised by the commission engines and accessory pricing audits",
   "items":[{"href":"/commcalc/flags"},{"href":"/commcalc/accessory-flags"},
            {"href":"/commcalc/chargebacks"}]},
  {"title":"Pay Discrepancy","icon":"⚖️",
   "desc":"Commission not received - open discrepancy items, appeals and the recovery chase list",
   "items":[{"href":"/commcalc/commission-discrepancy"},{"href":"/commcalc/discrepancy"},
            {"href":"/commcalc/recovery"}]},
  {"title":"Data Quality & Ingest","icon":"🛡️",
   "desc":"Quarantined ingest rows, feed health and failure logs",
   "items":[{"href":"/commcalc/ingest-guard"},{"href":"/admin/import-health"},
            {"href":"/failures"}]},
  {"title":"Workforce Compliance","icon":"🚨",
   "desc":"Attendance exceptions, lateness patterns and hours awaiting approval",
   "items":[{"href":"/storeops/attendance"},{"href":"/storeops/accountability"},
            {"href":"/storeops/payroll/approvals"},{"href":"/approvals"}]},
  {"title":"Cash & Closing Compliance","icon":"🧾",
   "desc":"Deposit accountability, envelope counts and tender reconciliation exceptions",
   "items":[{"href":"/closing/deposit-recon"},{"href":"/closing/envelope-report"},
            {"href":"/closing/tender-recon-3way"},{"href":"/closing/recon"}]}
]}')
ON CONFLICT (org_id, scope, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
