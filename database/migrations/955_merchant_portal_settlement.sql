-- 955_merchant_portal_settlement.sql — merchant-processor portal feed: settlement + funding storage
-- (owner directive 2026-09-04)
--
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- READ THIS IF YOU ARE BUILDING THE DAILY-CLOSING RECON  (sibling work, closing module)
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- The owner's ask: "a lot of tenants will be using 3rd party credit card processor which is not
-- integrated to the pos, which is recorded as external credit card … need to scrape the reports on a
-- daily basis and tally with our platform as entered by the employees."
--
-- THIS migration owns the PROCESSOR side of that tally (what the merchant portal says the store took).
-- The EMPLOYEE side — the new daily-closing field, its per-tenant label ("White machine" for the Boost
-- and Total tenants, "External credit card" elsewhere) and the recon UI — is the closing module's, and
-- nothing here touches commcalc.daily_closing or the closing schema.
--
-- THE ONE TABLE THE RECON READS:  commcalc.merchant_settlement_day
--   Grain:  org × source × merchant_id × business_date × card_brand.
--   Tally:  sum net_amount (or gross_amount, per the org's convention) over card_brand for a
--           (store_code, business_date) and compare with the employee-entered figure.
--   Pick the right side with `settlement_role`:
--       'external_cc'    the standalone terminal NOT integrated to the POS  → the closing field
--                        (PayAnywhere / Payments Hub, both current tenants).
--       'pos_merchant'   the merchant provider behind the POS's own card tender → the POS card tender
--                        (TransFirst TransLink for Total, ClientLine/BusinessTrack for Boost).
--     The role is per SOURCE (data_source.settlement_role, defaulted per portal), so a tenant that
--     runs a portal in the other role changes a config row, never code (RULE TWO).
--   Store attribution: `store_code` is resolved at ingest through storeops.store_merchant_id (mig 902,
--     the canonical (org, processor, merchant_id) → store_code map — NO new mapping table). A row whose
--     merchant id is not mapped yet lands with store_code NULL and is reported as an unresolved
--     merchant so the operator can map it in the existing store-setup panel. NEVER treat a NULL
--     store_code row as $0 for a store: exclude it and surface it, or the recon under-reports.
--   Idempotency: UNIQUE (org_id, source_id, merchant_id, business_date, card_brand). A re-pull of the
--     same day REPLACES that day's figures (portals restate); it never duplicates them. Several export
--     lines for one key (per terminal, per batch) are SUMMED before the upsert by
--     merchant_portals.dedupe_settlement — the day is the grain, so a per-terminal line must add, not
--     overwrite.
--
-- SECOND TABLE, DIFFERENT GRAIN:  commcalc.merchant_settlement_batch — one row per FUNDING event
--   (money leaving the processor for the bank). This is the cash/deposit recon's input (§12), NOT the
--   daily closing tally. Kept apart on purpose: a deposit covers several business days and summing the
--   two tables together would double-count. Do not read it for the closing recon.
--
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- NOT MONEY-MOVING. This migration only CAPTURES what a processor reports. It books nothing to the
-- P&L, pays no commission, and writes no ledger. The recon that consumes it is read-only.
--
-- SAFE: additive + idempotent. New tables + new nullable columns on an existing table; nothing
-- existing changes and every other processor ignores the new columns.

-- ── 1. Settlement, at the grain the daily-closing recon tallies ──────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.merchant_settlement_day (
  id               BIGSERIAL PRIMARY KEY,
  org_id           UUID NOT NULL,
  source_id        UUID,                    -- commcalc.data_source.id — WHICH login produced this
  portal_key       TEXT NOT NULL,           -- 'payanywhere' | 'transfirst' | 'businesstrack' (extensible)
  report_key       TEXT,                    -- which of that portal's reports this row came from
  settlement_role  TEXT NOT NULL DEFAULT 'external_cc',     -- 'external_cc' | 'pos_merchant' (shared with closing recon)

  business_date    DATE NOT NULL,           -- the processor's business day (NOT our upload day)
  merchant_id      TEXT,                    -- the PORTAL'S OWN merchant/MID identifier
  terminal_id      TEXT,                    -- the portal's terminal/device id, when the report has one
  store_label      TEXT,                    -- the DBA / location string the portal prints (diagnostic)
  store_code       TEXT,                    -- OUR store, resolved via storeops.store_merchant_id (902)
  card_brand       TEXT NOT NULL DEFAULT 'unknown',  -- visa|mastercard|amex|discover|debit|ebt|other|unknown

  gross_amount     NUMERIC(14,2) NOT NULL DEFAULT 0,
  refund_amount    NUMERIC(14,2) NOT NULL DEFAULT 0,
  net_amount       NUMERIC(14,2) NOT NULL DEFAULT 0,   -- the portal's own net when published, else gross-refunds
  fee_amount       NUMERIC(14,2) NOT NULL DEFAULT 0,
  txn_count        INTEGER NOT NULL DEFAULT 0,
  currency         TEXT NOT NULL DEFAULT 'USD',

  batch_ref        TEXT,                    -- the portal's batch/deposit reference, when the row has one
  source_line      INTEGER,                 -- line number in the export (traceability)
  raw              JSONB,                   -- the export row verbatim — never re-derive, always traceable
  pulled_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotent re-pull: the same (source, merchant, day, brand) is ONE row, restated in place.
-- COALESCE on merchant_id because a single-MID portal export may omit the column entirely.
CREATE UNIQUE INDEX IF NOT EXISTS merchant_settlement_day_key
  ON commcalc.merchant_settlement_day (org_id, source_id, COALESCE(merchant_id, ''), business_date, card_brand);
-- The recon's read path: one store's days.
CREATE INDEX IF NOT EXISTS merchant_settlement_day_store
  ON commcalc.merchant_settlement_day (org_id, store_code, business_date);
-- The "which merchant ids are still unmapped?" operator view.
CREATE INDEX IF NOT EXISTS merchant_settlement_day_unresolved
  ON commcalc.merchant_settlement_day (org_id, portal_key, merchant_id)
  WHERE store_code IS NULL;
-- Freshness / per-role reads.
CREATE INDEX IF NOT EXISTS merchant_settlement_day_role
  ON commcalc.merchant_settlement_day (org_id, settlement_role, business_date);

COMMENT ON TABLE commcalc.merchant_settlement_day IS
  'Merchant-processor portal settlement at org x source x merchant x business_date x card_brand. The PROCESSOR side of the daily closing card tally: settlement_role=external_cc is the standalone terminal not integrated to the POS (the "external credit card" / "white machine" closing field), pos_merchant is the POS card tender. Written by the daily portal scrape (merchant_portal_sweep); read-only for every consumer. store_code resolves via storeops.store_merchant_id (mig 902); a NULL store_code is an UNMAPPED merchant id and must be surfaced, never counted as zero.';
COMMENT ON COLUMN commcalc.merchant_settlement_day.settlement_role IS
  'Which side of the daily tally this row answers: external_cc (standalone terminal, not in the POS) or pos_merchant (the POS card tender''s provider). Per SOURCE via data_source.settlement_role, defaulted per portal — config, never a code branch.';
COMMENT ON COLUMN commcalc.merchant_settlement_day.raw IS
  'The portal export row verbatim (header -> cell). Keeps every figure traceable to what the portal actually published, so a disputed recon is settled by evidence rather than by re-deriving.';

-- ── 2. Funding / deposit batches — a DIFFERENT grain (deposit recon, not the closing tally) ──────
CREATE TABLE IF NOT EXISTS commcalc.merchant_settlement_batch (
  id               BIGSERIAL PRIMARY KEY,
  org_id           UUID NOT NULL,
  source_id        UUID,
  portal_key       TEXT NOT NULL,
  report_key       TEXT,
  settlement_role  TEXT NOT NULL DEFAULT 'external_cc',

  deposit_date     DATE NOT NULL,           -- when the processor funded the bank
  batch_date       DATE,                    -- the business day the batch covers, when published
  merchant_id      TEXT,
  terminal_id      TEXT,
  store_label      TEXT,
  store_code       TEXT,
  batch_ref        TEXT,                    -- the PORTAL'S OWN batch/funding id

  deposit_amount   NUMERIC(14,2) NOT NULL DEFAULT 0,
  fee_amount       NUMERIC(14,2) NOT NULL DEFAULT 0,
  txn_count        INTEGER NOT NULL DEFAULT 0,
  currency         TEXT NOT NULL DEFAULT 'USD',

  source_line      INTEGER,
  raw              JSONB,
  pulled_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS merchant_settlement_batch_key
  ON commcalc.merchant_settlement_batch
     (org_id, source_id, COALESCE(merchant_id, ''), deposit_date, COALESCE(batch_ref, ''));
CREATE INDEX IF NOT EXISTS merchant_settlement_batch_store
  ON commcalc.merchant_settlement_batch (org_id, store_code, deposit_date);

COMMENT ON TABLE commcalc.merchant_settlement_batch IS
  'Merchant-processor FUNDING events (money leaving the processor for the bank), one row per batch/deposit. Input to the cash/deposit recon (SYSTEM_DATA_FLOW_INDEX section 12) — NOT the daily closing card tally, which reads merchant_settlement_day. A deposit spans several business days, so summing the two tables together double-counts.';

-- ── 3. Per-source portal config on the existing data_source registry (RULE TWO) ──────────────────
-- No new credential table and no new schedule table: a merchant portal IS a data_source login. It
-- reuses username / password / account_id / proxy_url / enabled / frequency / hour / next_run_at /
-- session_state / session_expires_at / auth_status (migs 083, 084) and the existing
-- POST /commcalc/data-sources/sweep/run-due scheduler. These columns add only what a merchant portal
-- needs beyond that.
ALTER TABLE commcalc.data_source
  ADD COLUMN IF NOT EXISTS settlement_role     TEXT,     -- per-source override of the portal's house role
  ADD COLUMN IF NOT EXISTS portal_reports      JSONB,    -- ["deposits","card_summary"] — which reports to pull
  ADD COLUMN IF NOT EXISTS portal_calibration  JSONB,    -- operator-confirmed selectors + column synonyms
  ADD COLUMN IF NOT EXISTS portal_window_days  INTEGER,  -- how many days back each daily pull re-fetches
  ADD COLUMN IF NOT EXISTS totp_secret         TEXT,     -- OPTIONAL authenticator-app secret (SECRET)
  ADD COLUMN IF NOT EXISTS session_warn_hours  NUMERIC,  -- "expiring soon" window for this source
  ADD COLUMN IF NOT EXISTS session_linked_at   TIMESTAMPTZ,  -- when a human last completed the live login
  ADD COLUMN IF NOT EXISTS health_notified_state TEXT,   -- notify-once bookkeeping (portal_session_health)
  ADD COLUMN IF NOT EXISTS health_notified_at  TIMESTAMPTZ;

COMMENT ON COLUMN commcalc.data_source.totp_secret IS
  'OPTIONAL authenticator-app (TOTP) shared secret for a portal where the OWNER has enrolled this account in an authenticator. Same posture as the password column: BACKEND-ONLY, listed in router._SOURCE_SECRETS so every API read strips it, never logged, never echoed, surfaced to the UI only as has_totp + an opaque mask (portal_totp.mask_totp_secret). Used ONLY to compute the same 6-digit code the owner''s own authenticator would show — the portal still demands and receives a valid second factor. NEVER used for SMS/email OTP (those are typed by a human on the live-login screencast) and never to work around a captcha.';
COMMENT ON COLUMN commcalc.data_source.portal_calibration IS
  'Per-source operator calibration for a portal whose DOM we do not hardcode: {report_key: {nav/menu/export selectors}, column_synonyms: {field: [header text]}}. Captured once on the live-login screencast and reused by every scheduled pull, so a portal that renames a column or moves a menu is a config edit, not a deploy.';
COMMENT ON COLUMN commcalc.data_source.settlement_role IS
  'Override of the portal''s house settlement role for THIS tenant''s login: external_cc (standalone terminal, not integrated to the POS) or pos_merchant. NULL = the portal default in merchant_portals.PORTALS. The two slugs are shared verbatim with closing/external_credit_recon.py, whose assemble_rows() rejects any other value.';
COMMENT ON COLUMN commcalc.data_source.session_linked_at IS
  'When a human last completed the live login for this source. With session_expires_at it drives the session-health chip (portal_session_health.evaluate), so a stale durable session is surfaced BEFORE the overnight pull fails rather than weeks later by a hole in the recon.';

-- ── 4. Register the feed in the EXISTING report→table registry the closing recon resolves through ──
-- commcalc.report_pull_map (mig 207) is how a consumer finds a scraped feed's table + column spelling
-- WITHOUT hardcoding either. closing/external_credit_recon.py resolves the settlement feed through it,
-- so this house row is the contract between the scrape side and the tally side: rename a column here
-- and the recon follows, with no code change on either side (RULE TWO).
--
-- column_map is written in the {canonical: source_header} direction normalize_settlement_rows accepts.
-- Only `day`/`amount`/`role` actually need naming (store_code and merchant_id already match its
-- default spellings) — they are listed anyway so the mapping is explicit rather than relying on a
-- default that could drift.
INSERT INTO commcalc.report_pull_map
  (org_id, report_key, display_name, target_table, column_map, param_spec, export_pref, enabled,
   sort_order, processor)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'merchant_settlement',
   'Merchant portal settlement (per store/day/card brand)',
   'commcalc.merchant_settlement_day',
   '{"day":"business_date","amount":"net_amount","role":"settlement_role",'
   '"store_code":"store_code","merchant_id":"merchant_id"}'::jsonb,
   '{}'::jsonb, 'csv', true, 60, 'merchant_portal'),
  ('00000000-0000-0000-0000-000000000001', 'merchant_funding',
   'Merchant portal funding / deposits (per batch)',
   'commcalc.merchant_settlement_batch',
   '{"day":"deposit_date","amount":"deposit_amount","role":"settlement_role",'
   '"store_code":"store_code","merchant_id":"merchant_id"}'::jsonb,
   '{}'::jsonb, 'csv', true, 61, 'merchant_portal')
ON CONFLICT (org_id, report_key) DO UPDATE
  SET target_table = EXCLUDED.target_table,
      column_map   = EXCLUDED.column_map,
      display_name = EXCLUDED.display_name,
      processor    = EXCLUDED.processor,
      updated_at   = NOW();

notify pgrst, 'reload schema';
select 'Migration 955 — merchant portal settlement + funding storage, and per-source portal config' as status;

-- REVERT:
--   drop table if exists commcalc.merchant_settlement_day;
--   drop table if exists commcalc.merchant_settlement_batch;
--   alter table commcalc.data_source
--     drop column if exists settlement_role, drop column if exists portal_reports,
--     drop column if exists portal_calibration, drop column if exists portal_window_days,
--     drop column if exists totp_secret, drop column if exists session_warn_hours,
--     drop column if exists session_linked_at, drop column if exists health_notified_state,
--     drop column if exists health_notified_at;
