-- 953_carrier_vocabulary_term_presets.sql — CARRIER VOCABULARY TERM presets (owner directive
-- 2026-09-04, verbatim: "the word total wireless cannot be on the boost side and the word Boost
-- cannot be on the total tenant … remove vidapay tettra, edge financing, total wireless, MA handset,
-- MA Commission or MA TX etc from the boost side and then remove MI / asset landing, VIP Wireless,
-- Boost, Dish, Acima from the total side").
--
-- NO NEW TABLE, NO NEW MECHANISM (duplicate-check gate): this EXTENDS the mig-945 carrier label
-- preset system on the EXISTING commcalc.ui_label_override store (mig 068) with a third scope family:
--   scope 'report_term'            at a TENANT org  = that org's vocabulary-term OVERRIDE (key=term)
--   scope 'report_term:<carrier>'  at the HOUSE org = the carrier's vocabulary PRESET     (key=term)
-- Resolution (backend/app/modules/commcalc/report_labels.py LABELABLE_TERMS; proof
-- backend/harness_report_labels.py): tenant override > carrier preset > built-in NEUTRAL default.
-- Shared page copy writes the neutral noun ("payment processor", "distributor", "device financing",
-- "carrier marketplace feed", "POS") and resolves the carrier-specific brand from these rows via
-- GET /commcalc/report-labels (terms map) + frontend lib/report-labels.ts term(). A carrier with no
-- preset (or an org with no carrier row) renders the neutral noun — byte-safe for any future carrier.
--
-- SEEDS (display terminology only, no money, seeded live — same posture as mig 945):
--   boost: processor→'ePay', distributor→'VIP Wireless', financing→'ACIMA', pos_system→'b2bsoft'
--          (today's Boost-side wording, stated explicitly; marketplace_feed NOT seeded — Boost has
--          no marketplace feed, so the neutral noun renders where shared copy must mention one)
--   total: processor→'VidaPay', distributor→'VidaPay / T-CETRA', financing→'Edge',
--          marketplace_feed→'VidaPay/T-CETRA "MA Handset Ordering"'
--          (pos_system NOT seeded — the Total side has no POS-brand vocabulary of its own yet)
-- ON CONFLICT DO NOTHING — a later house-admin edit of a preset row is never clobbered by a re-run.
-- Org-specific tenant OVERRIDES are deliberately NOT seeded (a tenant sets its own via
-- PUT /commcalc/report-labels {terms:{...}}).
--
-- Additive + idempotent. RLS: table already has ENABLE ROW LEVEL SECURITY + policy from mig 068.
-- Display config, not a data feed → NO lineage-registry/seed entry (same posture as mig 945).
--
-- REVERT (paste and run to undo — deletes only the preset rows this migration owns):
--   DELETE FROM commcalc.ui_label_override
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope IN ('report_term:boost','report_term:total');
--   NOTIFY pgrst, 'reload schema';

INSERT INTO commcalc.ui_label_override (org_id, scope, key, label) VALUES
  ('00000000-0000-0000-0000-000000000001', 'report_term:boost', 'processor',   'ePay'),
  ('00000000-0000-0000-0000-000000000001', 'report_term:boost', 'distributor', 'VIP Wireless'),
  ('00000000-0000-0000-0000-000000000001', 'report_term:boost', 'financing',   'ACIMA'),
  ('00000000-0000-0000-0000-000000000001', 'report_term:boost', 'pos_system',  'b2bsoft'),
  ('00000000-0000-0000-0000-000000000001', 'report_term:total', 'processor',   'VidaPay'),
  ('00000000-0000-0000-0000-000000000001', 'report_term:total', 'distributor', 'VidaPay / T-CETRA'),
  ('00000000-0000-0000-0000-000000000001', 'report_term:total', 'financing',   'Edge'),
  ('00000000-0000-0000-0000-000000000001', 'report_term:total', 'marketplace_feed',
     'VidaPay/T-CETRA "MA Handset Ordering"')
ON CONFLICT (org_id, scope, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 953 complete — carrier vocabulary term presets seeded (boost: ePay/VIP Wireless/ACIMA/b2bsoft; total: VidaPay/T-CETRA/Edge/marketplace feed)' AS status;
