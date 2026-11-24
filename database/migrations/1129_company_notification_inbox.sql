-- 1129_company_notification_inbox.sql
-- THE COMPANY NOTIFICATION INBOX, and the declared delivery route that fills it.
--
-- Owner, 2026-11-24, verbatim: *"Stop sending daily messages about data not uploading instead create
-- a [one] for each compay in their notifications inbox and overwrite if it is the same message
-- everyday"*.
--
-- MEASURED FIRST (read-only, storeops.alert_log, 21 days to 2026-11-24): three companies received
-- 5-9 emails EVERY DAY for eight days straight on alert scope `connector` — stale feeds, errored
-- sweeps, unmatched report files and offline cameras. Five senders write that scope and each had its
-- own "don't repeat yourself" key; the stale-feed one carried the feed's own data date, so a feed
-- that stayed behind generated a NEW message every single day.
--
-- Two additive tables, no column on an existing one:
--   core.notification_inbox  — one row per (company, scope, finding). The next sighting UPDATES it.
--   storeops.alert_route     — which route a company+scope takes: message | inbox | both.
--
-- Index §CORE-49. Money-touching: NO (no ledger, no rate, no amount is read or written).
-- Applying it changes no behaviour on its own: the code that reads both tables ships in the same PR,
-- and until a route row says `inbox` every alert is still mailed exactly as it is today.

-- ── the inbox ─────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.notification_inbox (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid        NOT NULL,
  -- the alert scope (`storeops.alert_recipient.scope`) this finding came from. A VALUE, never a
  -- carrier or tenant name (CLAUDE.md RULE TWO).
  scope         text        NOT NULL,
  -- the finding's STABLE identity: the feed, the connector, the document. A date in this column is
  -- the defect this table exists to remove, which is why the backend refuses an empty one.
  inbox_key     text        NOT NULL,
  title         text        NOT NULL,
  body          text,
  severity      text        NOT NULL DEFAULT 'warning',
  deep_link     text,                 -- the page that FIXES it
  affected      jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- stores / feeds / codes this names
  occurrences   integer     NOT NULL DEFAULT 1,             -- how many times it has been reported
  first_seen_at timestamptz NOT NULL DEFAULT now(),         -- = how old the problem is
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  read_at       timestamptz,
  resolved_at   timestamptz,          -- set when a later check no longer reports it
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ONE ROW PER FINDING PER COMPANY. This unique index IS "overwrite if it is the same message
-- everyday": the backend upserts on it, so a second row for the same finding cannot exist.
CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_inbox_org_scope_key
  ON core.notification_inbox (org_id, scope, inbox_key);

-- the inbox read: this company's open items, newest sighting first
CREATE INDEX IF NOT EXISTS ix_notification_inbox_org_open
  ON core.notification_inbox (org_id, resolved_at, last_seen_at DESC);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'notification_inbox_severity_known') THEN
    ALTER TABLE core.notification_inbox
      ADD CONSTRAINT notification_inbox_severity_known
      CHECK (severity IN ('info', 'warning', 'error'));
  END IF;
  -- A key with a date in it would re-create the daily-message defect one row at a time. The code
  -- refuses one; so does the database, because a key is never legitimately empty.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'notification_inbox_key_not_blank') THEN
    ALTER TABLE core.notification_inbox
      ADD CONSTRAINT notification_inbox_key_not_blank CHECK (length(btrim(inbox_key)) > 0);
  END IF;
END $$;

-- ── the declared route ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.alert_route (
  id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid        NOT NULL,
  scope      text        NOT NULL,     -- an alert scope, or 'all' for this company's catch-all
  route      text        NOT NULL,     -- message | inbox | both
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_route_org_scope
  ON storeops.alert_route (org_id, scope);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alert_route_known') THEN
    ALTER TABLE storeops.alert_route
      ADD CONSTRAINT alert_route_known CHECK (route IN ('message', 'inbox', 'both'));
  END IF;
END $$;

-- THE HOUSE DEFAULT, WHICH EVERY TENANT INHERITS (CLAUDE.md: per-org config rows with house
-- defaults on org 00000000-0000-0000-0000-000000000001). A route is a POLICY — a switch — which is
-- where a house default is correct; it is not a tenant's own vocabulary. Any company can override
-- its own row and no other company's read is affected.
--   `connector`  = the four data-source senders (stale feed, errored/stalled sweep, a report file no
--                  rule matched, offline cameras)
--   `upload_due` = the setup-wizard "please upload this" reminders
INSERT INTO storeops.alert_route (org_id, scope, route)
VALUES ('00000000-0000-0000-0000-000000000001', 'connector',  'inbox'),
       ('00000000-0000-0000-0000-000000000001', 'upload_due', 'inbox')
ON CONFLICT (org_id, scope) DO UPDATE SET route = excluded.route, updated_at = now();

-- A row whose delivery was the company inbox. Extends the 1051 vocabulary; `alert_log.CHANNELS` is
-- the code's one home for the same list.
DO $$
BEGIN
  ALTER TABLE storeops.alert_log DROP CONSTRAINT IF EXISTS alert_log_channel_known;
  ALTER TABLE storeops.alert_log
    ADD CONSTRAINT alert_log_channel_known
    CHECK (channel IS NULL OR channel IN ('email', 'whatsapp', 'inbox'));
END $$;

-- The backend reaches every relation through PostgREST (CLAUDE.md: a migration grants what it
-- creates). service_role only — the browser never reads these tables directly.
GRANT SELECT, INSERT, UPDATE, DELETE ON core.notification_inbox TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON storeops.alert_route     TO service_role;

NOTIFY pgrst, 'reload schema';

-- REVERT:
--   DROP TABLE IF EXISTS core.notification_inbox;
--   DROP TABLE IF EXISTS storeops.alert_route;
--   ALTER TABLE storeops.alert_log DROP CONSTRAINT IF EXISTS alert_log_channel_known;
--   ALTER TABLE storeops.alert_log ADD CONSTRAINT alert_log_channel_known
--     CHECK (channel IS NULL OR channel IN ('email', 'whatsapp'));
--   (Dropping alert_route restores the mailed alerts: with no route row every scope reads
--    `message`, which is the pre-1129 behaviour exactly. No other table is touched.)
