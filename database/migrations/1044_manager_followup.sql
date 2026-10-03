-- 1044_manager_followup.sql — FOLLOW UP WITH MANAGERS: per-tenant config for the daily follow-up.
--
-- OWNER ASK 2026-10-03, verbatim: "then alert the management via a whats app message for all
-- followup items with the managers - this will be a seprate module - Follow Up with Managers , all
-- pending jobs assigned to the managers will be followed up via this module".
--
-- WHAT "ALL PENDING JOBS" MEANS IS NOT DEFINED BY THIS MODULE. It is the EXISTING registry
-- commcalc/compliance_summary.CATEGORIES — the ten flag / exception / compliance queues the Flags &
-- Compliance dashboard already counts off — which commcalc/manager_followup.sources() dereferences.
-- A second list of what counts as a pending job is the duplicate defect the index rules forbid, so
-- there is not one. What the follow-up adds is the two things a COUNT cannot give: who owns an item,
-- and how long it has been pending.
--
-- WHY IT IS A ROLL-UP AND NOT A TO-DO LIST — measured read-only on the house org, 2026-10-03:
--     open items that can be attributed and aged     76,094
--     … of which more than 90 days old               20,304
--     the oldest                                     324 days
--     open items carrying NO store, so NO owner      14,372
-- A module that WhatsApps a district manager 76,094 rows is not a follow-up. So a follow-up is per
-- (manager x queue): how many are open, how old the oldest is, which age band it sits in, and the
-- few oldest by name — with the full list on the queue's own page, which already exists and is
-- linked. The ESCALATION is what makes it accountability rather than a newsletter: work past
-- `manager_followup_escalate_after_days` appears in the digest of the manager ABOVE the owner too.
--
-- AND THE FINDING IT REFUSES TO HIDE: 14,372 open items carry no store at all and therefore cannot
-- be assigned to any manager. They are counted as unattributed and reported in every digest and
-- payload rather than silently dropped. Quietly excluding a fifth of the backlog because it has no
-- owner would make this module lie by omission; an unowned backlog is precisely what a follow-up
-- module exists to surface. Seven of the ten registry queues are likewise reported in
-- `not_attributed` WITH THE REASON, because they are counted by the dashboard through handlers that
-- return a period's rows rather than an ageable per-item queue — so nobody can read this board as
-- "that is everything".
--
-- NOTHING HARDCODED. Every knob is a column here with a house default in
-- manager_followup.HOUSE_CONFIG, and the recipients come from the org tree, never a typed list:
--   • the SWITCH             manager_followup_enabled              default FALSE (nothing on deploy)
--   • the send TIME          manager_followup_time                 default '10:30' (tenant-local)
--   • the CHANNELS           manager_followup_channels             default {whatsapp,email}
--   • the ESCALATION age     manager_followup_escalate_after_days  default 30
--   • how many to NAME       manager_followup_show_oldest          default 5
--   • the floor              manager_followup_min_items            default 1
--
-- NO NEW TABLE, NO NEW DEDUP RULE, NO NEW SCHEDULER, NO NEW FAN-OUT. Dedup rows go in the EXISTING
-- storeops.alert_log under scope 'manager_followup' through the same _lateness_already_sent /
-- _lateness_record_sent helpers mig 433, mig 905 and the zero-sales alert use; recipients,
-- one-digest-per-manager, the unreachable-recipient skip, the channel vocabulary, the ref_key
-- spelling and the due-time rule are commcalc/manager_digest, the ONE fan-out. The hourly pg_cron
-- tick + a tenant-local HH:MM is the mig-433 convention.
--
-- THE DEDUP KEY IS (store, queue, age BAND), not the count. A follow-up re-escalates when work moves
-- into an OLDER band — which is the point of a follow-up — but not every day for the same work in
-- the same band, because a count that ticks by one is not news.
--
-- Additive, idempotent, no backfill, no data movement, no money moved.
-- REVERT: ALTER TABLE storeops.tenants
--           DROP COLUMN IF EXISTS manager_followup_enabled,
--           DROP COLUMN IF EXISTS manager_followup_time,
--           DROP COLUMN IF EXISTS manager_followup_channels,
--           DROP COLUMN IF EXISTS manager_followup_escalate_after_days,
--           DROP COLUMN IF EXISTS manager_followup_show_oldest,
--           DROP COLUMN IF EXISTS manager_followup_min_items,
--           DROP COLUMN IF EXISTS manager_followup_last_run,
--           DROP COLUMN IF EXISTS manager_followup_last_detail;
--         (Dropping them restores the house defaults, which are OFF — nothing would send.)

ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS manager_followup_enabled             boolean     NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS manager_followup_time                text        NOT NULL DEFAULT '10:30',
  ADD COLUMN IF NOT EXISTS manager_followup_channels            jsonb       NOT NULL DEFAULT '["whatsapp","email"]'::jsonb,
  ADD COLUMN IF NOT EXISTS manager_followup_escalate_after_days integer     NOT NULL DEFAULT 30,
  ADD COLUMN IF NOT EXISTS manager_followup_show_oldest         integer     NOT NULL DEFAULT 5,
  ADD COLUMN IF NOT EXISTS manager_followup_min_items           integer     NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS manager_followup_last_run            timestamptz,
  ADD COLUMN IF NOT EXISTS manager_followup_last_detail          text;

COMMENT ON COLUMN storeops.tenants.manager_followup_escalate_after_days IS
  'Days. Pending work older than this appears in the digest of the manager ABOVE the owner as well, '
  'which is what makes the module accountability rather than a newsletter. Work whose age cannot be '
  'read NEVER escalates (it is reported as unknown instead) — escalating on an age we could not read '
  'would send a manager after work that might be a day old.';

COMMENT ON COLUMN storeops.tenants.manager_followup_channels IS
  'Channels from the vocabulary in commcalc/manager_digest (whatsapp / email). A recipient is reached '
  'on each channel they have an ADDRESS for and is skipped only when none can reach them. WhatsApp '
  'goes through whatsapp_meta.send_document_detailed so a business-initiated send takes the approved '
  'template rung rather than a free-form text Meta accepts with a 200 and then silently drops.';

NOTIFY pgrst, 'reload schema';

-- ── pg_cron registration (run AFTER deploy, once, in the Supabase SQL editor with the real secret) ──
-- HOURLY, like mig 433 / 905 / the zero-sales alert: the per-tenant send time is compared inside the
-- handler, so ONE job serves every tenant in every timezone and alert_log dedup makes a follow-up
-- escalate once per day per band.
--
--   SELECT cron.schedule('manager-followup-run-due', '10 * * * *', $$
--     SELECT net.http_post(
--       url     := 'https://metricspro-production.up.railway.app/api/v1/commcalc/manager-followup/alerts/run-due',
--       headers := jsonb_build_object('Content-Type','application/json','X-Notify-Secret','<NOTIFY_RUN_SECRET>'),
--       body    := '{}'::jsonb); $$);

SELECT 'Migration 1044 complete — storeops.tenants Follow Up With Managers config (enabled=false, '
       '10:30 tenant-local, whatsapp+email, escalate after 30 days). Register the pg_cron job with '
       'the real NOTIFY_RUN_SECRET, then enable per tenant.' AS status;
