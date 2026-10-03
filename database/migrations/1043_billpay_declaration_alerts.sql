-- 1043_billpay_declaration_alerts.sql — the MORNING bill-pay declaration-exception digest.
--
-- OWNER ASK 2026-10-03, verbatim: "the system should create that report and send it to the dm and
-- all above via whats app and email the next morning at 1030 am - nothing hardcoded".
--
-- NOTHING HARDCODED, LITERALLY. Every knob the ask names is a per-tenant column here with a house
-- default, and the recipients are resolved from the org tree (storeops.org_units / org_managers /
-- employees) rather than typed into a list:
--   • the send TIME          billpay_declaration_alert_time       default '10:30'  (tenant-local HH:MM)
--   • the CHANNELS           billpay_declaration_alert_channels   default {email,whatsapp}
--   • the dollar TOLERANCE   billpay_declaration_alert_tolerance  default 1.00
--   • how far BACK to look   billpay_declaration_alert_lookback_days default 1 ("the next morning")
--   • the on/off SWITCH      billpay_declaration_alerts_enabled   default FALSE
--
-- SAFE BY DEFAULT. `enabled` is FALSE, so nothing sends on deploy — the mig-905 posture. The owner
-- switches it on per tenant. The code resolves these exact values before this migration is applied
-- (`billpay_declaration_alerts.resolve_config` over HOUSE_CONFIG), so the sweep is inert and
-- harmless either way, and applying the migration changes no behaviour until the switch is flipped.
--
-- 10:30 IS NOT A NUMBER CHOSEN HERE. It is migration 433's `lateness_alert_time` convention —
-- tenant-local HH:MM compared by an HOURLY pg_cron tick, deduped per day via storeops.alert_log —
-- which already defaults to 10:30 because that is the hour the owner asked for then, too. The
-- comparison itself now lives in ONE place, `commcalc/manager_digest.due_now`, instead of being
-- re-spelled by each sweep.
--
-- NO NEW TABLE, NO NEW DEDUP RULE, NO NEW SCHEDULER, NO NEW FAN-OUT. Dedup rows go in the EXISTING
-- storeops.alert_log under scope 'billpay_declaration' through the same _lateness_already_sent /
-- _lateness_record_sent helpers mig 433 and mig 905 use; the recipient rule, one-digest-per-manager,
-- the unreachable-recipient skip and the ref_key spelling are commcalc/manager_digest, the one
-- fan-out the ePay and zero-sales alerts already ride.
--
-- WHAT IT ALERTS ON, AND THE ONE THING IT REFUSES TO. Only the two classes that are a person's
-- problem: 'under_declared' (bill-pay cash the store took and did not declare) and 'over_declared'
-- (sales cash labelled as bill-pay cash). A store-day the sales feed carried NO bill payments for is
-- 'no_pos_figure' and is NEVER alerted — that is a data-feed gap with its own surface (§20 import
-- health), and blaming a store for a report that did not arrive is the silent-zero defect class the
-- house rules forbid. It is COUNTED in the digest footer so a thin digest is never read as a healthy
-- estate (the §15z footer rule).
--
-- WHY IT IS NOT NOISE. Before §47.12 fixed the POS basis to include the customer service fee, this
-- digest would have alerted on 526 of 535 September store-days. After the fix, yesterday's real run
-- (read-only dry run, 2026-10-02) flagged 5 store-days worth $278.53 out of 18, with 13 agreeing.
--
-- Additive, idempotent, no backfill, no data movement, no money moved.
-- REVERT: ALTER TABLE storeops.tenants
--           DROP COLUMN IF EXISTS billpay_declaration_alerts_enabled,
--           DROP COLUMN IF EXISTS billpay_declaration_alert_time,
--           DROP COLUMN IF EXISTS billpay_declaration_alert_tolerance,
--           DROP COLUMN IF EXISTS billpay_declaration_alert_channels,
--           DROP COLUMN IF EXISTS billpay_declaration_alert_lookback_days,
--           DROP COLUMN IF EXISTS billpay_declaration_alert_last_run,
--           DROP COLUMN IF EXISTS billpay_declaration_alert_last_detail;
--         (Dropping them restores the house defaults, which are OFF — nothing would send.)

ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS billpay_declaration_alerts_enabled      boolean     NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS billpay_declaration_alert_time          text        NOT NULL DEFAULT '10:30',
  ADD COLUMN IF NOT EXISTS billpay_declaration_alert_tolerance     numeric     NOT NULL DEFAULT 1.00,
  ADD COLUMN IF NOT EXISTS billpay_declaration_alert_channels      jsonb       NOT NULL DEFAULT '["email","whatsapp"]'::jsonb,
  ADD COLUMN IF NOT EXISTS billpay_declaration_alert_lookback_days integer     NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS billpay_declaration_alert_last_run      timestamptz,
  ADD COLUMN IF NOT EXISTS billpay_declaration_alert_last_detail   text;

COMMENT ON COLUMN storeops.tenants.billpay_declaration_alert_time IS
  'Tenant-local HH:MM at which the bill-pay declaration-exception digest is sent (mig-433 '
  'convention; an HOURLY pg_cron tick asks manager_digest.due_now whether this minute has arrived '
  'in the tenant own day, and storeops.alert_log dedup stops every later tick that day). An '
  'unparseable value resolves to the house default rather than never alerting.';

COMMENT ON COLUMN storeops.tenants.billpay_declaration_alert_channels IS
  'Which channels the digest is sent on, from the vocabulary in commcalc/manager_digest '
  '(email / whatsapp). A recipient is reached on each channel they have an ADDRESS for and is '
  'skipped only when none can reach them; WhatsApp goes through whatsapp_meta.send_document_detailed '
  'so a business-initiated 10:30 send takes the approved-template rung instead of a free-form text '
  'that Meta accepts with a 200 and then silently drops (the 2026-08-05 incident).';

COMMENT ON COLUMN storeops.tenants.billpay_declaration_alert_tolerance IS
  'Dollars. A declared-vs-POS difference at or below this is rounding, not an exception. The POS '
  'side is metric_recon.pos_billpay_cash, which INCLUDES the customer service fee (index 47.12) — '
  'without that correction this alert would fire on nine store-days in ten.';

NOTIFY pgrst, 'reload schema';

-- ── pg_cron registration (run AFTER deploy, once, in the Supabase SQL editor with the real secret) ──
-- HOURLY, exactly like mig 433 and mig 905: the per-tenant send time is compared inside the handler,
-- so one job serves every tenant in every timezone and the alert_log dedupe makes a finding escalate
-- exactly once per day.
--
--   SELECT cron.schedule('billpay-declaration-run-due', '5 * * * *', $$
--     SELECT net.http_post(
--       url     := 'https://metricspro-production.up.railway.app/api/v1/closing/billpay-declaration-alerts/run-due',
--       headers := jsonb_build_object('Content-Type','application/json','X-Notify-Secret','<NOTIFY_RUN_SECRET>'),
--       body    := '{}'::jsonb); $$);

SELECT 'Migration 1043 complete — storeops.tenants bill-pay declaration-alert config '
       '(enabled=false, 10:30 tenant-local, email+whatsapp, $1.00 tolerance). Register the pg_cron '
       'job with the real NOTIFY_RUN_SECRET, then enable per tenant.' AS status;
