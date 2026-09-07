-- 987_marketing_event_seed.sql — marketing module: HOUSE vocabulary, module registration,
--                                control-box coverage, GPS retention job
--
-- Companion to migration 986 (the schema). Split so the schema can be reviewed without the
-- vocabulary and the vocabulary can be re-seeded without touching the schema.
--
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- WHAT IS SEEDED, AND WHY IT IS SAFE
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- RULE TWO in practice: the owner named "back to school", "byod plan", "DJ", "food truck", "table
-- event". Those are seeded as ROWS on the HOUSE org — starting vocabulary a tenant can rename,
-- deactivate, reorder or extend from the settings screen with the "+" the owner asked for. They are
-- NOT a code enum, there is no CHECK constraint behind them, and NOTHING in the backend branches on
-- any key here. A tenant that deletes every one of these rows gets an empty picker with a "+", not
-- a broken module.
--
-- MONEY: nothing money-valued is seeded. The two informational dollar columns in mig 986
-- (marketing_event_vendor.cost, marketing_event_giveaway.unit_cost) get NO seed rows and no
-- defaults — a house-seeded price would be a number nobody agreed to. There is no commented-out
-- money seed below because there is no money seed to approve; if a later phase wants house price
-- defaults, they arrive as their own migration with owner sign-off.
--
-- Idempotent: every INSERT is ON CONFLICT DO NOTHING against the mig-986 unique keys, so re-running
-- this NEVER clobbers a tenant's edited label, sort order or is_active flag.

BEGIN;

-- House org (CLAUDE.md). Tenant rows override these per (list_key, key); see marketing_option's
-- table comment for the resolution order.
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. THE HOUSE OPTION VOCABULARY
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- ── THEME — "Theme of the event - back to school etc or byod plan" (owner, verbatim) ───────────
INSERT INTO core.marketing_option (org_id, list_key, key, label, sort_order, extra) VALUES
  ('00000000-0000-0000-0000-000000000001','theme','back_to_school','Back to School',10,'{}'),
  ('00000000-0000-0000-0000-000000000001','theme','byod_plan','BYOD / Bring Your Own Device',20,'{}'),
  ('00000000-0000-0000-0000-000000000001','theme','grand_opening','Grand Opening',30,'{}'),
  ('00000000-0000-0000-0000-000000000001','theme','holiday','Holiday',40,'{}'),
  ('00000000-0000-0000-0000-000000000001','theme','community_day','Community Day',50,'{}'),
  ('00000000-0000-0000-0000-000000000001','theme','sports_season','Sports Season',60,'{}'),
  ('00000000-0000-0000-0000-000000000001','theme','tax_season','Tax Season',70,'{}'),
  ('00000000-0000-0000-0000-000000000001','theme','general','General Awareness',900,'{}')
ON CONFLICT (org_id, list_key, key) DO NOTHING;

-- ── VENUE TYPE — "Location / Venue" (owner) ────────────────────────────────────────────────────
INSERT INTO core.marketing_option (org_id, list_key, key, label, sort_order, extra) VALUES
  ('00000000-0000-0000-0000-000000000001','venue_type','table_event','Table Event',10,'{}'),
  ('00000000-0000-0000-0000-000000000001','venue_type','mall_kiosk','Mall / Kiosk',20,'{}'),
  ('00000000-0000-0000-0000-000000000001','venue_type','school','School / Campus',30,'{}'),
  ('00000000-0000-0000-0000-000000000001','venue_type','community_center','Community Center',40,'{}'),
  ('00000000-0000-0000-0000-000000000001','venue_type','church','Place of Worship',50,'{}'),
  ('00000000-0000-0000-0000-000000000001','venue_type','street_fair','Street Fair / Festival',60,'{}'),
  ('00000000-0000-0000-0000-000000000001','venue_type','sports_venue','Sports Venue',70,'{}'),
  ('00000000-0000-0000-0000-000000000001','venue_type','partner_business','Partner Business',80,'{}'),
  ('00000000-0000-0000-0000-000000000001','venue_type','parking_lot','Parking Lot',90,'{}')
ON CONFLICT (org_id, list_key, key) DO NOTHING;

-- ── OUTSIDE PARTY — "Who is the outside party if there is one e.g DJ/ food truck / table event" ─
INSERT INTO core.marketing_option (org_id, list_key, key, label, sort_order, extra) VALUES
  ('00000000-0000-0000-0000-000000000001','party_type','dj','DJ',10,'{}'),
  ('00000000-0000-0000-0000-000000000001','party_type','food_truck','Food Truck',20,'{}'),
  ('00000000-0000-0000-0000-000000000001','party_type','table_host','Table Host / Venue Contact',30,'{}'),
  ('00000000-0000-0000-0000-000000000001','party_type','photographer','Photographer / Videographer',40,'{}'),
  ('00000000-0000-0000-0000-000000000001','party_type','entertainer','Entertainer / Mascot',50,'{}'),
  ('00000000-0000-0000-0000-000000000001','party_type','security','Security',60,'{}'),
  ('00000000-0000-0000-0000-000000000001','party_type','equipment_rental','Equipment Rental',70,'{}'),
  ('00000000-0000-0000-0000-000000000001','party_type','permit_office','Permit / Licensing Office',80,'{}')
ON CONFLICT (org_id, list_key, key) DO NOTHING;

-- ── TRANSPORT — "How are employees getting there" (owner) ───────────────────────────────────────
-- `needs_pickup` is DATA the UI uses to decide whether to prompt for a driver. It is not a branch
-- on a mode NAME: the UI reads the flag, so a tenant-added mode ("company shuttle") behaves
-- correctly the moment it is added with the flag set.
INSERT INTO core.marketing_option (org_id, list_key, key, label, sort_order, extra) VALUES
  ('00000000-0000-0000-0000-000000000001','transport_mode','own_car','Own Car',10,'{"needs_pickup":false}'),
  ('00000000-0000-0000-0000-000000000001','transport_mode','carpool','Carpool (being picked up)',20,'{"needs_pickup":true}'),
  ('00000000-0000-0000-0000-000000000001','transport_mode','company_vehicle','Company Vehicle',30,'{"needs_pickup":false}'),
  ('00000000-0000-0000-0000-000000000001','transport_mode','rideshare','Rideshare',40,'{"needs_pickup":false}'),
  ('00000000-0000-0000-0000-000000000001','transport_mode','public_transit','Public Transit',50,'{"needs_pickup":false}'),
  ('00000000-0000-0000-0000-000000000001','transport_mode','walking','Walking',60,'{"needs_pickup":false}')
ON CONFLICT (org_id, list_key, key) DO NOTHING;

-- ── GIVEAWAYS — "Giveaways" (owner) ────────────────────────────────────────────────────────────
-- Labels only. No cost, no price, no quantity: those are per-event facts a human enters.
INSERT INTO core.marketing_option (org_id, list_key, key, label, sort_order, extra) VALUES
  ('00000000-0000-0000-0000-000000000001','giveaway_type','branded_merch','Branded Merchandise',10,'{}'),
  ('00000000-0000-0000-0000-000000000001','giveaway_type','accessory','Accessory',20,'{}'),
  ('00000000-0000-0000-0000-000000000001','giveaway_type','gift_card','Gift Card',30,'{}'),
  ('00000000-0000-0000-0000-000000000001','giveaway_type','raffle_prize','Raffle Prize',40,'{}'),
  ('00000000-0000-0000-0000-000000000001','giveaway_type','food_drink','Food / Drink',50,'{}'),
  ('00000000-0000-0000-0000-000000000001','giveaway_type','print_collateral','Print Collateral',60,'{}')
ON CONFLICT (org_id, list_key, key) DO NOTHING;

-- ── ROLE AT THE EVENT ("Employees planned for the event", by what they are doing there) ─────────
INSERT INTO core.marketing_option (org_id, list_key, key, label, sort_order, extra) VALUES
  ('00000000-0000-0000-0000-000000000001','event_role','lead','Event Lead',10,'{}'),
  ('00000000-0000-0000-0000-000000000001','event_role','sales','Sales / Activations',20,'{}'),
  ('00000000-0000-0000-0000-000000000001','event_role','greeter','Greeter / Crowd',30,'{}'),
  ('00000000-0000-0000-0000-000000000001','event_role','setup','Setup / Teardown',40,'{}'),
  ('00000000-0000-0000-0000-000000000001','event_role','tech','Tech / Device Support',50,'{}'),
  ('00000000-0000-0000-0000-000000000001','event_role','driver','Driver',60,'{}')
ON CONFLICT (org_id, list_key, key) DO NOTHING;

-- ── CREATIVE CHANNELS — "Social media and other marketing planned links for the creatives" ──────
-- These are CHANNELS, not integrations: a row here buys a labelled slot to paste a planned link
-- into. Nothing authenticates to, posts to, or reads from any of these platforms in this phase.
INSERT INTO core.marketing_option (org_id, list_key, key, label, sort_order, extra) VALUES
  ('00000000-0000-0000-0000-000000000001','link_channel','instagram','Instagram',10,'{}'),
  ('00000000-0000-0000-0000-000000000001','link_channel','facebook','Facebook',20,'{}'),
  ('00000000-0000-0000-0000-000000000001','link_channel','tiktok','TikTok',30,'{}'),
  ('00000000-0000-0000-0000-000000000001','link_channel','flyer','Flyer / Print',40,'{}'),
  ('00000000-0000-0000-0000-000000000001','link_channel','email','Email Campaign',50,'{}'),
  ('00000000-0000-0000-0000-000000000001','link_channel','sms','SMS Campaign',60,'{}'),
  ('00000000-0000-0000-0000-000000000001','link_channel','local_press','Local Press / Radio',70,'{}'),
  ('00000000-0000-0000-0000-000000000001','link_channel','signage','On-site Signage',80,'{}')
ON CONFLICT (org_id, list_key, key) DO NOTHING;

-- ── GOAL METRICS — "Goal for the event - how many activations or accessories" (owner) ───────────
-- `derivable` + `field` say whether the platform can compute an ACTUAL for this metric from the ONE
-- shared sales pass (commcalc router._sales_cell_agg, reached via _compute_feed_actuals_py). The
-- field names below are that pass's own output keys — this is a POINTER at the shared derivation,
-- never a copy of it.
--
-- derivable=false is deliberate and honest: nothing in the platform counts foot traffic or collected
-- leads at an outside event, so those goals are tracked as targets a human reports against and are
-- rendered as "no automatic actual" rather than a zero that looks like failure.
INSERT INTO core.marketing_option (org_id, list_key, key, label, sort_order, extra) VALUES
  ('00000000-0000-0000-0000-000000000001','goal_metric','activations','Activations',10,
   '{"unit":"count","derivable":true,"field":"activations"}'),
  ('00000000-0000-0000-0000-000000000001','goal_metric','accessory_dollars','Accessory $',20,
   '{"unit":"money","derivable":true,"field":"accessory_dollars"}'),
  ('00000000-0000-0000-0000-000000000001','goal_metric','boxes','Total Boxes',30,
   '{"unit":"count","derivable":true,"field":"boxes"}'),
  ('00000000-0000-0000-0000-000000000001','goal_metric','upgrades','Upgrades',40,
   '{"unit":"count","derivable":true,"field":"upgrades"}'),
  ('00000000-0000-0000-0000-000000000001','goal_metric','byod','BYOD',50,
   '{"unit":"count","derivable":true,"field":"byod"}'),
  ('00000000-0000-0000-0000-000000000001','goal_metric','leads_collected','Leads Collected',60,
   '{"unit":"count","derivable":false,"note":"No automatic source — reported by the event lead."}'),
  ('00000000-0000-0000-0000-000000000001','goal_metric','foot_traffic','Foot Traffic / Conversations',70,
   '{"unit":"count","derivable":false,"note":"No automatic source — reported by the event lead."}')
ON CONFLICT (org_id, list_key, key) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. A HOUSE CHECKLIST TEMPLATE — a worked example of "a user created checklist"
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- theme_key NULL = offered for every theme. This is a STARTING POINT a tenant edits or deletes; the
-- owner's requirement is that the checklist is user-created, and it still is — this template just
-- means the first event of the first tenant isn't a blank page. No quantities that imply cost.
DO $$
DECLARE tpl UUID;
BEGIN
  SELECT id INTO tpl FROM core.marketing_checklist_template
   WHERE org_id = '00000000-0000-0000-0000-000000000001' AND name = 'Standard Table Event';
  IF tpl IS NULL THEN
    INSERT INTO core.marketing_checklist_template (org_id, name, theme_key, created_by)
    VALUES ('00000000-0000-0000-0000-000000000001', 'Standard Table Event', NULL, 'migration 987')
    RETURNING id INTO tpl;

    INSERT INTO core.marketing_checklist_template_item
      (org_id, template_id, label, category, qty, is_returnable, sort_order) VALUES
      ('00000000-0000-0000-0000-000000000001', tpl, 'Folding table',            'Setup',      1, TRUE,  10),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Table cloth / branding',   'Setup',      1, TRUE,  20),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Canopy / tent',            'Setup',      1, TRUE,  30),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Chairs',                   'Setup',      2, TRUE,  40),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Banner / signage',         'Signage',    1, TRUE,  50),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Flyers / brochures',       'Collateral', NULL, FALSE, 60),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Demo devices',             'Devices',    NULL, TRUE,  70),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Tablet / laptop for activations', 'Devices', 1, TRUE, 80),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Power bank / extension cord',     'Power',   1, TRUE, 90),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Mobile hotspot',           'Power',      1, TRUE, 100),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Card reader',              'Payments',   1, TRUE, 110),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Giveaway stock',           'Giveaways',  NULL, FALSE, 120),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Sign-up sheets / QR stand','Collateral', 1, TRUE, 130),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Trash bags / cleanup kit', 'Teardown',   1, FALSE, 140),
      ('00000000-0000-0000-0000-000000000001', tpl, 'Permit / venue paperwork', 'Compliance', 1, TRUE, 150);
  END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. HOUSE CONFIG ROW — approval OFF, as directed
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Written explicitly rather than relying on the column defaults so the house posture is visible in
-- the data, not only in the DDL. Every other tenant with no row falls back to the same defaults.
INSERT INTO core.marketing_config (org_id, approval_required, approval_spend_threshold,
                                   default_checkin_radius_m, max_checkin_accuracy_m,
                                   block_checkin_outside_fence, checkin_geo_retention_days,
                                   staffing_alert_lead_hours, updated_by)
VALUES ('00000000-0000-0000-0000-000000000001', FALSE, NULL, 150, 200, FALSE, 180, 48, 'migration 987')
ON CONFLICT (org_id) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 4. MODULE REGISTRATION — this is the load-bearing part
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The canonical registry (mig 700) mirrors MODULE_CATALOG in app/modules/core/entitlements.py.
-- Registering here is what makes 'marketing' appear in the tenant-entitlement picker AND the mig-975
-- billing pricing grid. A module that ships without this row bills nothing, forever, silently — the
-- exact class of bug the hardcoded /health module list was. The in-code dict is the fallback, so the
-- application behaves identically whether or not this migration has run.
INSERT INTO core.module_catalog (key, label, sort_order) VALUES
  ('marketing', 'Marketing & Events', 130)
ON CONFLICT (key) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 5. CONTROL BOX — lamps that exist, and an HONEST declaration of what is NOT watched
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The board derives one lamp per LIVE attention provider (mig 970), so the three providers this
-- module registers in app/modules/marketing/attention_providers.py — unconfirmed staff, missing
-- backup, incomplete checklist on an imminent event — light up with NO row here and no code change.
--
-- These rows are the opposite: the parts of this module NOTHING observes. A board showing green for
-- something it never checked is worse than one that says "not monitored", so each gap is declared
-- and counted against the board's coverage fraction. Turning one on later is a config change (swap
-- `kind`), not a deploy.
INSERT INTO core.system_check (org_id, key, subsystem, label, kind, config, deep_link,
                               deep_link_label, index_ref, sort_order)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'marketing_event_actuals_source', 'marketing',
   'Event actuals depend on the sales feed being current', 'unmonitored',
   '{"note":"Event planned-vs-actual reads commcalc''s shared sales pass. If the daily sales feed is stale, an event reports LOW numbers rather than an error. Feed freshness IS monitored by the imports lamp; that this module silently depends on it is not. The event report labels its own source date so a human can see it."}'::jsonb,
   '/marketing', 'Open Marketing', '§23 Marketing & Events', 940),
  ('00000000-0000-0000-0000-000000000001', 'marketing_checkin_gps_retention', 'marketing',
   'Employee event GPS purged on schedule', 'unmonitored',
   '{"note":"Every check-in row is stamped with purge_after_date from marketing_config.checkin_geo_retention_days, and GET /marketing/checkin-retention reports what is due. NOTHING deletes it automatically in this phase — no purge job runs, so the retention promise is currently manual. Declared rather than assumed."}'::jsonb,
   '/marketing/settings', 'Marketing settings', '§23 Marketing & Events', 941),
  ('00000000-0000-0000-0000-000000000001', 'marketing_creative_assets', 'marketing',
   'Creative assets / marketing-portal pull', 'unmonitored',
   '{"note":"Phase 1 models planned creative LINKS only. No asset gallery, no bring-your-own cloud storage and no marketing-portal asset pull exists yet, so there is nothing to monitor. The seam is core.marketing_event_link.asset_ref / asset_source."}'::jsonb,
   '/marketing', 'Open Marketing', '§23 Marketing & Events', 942)
ON CONFLICT (org_id, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 987 complete — house marketing vocabulary (8 option lists), standard checklist template, house config (approval OFF), module_catalog row ''marketing'', 3 honest control-box unmonitored declarations' AS status;

COMMIT;

-- REVERT:
--   DELETE FROM core.system_check WHERE key IN ('marketing_event_actuals_source',
--                                               'marketing_checkin_gps_retention',
--                                               'marketing_creative_assets');
--   DELETE FROM core.module_catalog WHERE key = 'marketing';
--   DELETE FROM core.marketing_config WHERE org_id = '00000000-0000-0000-0000-000000000001';
--   DELETE FROM core.marketing_checklist_template_item WHERE template_id IN (
--     SELECT id FROM core.marketing_checklist_template
--      WHERE org_id = '00000000-0000-0000-0000-000000000001' AND name = 'Standard Table Event');
--   DELETE FROM core.marketing_checklist_template
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND name = 'Standard Table Event';
--   DELETE FROM core.marketing_option WHERE org_id = '00000000-0000-0000-0000-000000000001';
--   -- (A tenant's OWN option rows are deliberately NOT touched by this revert.)
