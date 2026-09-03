-- 945_report_column_label_presets.sql — CARRIER PRESETS for report column labels + report-banner
-- terminology (owner directive 2026-09-02, verbatim asks: the Boost side must not see the
-- "…lower than the b2bsoft MTD report…" warning terminology; the column that says "Edge" must say
-- "ACIMA" on the Boost side; and there must be a stored, per-carrier label preset a NEW tenant
-- inherits automatically when it specifies its carrier — which the tenant can then change).
--
-- NO NEW TABLE (duplicate-check gate): this reuses the EXISTING commcalc.ui_label_override
-- display-label store (mig 068 — already multiplexed by scope: 'nav'/'group'/'cap'/'layout'/'tiles')
-- with two new scope families:
--   scope 'report_col'             at a TENANT org  = that org's column-label OVERRIDE  (key=column)
--   scope 'report_col:<carrier>'   at the HOUSE org = the carrier PRESET                (key=column)
--   scope 'report_banner'          at a TENANT org  = that org's banner OVERRIDE  (label 'on'|'off')
--   scope 'report_banner:<carrier>'at the HOUSE org = the carrier's banner PRESET (label 'on'|'off')
-- Resolution (backend/app/modules/commcalc/report_labels.py, pure; proof
-- backend/harness_report_labels.py):  tenant override > carrier preset > built-in default.
-- Auto-assign is LAZY: the resolver keys the preset off the org's commcalc.carrier rows (mig 038,
-- written by the onboarding "Carrier Selection" step) — no setup hook needed; an org with no
-- carrier row or no preset rows renders the built-in labels, byte-identical to today.
--
-- SEEDS (house-level label DATA — display terminology only, no money, so seeded live):
--   boost: edge → 'ACIMA' (Boost-side device financing program) ;  unrecognized_ct_recon banner OFF
--          (the b2bsoft-MTD reconciliation warning is meaningless terminology on the Boost side)
--   total: edge → 'Edge'  (explicit statement of today's label)  ;  unrecognized_ct_recon banner ON
--          (byte-identical for LuxeLink / every Total-side org)
-- ON CONFLICT DO NOTHING — a later house-admin edit of a preset row is never clobbered by a re-run.
--
-- Org-specific tenant OVERRIDES are deliberately NOT seeded (owner gate — a tenant sets its own via
-- PUT /commcalc/report-labels). Example, kept commented per house convention:
--   -- INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
--   -- VALUES ('<tenant org uuid>', 'report_col', 'edge', 'ACIMA')
--   -- ON CONFLICT (org_id, scope, key) DO NOTHING;
--
-- Additive + idempotent. RLS: table already has ENABLE ROW LEVEL SECURITY + policy from mig 068.
-- Display config, not a data feed → NO lineage-registry/seed entry (same posture as scope='tiles').
--
-- REVERT (paste and run to undo — deletes only the four preset rows this migration owns):
--   DELETE FROM commcalc.ui_label_override
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope IN ('report_col:boost','report_col:total','report_banner:boost','report_banner:total');
--   NOTIFY pgrst, 'reload schema';

INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
VALUES ('00000000-0000-0000-0000-000000000001', 'report_col:boost', 'edge', 'ACIMA')
ON CONFLICT (org_id, scope, key) DO NOTHING;

INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
VALUES ('00000000-0000-0000-0000-000000000001', 'report_col:total', 'edge', 'Edge')
ON CONFLICT (org_id, scope, key) DO NOTHING;

INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
VALUES ('00000000-0000-0000-0000-000000000001', 'report_banner:boost', 'unrecognized_ct_recon', 'off')
ON CONFLICT (org_id, scope, key) DO NOTHING;

INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
VALUES ('00000000-0000-0000-0000-000000000001', 'report_banner:total', 'unrecognized_ct_recon', 'on')
ON CONFLICT (org_id, scope, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 945 complete — carrier report-label presets seeded (boost: edge→ACIMA, ct-gap banner off; total: byte-identical explicit)' AS status;
