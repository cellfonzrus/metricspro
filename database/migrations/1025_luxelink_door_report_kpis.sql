-- 1025_luxelink_door_report_kpis.sql — define the door-report KPIs for the LuxeLink / Total tenant.
--
-- OWNER (2026-09-25), given the door report's own column list: "all of them, twp all%, address check is
-- not known at this point, add it will send the feed later."
--
-- WHY A SEED AND NOT CODE: which KPIs a tenant has is DATA (mig 060's whole point, and CLAUDE.md RULE
-- TWO — "a per-tenant config row is not a patch; per-tenant CODE is"). These rows are exactly what an
-- admin would type on /admin/kpi-metrics; they are seeded here so the set is auditable in the repo and
-- idempotent, and every one of them stays editable on that page afterwards.
--
-- LABELS are the report's own column names, minus the "Current " prefix, because a label's job is to let
-- the owner recognise the number on the sheet in front of him. Relabelling any of them is one click on
-- /admin/kpi-metrics and touches no code.
--
-- TARGETS ARE DELIBERATELY NULL — not 0. `kpi_failing.evaluate` skips a metric with NO target ("a metric
-- with no target cannot fail anyone"), but a target of **0** is a real target that everything meets: it
-- would paint every store green on a KPI nobody has set a bar for. NULL says "not set yet" and shows as
-- such. The owner sets each bar on /admin/kpi-metrics, or a per-period `payout_config` column wins over
-- it where one exists.
--
-- `address_checks` is seeded WITH NO FEED, on the owner's instruction that the feed comes later. Until
-- it arrives the metric reports `no_data` everywhere — never 0, never "failing". That is the honest
-- state, and it is why the row is worth creating now: a defined-but-unfed KPI is visible and asks to be
-- fed; an undefined one is invisible.
--
-- MONEY NOTE (surface before applying): `twp` is a Management-Incentive QUALIFIER. This migration only
-- DEFINES it — mig 060's table holds definitions, not values — and `commcalc.kpi_actual` currently holds
-- 0 rows platform-wide, so nothing that has already been paid moves. The values arrive from
-- POST /kpi-import/paramount, whose column choice is exact-match and locked (§19.26).
--
-- Additive + idempotent (ON CONFLICT DO NOTHING on the mig-060 unique key, so re-running changes nothing
-- and an existing row — LuxeLink's `zulu` — keeps its label, target and sort).
--
-- REVERT: DELETE FROM commcalc.carrier_kpi_metric
--           WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
--             AND carrier_id = '00000000-0000-0000-0000-000000000000'
--             AND metric_key IN ('acts','pacing_acts','quota','pacing_quota','twp','twp_plus','tablets',
--                                'fwa_acts','upgrades','edge_apply','edge_approve','edge_acts',
--                                'autopay_ta','autopay_all','tmr3','address_checks');
--         (deliberately NOT listing 'zulu' — that row predates this migration.)

INSERT INTO commcalc.carrier_kpi_metric
  (org_id, carrier_id, metric_key, label, target_default, payout_config_col, sort)
VALUES
  -- Section A — the door's sales performance, in the report's own order
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'acts',           'Acts',            NULL, NULL,  1),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'pacing_acts',    'Pacing Acts',     NULL, NULL,  2),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'quota',          'Quota',           NULL, NULL,  3),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'pacing_quota',   'Pacing % to Quota', NULL, NULL, 4),
  -- the two that must never collapse into one another (§19.26): TWP ALL is the qualifier, TWP+ is its own metric
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'twp',            'TWP ALL %',       NULL, NULL,  5),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'twp_plus',       'TWP+ %',          NULL, NULL,  6),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'tablets',        'Tablets',         NULL, NULL,  7),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'fwa_acts',       'FWA Acts',        NULL, NULL,  8),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'upgrades',       'Upgrades',        NULL, NULL,  9),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'edge_apply',     'Edge Apply',      NULL, NULL, 10),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'edge_approve',   'Edge Approve',    NULL, NULL, 11),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'edge_acts',      'Edge Acts',       NULL, NULL, 12),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'autopay_ta',     'Autopay TA %',    NULL, NULL, 13),
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'autopay_all',    'Autopay All %',   NULL, NULL, 14),
  -- Section B — quality. 'Finalized 3MR%' wins over 'Pacing 3MR%' in the parser.
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'tmr3',           '3-Month Retention', NULL, 'kpi_tmr3_target', 15),
  -- 'zulu' (sort 0) already exists for this tenant and is left exactly as it is.
  -- Defined now, fed later — reports no_data until the feed arrives, never 0 (owner 2026-09-25).
  ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '00000000-0000-0000-0000-000000000000', 'address_checks', 'Address Checks',  NULL, NULL, 17)
ON CONFLICT (org_id, carrier_id, metric_key) DO NOTHING;
