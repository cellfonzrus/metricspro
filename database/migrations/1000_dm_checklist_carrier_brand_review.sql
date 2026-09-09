-- 1000_dm_checklist_carrier_brand_review.sql — THE CARRIER BRAND-REVIEW QUESTIONS JOIN THE DM VISIT.
--
-- Owner directive, 2026-09-09, verbatim: "Make the following a part of the dm checklist and replace
-- boost to carrier" — followed by a carrier Brand Resolution Visit form (six sections, 24 questions).
--
-- ── NO NEW MECHANISM. THIS IS CONFIG. ─────────────────────────────────────────────────────────
-- `storeops.checklist_items` (mig 027) is ALREADY the configurable, management-editable DM visit
-- checklist: item_key / label / category / input_type / sort_order / is_active, org-scoped, read by
-- GET /storevisit/checklist-items and rendered by /storeops/visits/new. A visit snapshots the label
-- and category onto each response (`label_snapshot`, `category_snapshot`), so editing an item later
-- never rewrites a visit already filed. Nothing here adds a table, an endpoint or a second checklist
-- — these are rows in the list that already exists, seeded for the HOUSE org and inherited from
-- there exactly like the mig-027 defaults above them.
--
-- ── RULE TWO: THE CARRIER IS NEVER NAMED ──────────────────────────────────────────────────────
-- The source form names one carrier in fifteen questions ("Boost Signage Family standards", "Boost
-- U", "Boost Mobile Return Policy"). Per the owner's instruction and RULE TWO, every one reads
-- `carrier` here. These labels are TENANT-EDITABLE TEXT, not code: an org that wants its carrier's
-- real name on the form types it in Visit Settings, and no other tenant sees it. Seeding a brand
-- name instead would put one carrier's vocabulary in front of every tenant on the platform.
--
-- ── THE DEFECT THIS SEED WOULD HAVE HIT, FIXED IN THE SAME CHANGE ─────────────────────────────
-- `visits/new/page.tsx` grouped the checklist by mapping over a FIXED list of six categories, so an
-- item whose category was not one of those six was filtered out of every group and never rendered.
-- The API accepts any category string, so config and screen could disagree in silence. Three of this
-- form's sections (customer experience, merchandise/brand, employee experience) are new categories,
-- and seeding them alone would have written 12 questions into the database that the DM never sees.
-- The page now renders an unknown category under the trailing group instead of dropping it, and both
-- the visit form and Visit Settings carry the three new categories.
--
-- Six sections, 24 questions. Sort orders start at 200 so the mig-027 day-to-day checks (10..160)
-- stay at the top of a visit: the routine questions are asked every time, the brand review is the
-- longer tail beneath them.
--
-- ⚠ MONEY: none. No payout, ledger, statement or commission figure reads storeops.checklist_items.
--
-- Proof: backend/harness_dm_checklist_carrier_review.py — the seed parsed OUT of this file (so it
-- cannot pass against a seed this migration does not contain), every seeded category present in the
-- page's CATS, the unknown-category fallback, and no carrier brand name in any label.
--
-- REVERT:
--   DELETE FROM storeops.checklist_items
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND item_key LIKE 'qa\_%';
--   -- Historical visits are UNAFFECTED: store_visit_responses carries label_snapshot and
--   -- category_snapshot, so a filed visit keeps the wording it was filed under.

INSERT INTO storeops.checklist_items (org_id, item_key, label, category, input_type, sort_order) VALUES
 -- General
 ('00000000-0000-0000-0000-000000000001','qa_visit_notes','Overall visit comments / notes','general','text',200),
 ('00000000-0000-0000-0000-000000000001','qa_infraction_photos','Upload a picture for each resolved QA infraction','general','photo',210),

 -- Store appearance — exterior and entry
 ('00000000-0000-0000-0000-000000000001','qa_ext_signage','Does the exterior signage on the location meet the carrier signage family standards?','appearance','check',300),
 ('00000000-0000-0000-0000-000000000001','qa_ext_monument','Does the exterior monument or pylon signage meet carrier standards?','appearance','check',310),
 ('00000000-0000-0000-0000-000000000001','qa_ext_clean','Is the exterior of the store clean (including windows) and clutter free?','appearance','check',320),
 ('00000000-0000-0000-0000-000000000001','qa_ext_lightboxes','Are the light boxes hung straight and powered on, and the window clings hung straight?','appearance','check',330),
 ('00000000-0000-0000-0000-000000000001','qa_ext_hours','Are store hours posted on the approved carrier hours signage, and correct according to Google for this location?','appearance','check',340),

 -- Store appearance — interior and manager compliance
 ('00000000-0000-0000-0000-000000000001','qa_int_clutter','Is the store interior clean and free of clutter, with no additional fixtures or displays present (sales floor, trip hazards, stacks of accessories or boxes)?','facilities','check',400),
 ('00000000-0000-0000-0000-000000000001','qa_int_flooring','Is the approved flooring in proper condition?','facilities','check',410),
 ('00000000-0000-0000-0000-000000000001','qa_int_walls','Are the walls clean, the correct color, and free from scuff marks or damage?','facilities','check',420),
 ('00000000-0000-0000-0000-000000000001','qa_int_pos_stations','Are the POS stations and fixtures clean, free of handprints and scuffs, and in good condition with no repairs needed?','facilities','check',430),
 ('00000000-0000-0000-0000-000000000001','qa_int_lighting','Are more than 90% of all light fixtures working, including fixture and fluorescent lights?','facilities','check',440),

 -- Customer experience
 ('00000000-0000-0000-0000-000000000001','qa_cx_accessories','Is the store adhering to the carrier accessory requirements?','customer','check',500),
 ('00000000-0000-0000-0000-000000000001','qa_cx_approved_products','Is the location selling only approved carrier products and services?','customer','check',510),
 ('00000000-0000-0000-0000-000000000001','qa_cx_dress_code','Are all employees in proper dress code as outlined in the carrier brand standards (carrier shirt and/or name tag)?','customer','check',520),
 ('00000000-0000-0000-0000-000000000001','qa_cx_data_protection','Do store employees actively protect customer and carrier proprietary information (ID policies, shredding, securing documents and receipts, locking screens when not in use, verifying passwords, protecting SIM ICCIDs)?','customer','check',530),
 ('00000000-0000-0000-0000-000000000001','qa_cx_min_stock','Does the location have the minimum number of devices in stock per the branded store standards?','customer','check',540),

 -- Merchandise and brand
 ('00000000-0000-0000-0000-000000000001','qa_mb_pedestals','Are all existing phone pedestals per panel in this location in proper working condition?','merchandise','check',600),
 ('00000000-0000-0000-0000-000000000001','qa_mb_live_demo','Is the location adhering to the live demo requirement stated in the branded store standards?','merchandise','check',610),
 ('00000000-0000-0000-0000-000000000001','qa_mb_unapproved_signage','Is the location free from unapproved signs and banners inside and out (expired collateral, handwritten or computer-generated signs, vinyl banners, light boxes, LED / neon / flashing signs, easel boards)?','merchandise','check',620),
 ('00000000-0000-0000-0000-000000000001','qa_mb_return_policy','Is the carrier return policy posted and customer facing in the store?','merchandise','check',630),

 -- Employee experience
 ('00000000-0000-0000-0000-000000000001','qa_ee_training_portal','Can every employee present log into the carrier training portal and find the most commonly used links on carrier systems, and have they completed all required training modules?','employee','check',700),
 ('00000000-0000-0000-0000-000000000001','qa_ee_color_printer','Does the location have a working color printer?','employee','check',710),
 ('00000000-0000-0000-0000-000000000001','qa_ee_safe_storage','Does the location have a safe, or a location for safe storage of devices and deposits?','employee','check',720)
ON CONFLICT (org_id, item_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1000 complete — 24 carrier brand-review questions added to the DM visit checklist' AS status;
