-- 1030_portout_fraud_report.sql — the daily port-out fraud report: its per-org rules, its designated
-- recipients, and its daily send.
--
-- Run this in the Supabase SQL editor. Claude does not run SQL and has applied NOTHING of this file.
-- Additive, idempotent, safe to re-run. Numbered, higher number = later ALTER wins.
--
-- OWNER DIRECTIVE, verbatim, 2026-09-28: *"port in is activation and port port out within 30 days is
-- a gnale of fraud or before the second payment is a signal of fraud , either customer initiated or
-- sales rep initiated as the phones are cheaper on new aCTIVATION WITH port in ,so it is also
-- important to report how much acessories were sold with that activation , if it is below $50 then it
-- could be a sales rep driven and that shoudl be on top of a daily fraud report being sent to all
-- market managers and above via whats app and email - with a big red mark and open urgently"*.
--
-- See index §19.32. The report is `GET /commcalc/portout-fraud`; the logic is pure in
-- `commcalc/portout_fraud.py` and proven by `backend/harness_portout_fraud.py` (85 checks).
--
-- ══ WHAT THIS FILE DOES NOT DO ══════════════════════════════════════════════════════════════════
-- It creates NO table and NO ingest path. The report reads `commcalc.raw_sales` and `commcalc.raw_mi`
-- as they already land — no new external feed, so no `data_lineage_registry` row and no `925` seed
-- entry. It moves no money and writes nothing to any payout, ledger or P&L line.


-- ══ BLOCK 1 — THE RULES ARE CONFIG (RULE TWO). Behaviour-neutral by existing. ═══════════════════
-- One nullable JSONB column on the EXISTING per-org config row, beside `activation_details_rules`
-- (mig 313), which is where this module's other per-org classification rules already live. NULL
-- resolves to the house defaults in `portout_fraud.HOUSE_RULES`, which ARE the owner's numbers — so
-- applying Block 1 alone changes nothing at all.

BEGIN;

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS portout_fraud_rules JSONB;

COMMENT ON COLUMN commcalc.accessory_config.portout_fraud_rules IS
  'Per-org rules for the daily port-out fraud report (index §19.32), resolved by '
  'commcalc.portout_fraud.resolve_rules over the house defaults. Keys: '
  '"window_days" (int > 0, house 30) — a port-out within this many days of OUR sale is the signal; '
  '"accessory_floor" (number >= 0, house 50.0) — below this the report leans REP-DRIVEN. It is an '
  'ATTRIBUTION column on an already-flagged row, never a trigger: measured live 2026-09-28, under-$50 '
  'alone names 50.8% of all port-ins and would be noise; '
  '"second_payment_days" (int > 0, house NULL = NOT EVALUATED) — the owner''s second trigger, a '
  'port-out before the second payment. Deliberately unset: no field on the sale line or the subscriber '
  'row states when a line''s second payment fell due, and quietly aliasing it to the 30-day window '
  'would let the report claim to answer a question it has never asked. Every payload says so; '
  '"watch_classes" (array, house ["port"]) — which line_class activation classes are watched. '
  'A junk or out-of-range value falls back to the house default rather than emptying or flooding the '
  'report (missing beats wrong, §19.26). NULL = the house defaults = the owner''s numbers exactly.';

COMMIT;


-- ══ BLOCK 2 — THE DAILY SEND ═══════════════════════════════════════════════════════════════════
-- ⚠ THIS BLOCK SENDS MESSAGES TO PEOPLE. Applying it starts a daily WhatsApp + email to every market
-- manager and above. It is separated from Block 1 for exactly that reason and needs the owner's
-- explicit go-ahead, not merely a green CI run.
--
-- NOTHING NEW IS SCHEDULED. The report rides the EXISTING notify scheduler: `notify.subscriptions`
-- with `frequency='daily'`, drained by `POST /notify/run-due`, which pg_cron already calls. No second
-- cron job, no second scheduler, no second dispatch path.
--
-- RECIPIENTS ARE RESOLVED, NOT LISTED. The registry entry declares
-- `role_scopes: ['market','region','regional','all']` and `notify.router._role_scope_recipients`
-- resolves that against `roles.permissions.scope` at SEND TIME — the same vocabulary
-- `core.scope.roster_reach` already calls "market manager and above". So the report reaches whoever
-- holds the scope on the day it fires, and a manager hired next month is not a config edit somebody
-- has to remember. `recipient_ids` / `ad_hoc_*` below are therefore left EMPTY on purpose: any address
-- added there is sent to IN ADDITION to the resolved managers.
--
-- BEFORE APPLYING, CHECK WHO THIS ACTUALLY REACHES — a fraud report naming stores and reps must not
-- be a surprise to the people on it:
--
--   SELECT r.name, r.permissions->>'scope' AS scope, COUNT(u.email) AS people
--     FROM public.roles r LEFT JOIN public.app_users u
--       ON u.org_id = r.org_id AND u.role = r.name
--    WHERE r.org_id = '00000000-0000-0000-0000-000000000001'
--      AND lower(COALESCE(r.permissions->>'scope','')) IN ('market','region','regional','all')
--    GROUP BY 1, 2 ORDER BY 2, 1;
--
-- (a) THE SUBSCRIPTION. 07:00 in the tenant's own timezone, so it lands before the day's trading
--     rather than at UTC midnight. `filters` is left empty: the endpoint's own default window is the
--     trailing twelve months, which is what makes a line sold in March and ported out in September
--     visible at all — a 24-hour window would only ever see the day's own sales, none of which can
--     have ported out yet.
--
--   INSERT INTO notify.subscriptions
--     (org_id, name, report_key, filters, channels, formats, frequency, hour, timezone, is_active)
--   SELECT '00000000-0000-0000-0000-000000000001',
--          'Daily Port-Out Fraud Report', 'portout_fraud', '{}'::jsonb,
--          ARRAY['email','whatsapp'], ARRAY['xlsx','pdf'], 'daily', 7, 'America/New_York', true
--   WHERE NOT EXISTS (
--     SELECT 1 FROM notify.subscriptions
--      WHERE org_id = '00000000-0000-0000-0000-000000000001' AND report_key = 'portout_fraud');
--
-- (b) THE DESIGNATED-RECIPIENT ROW, so an on-demand "send this now" from the report page routes the
--     same way the schedule does (POST /notify/send-to-designated). Same resolution, so the two
--     cannot drift apart:
--
--   INSERT INTO notify.report_config (org_id, report_key, channels, recipient_ids, is_active)
--   VALUES ('00000000-0000-0000-0000-000000000001', 'portout_fraud',
--           ARRAY['email','whatsapp'], '{}', true)
--   ON CONFLICT (org_id, report_key) DO UPDATE
--     SET channels = EXCLUDED.channels, is_active = EXCLUDED.is_active;


-- ══ BLOCK 3 — OPTIONAL: narrow or widen the rules for one org ═══════════════════════════════════
-- Nothing here needs applying; the house defaults already are the owner's numbers. These are the
-- shapes, so a later change is a config row and never a code edit.
--
-- (a) A tenant billing on a 45-day cycle:
--   UPDATE commcalc.accessory_config
--      SET portout_fraud_rules = COALESCE(portout_fraud_rules,'{}'::jsonb) || '{"window_days":45}'::jsonb
--    WHERE org_id = '<org>';
--
-- (b) SWITCHING THE SECOND TRIGGER ON — only once somebody can say what "the second payment" means
--     for that tenant's plans. Until then the report reports its own silence, which is the honest
--     state (CLAUDE.md: absence of a business rule is itself reported):
--   UPDATE commcalc.accessory_config
--      SET portout_fraud_rules = COALESCE(portout_fraud_rules,'{}'::jsonb) || '{"second_payment_days":60}'::jsonb
--    WHERE org_id = '<org>';
--
-- (c) Watching NEW ACTIVATIONS as well as port-ins (the owner named port-in; the mechanism is not
--     limited to it):
--   UPDATE commcalc.accessory_config
--      SET portout_fraud_rules = COALESCE(portout_fraud_rules,'{}'::jsonb)
--          || '{"watch_classes":["port","activation"]}'::jsonb
--    WHERE org_id = '<org>';


-- ══ REVERT ══════════════════════════════════════════════════════════════════════════════════════
-- Block 1 (the column is read through a try/except, so dropping it degrades to house defaults rather
-- than erroring):
--   ALTER TABLE commcalc.accessory_config DROP COLUMN IF EXISTS portout_fraud_rules;
-- Block 2 (stops the daily send; the report stays reachable on its page):
--   UPDATE notify.subscriptions SET is_active = false
--    WHERE report_key = 'portout_fraud' AND org_id = '00000000-0000-0000-0000-000000000001';
--   UPDATE notify.report_config SET is_active = false
--    WHERE report_key = 'portout_fraud' AND org_id = '00000000-0000-0000-0000-000000000001';
