-- 850_referral.sql — Referral: QR-code customer referrals, activation-gated commission, anti-fraud
--
-- OWNER DIRECTIVE 2026-08-13 (sanjot@): "A top-of-the-line referral system. Staff create a referral
-- which produces a QR code sent to the REFERRING party. The referred customer comes back to the store,
-- the QR is scanned, the sale gets done. Once the LINE IS ACTIVATED for the referred customer, an
-- APPROVAL goes to the referring party that they have generated commission toward the referral, in a
-- USER-DEFINED AMOUNT, paid out on a USER-DEFINED DATE. Must be FOOLPROOF so nobody can scam the
-- system. Make it a NEW MODULE ... Must capture the referred customer's PHONE NUMBER and NAME and WHAT
-- PRODUCT they're interested in, with checkbox 'bubble' options: Phone, Activations, Tablet, BYOD,
-- Home Internet, Accessories."
--
-- BAND: 850–899 is hereby RESERVED FOR REFERRAL. CRM took 800–849 (its header reserves that band); the
-- next module up from CRM is Referral, which belongs to no existing module agent. Highest number
-- applied anywhere before this: 800 (CRM). Referral picks up the next free band so the two never
-- collide on a migration number.
--
-- SCHEMA CHOICE — core.referral_*, NOT a `referral` schema. Identical reasoning to migration 800 (CRM):
-- PostgREST only serves schemas on the project's "Exposed schemas" dashboard list, which is not
-- reachable from here (the Management API returns 403 for /postgrest and there is no pgrst.db_schemas
-- role setting to patch), so every `.schema("referral")` call would 404. `core` is exposed, already
-- holds the platform-wide + CRM tables, and the referral_ prefix keeps it legible next to crm_*.
--
-- SAFE: additive + idempotent (create ... if not exists / on conflict do nothing). Re-runnable.
-- MONEY: referral commission is its OWN ledger (core.referral.commission_amount / payout_date). It
--        touches NO existing payout number, rate, plan, or paid/earned column in commcalc/closing/etc.
--        A referral payout is APPROVAL-GATED and never auto-posts into any pay run — it is recorded
--        here and here only until an operator wires an export, so this migration cannot move a dollar
--        anywhere the old system pays from.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5). The backend
--           service role bypasses RLS; the frontend anon key is auth-only and never reaches these. The
--           PUBLIC redemption endpoint authenticates with an HMAC capability TOKEN (see download_token
--           precedent), NOT the anon key — so no RLS policy is required for it either.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. CONFIGURATION (RULE TWO — nothing about a tenant's referral program is hard-coded)
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- Per-tenant switches. One row per org. Every anti-fraud threshold and money default lives here so an
-- operator tunes the program without a code change; the backend resolve_config() mirrors these defaults
-- so the module still works before the row exists.
CREATE TABLE IF NOT EXISTS core.referral_config (
  org_id                     UUID PRIMARY KEY,
  -- MONEY defaults (each referral may override; these are the fall-back)
  default_commission_amount  NUMERIC(12,2) NOT NULL DEFAULT 25.00,  -- $ the referrer earns per activated referral
  default_payout_offset_days INT  NOT NULL DEFAULT 30,              -- payout date = approval date + this many days
  -- QR / token lifetime
  qr_expiry_hours            INT  NOT NULL DEFAULT 168,             -- 7d: how long the signed QR stays scannable
  redemption_window_hours    INT  NOT NULL DEFAULT 72,             -- how long after creation the customer may redeem
  -- anti-fraud thresholds
  max_referrals_per_referrer INT  NOT NULL DEFAULT 10,             -- velocity cap per referrer per window (0 = no cap)
  velocity_window_days       INT  NOT NULL DEFAULT 30,             -- rolling window for the velocity cap
  duplicate_match            TEXT NOT NULL DEFAULT 'phone'
                               CHECK (duplicate_match IN ('phone','none')),
  require_approval           BOOLEAN NOT NULL DEFAULT true,         -- payout needs an explicit human approval
  self_referral_block        BOOLEAN NOT NULL DEFAULT true,        -- referrer phone may not equal customer phone
  updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. OPERATIONAL — the referral record
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The lifecycle is an explicit STATE MACHINE enforced in app.modules.referral.referral_core
-- (can_transition): created → sent → redeemed → sale_logged → activated → commission_pending →
-- approved → paid, plus the terminal/exception states expired / rejected / void / flagged_fraud.
-- The DB stores the state; the pure state-machine decides which moves are legal. Every move writes an
-- immutable core.referral_audit row (who / when / from→to / reason).
CREATE TABLE IF NOT EXISTS core.referral (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL,
  referral_no           BIGINT GENERATED BY DEFAULT AS IDENTITY,

  -- ── the REFERRING party (gets the QR + the commission) ───────────────────────────────────────
  referrer_name         TEXT,
  referrer_phone        TEXT,
  referrer_email        TEXT,
  -- The 10-digit national key, IDENTICAL rule to core.crm_lead.phone_norm (migration 800) and to
  -- app.modules.referral.referral_core.normalize_phone(). Self-referral + duplicate checks key on this,
  -- so the SQL and Python must stay byte-identical (an extension is written at the END, so keep the
  -- FIRST ten digits — a naive last-10 turns "5165550134 x22" into a key that matches nothing).
  referrer_phone_norm   TEXT GENERATED ALWAYS AS (
                          CASE
                            WHEN LENGTH(REGEXP_REPLACE(COALESCE(referrer_phone,''), '[^0-9]', '', 'g')) < 7
                              THEN ''
                            WHEN LENGTH(REGEXP_REPLACE(COALESCE(referrer_phone,''), '[^0-9]', '', 'g')) > 10
                                 AND LEFT(REGEXP_REPLACE(COALESCE(referrer_phone,''), '[^0-9]', '', 'g'), 1) = '1'
                              THEN SUBSTR(REGEXP_REPLACE(COALESCE(referrer_phone,''), '[^0-9]', '', 'g'), 2, 10)
                            WHEN LENGTH(REGEXP_REPLACE(COALESCE(referrer_phone,''), '[^0-9]', '', 'g')) > 10
                              THEN LEFT(REGEXP_REPLACE(COALESCE(referrer_phone,''), '[^0-9]', '', 'g'), 10)
                            ELSE REGEXP_REPLACE(COALESCE(referrer_phone,''), '[^0-9]', '', 'g')
                          END) STORED,

  -- ── the REFERRED customer (captured at the store when the QR is scanned) ──────────────────────
  customer_name         TEXT,
  customer_phone        TEXT,
  customer_phone_norm   TEXT GENERATED ALWAYS AS (
                          CASE
                            WHEN LENGTH(REGEXP_REPLACE(COALESCE(customer_phone,''), '[^0-9]', '', 'g')) < 7
                              THEN ''
                            WHEN LENGTH(REGEXP_REPLACE(COALESCE(customer_phone,''), '[^0-9]', '', 'g')) > 10
                                 AND LEFT(REGEXP_REPLACE(COALESCE(customer_phone,''), '[^0-9]', '', 'g'), 1) = '1'
                              THEN SUBSTR(REGEXP_REPLACE(COALESCE(customer_phone,''), '[^0-9]', '', 'g'), 2, 10)
                            WHEN LENGTH(REGEXP_REPLACE(COALESCE(customer_phone,''), '[^0-9]', '', 'g')) > 10
                              THEN LEFT(REGEXP_REPLACE(COALESCE(customer_phone,''), '[^0-9]', '', 'g'), 10)
                            ELSE REGEXP_REPLACE(COALESCE(customer_phone,''), '[^0-9]', '', 'g')
                          END) STORED,
  -- Product interest: the exact six 'bubble' options from the directive, stored as a normalized JSON
  -- array of canonical labels. Validated against the allowed set app-side
  -- (referral_core.ALLOWED_PRODUCTS) on every write, so a forged public POST cannot inject a new one.
  products              JSONB NOT NULL DEFAULT '[]'::jsonb,

  -- ── lifecycle ─────────────────────────────────────────────────────────────────────────────────
  status                TEXT NOT NULL DEFAULT 'created'
                          CHECK (status IN ('created','sent','redeemed','sale_logged','activated',
                                            'commission_pending','approved','paid',
                                            'expired','rejected','void','flagged_fraud')),
  -- Token version — bumping this (a re-issue) invalidates every previously-minted QR for this referral,
  -- because the signature covers the id + this version. Single-use redemption also flips status past
  -- `sent`, so a second scan is refused; the version is the belt to the single-use suspenders.
  token_version         INT  NOT NULL DEFAULT 1,
  redeem_expires_at     TIMESTAMPTZ,          -- created_at + min(qr_expiry, redemption_window); redeem past this = expired

  -- clocks, one per state entered (append-only in spirit; a state is stamped once)
  sent_at               TIMESTAMPTZ,
  redeemed_at           TIMESTAMPTZ,
  sale_logged_at        TIMESTAMPTZ,
  activated_at          TIMESTAMPTZ,
  submitted_at          TIMESTAMPTZ,          -- entered commission_pending (sent for approval)
  approved_at           TIMESTAMPTZ,
  paid_at               TIMESTAMPTZ,
  closed_at             TIMESTAMPTZ,          -- entered a terminal exception state

  -- links out to the sale / activation. These are REFERENCES a human types/pastes — real activation
  -- verification against a carrier feed does not exist yet, so these are advisory evidence, not proof.
  sale_ref              TEXT,
  activation_ref        TEXT,

  -- ── MONEY (this module's own ledger — see the MONEY note in the header) ───────────────────────
  commission_amount     NUMERIC(12,2),        -- user-defined per referral; defaults from referral_config
  payout_date           DATE,                 -- user-defined; defaults to approval date + offset
  approver_employee_id  TEXT,                 -- who approved (segregation of duties: never the creator)
  approver_app_user_id  UUID,

  -- ── anti-fraud ────────────────────────────────────────────────────────────────────────────────
  fraud_flag            BOOLEAN NOT NULL DEFAULT false,
  fraud_reason          TEXT,

  -- ── routing / scope / ownership ───────────────────────────────────────────────────────────────
  store_code            TEXT,
  market                TEXT,
  created_by            TEXT,                 -- employee_id of the rep who created it
  created_by_app_user_id UUID,
  notes                 TEXT,

  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, referral_no),
  UNIQUE (org_id, id)                          -- composite target so children FK tenant-scoped (cf. mig 728)
);
CREATE INDEX IF NOT EXISTS referral_org_status   ON core.referral(org_id, status);
CREATE INDEX IF NOT EXISTS referral_org_referrer ON core.referral(org_id, referrer_phone_norm);
CREATE INDEX IF NOT EXISTS referral_org_customer ON core.referral(org_id, customer_phone_norm);
CREATE INDEX IF NOT EXISTS referral_org_store    ON core.referral(org_id, store_code);
CREATE INDEX IF NOT EXISTS referral_org_created  ON core.referral(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS referral_org_payout   ON core.referral(org_id, payout_date);

-- Append-only audit — every state transition AND every fraud decision (who / when / from→to / reason).
-- Per [[recalc-additive-never-erase-review]] nothing here is ever updated or deleted by the app: the
-- trail of who approved a payout, and why a referral was flagged, is exactly what makes the system
-- foolproof after the fact.
CREATE TABLE IF NOT EXISTS core.referral_audit (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  referral_id       UUID NOT NULL,
  action            TEXT NOT NULL,            -- create | transition | fraud_check | approve | reject | pay | notify | ...
  from_status       TEXT,
  to_status         TEXT,
  reason            TEXT,
  actor_employee_id TEXT,
  actor_app_user_id UUID,
  actor_kind        TEXT NOT NULL DEFAULT 'staff'
                      CHECK (actor_kind IN ('staff','customer','system')),  -- customer = the public redeem
  meta              JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  FOREIGN KEY (org_id, referral_id) REFERENCES core.referral(org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS referral_audit_org_ref  ON core.referral_audit(org_id, referral_id, created_at DESC);
CREATE INDEX IF NOT EXISTS referral_audit_org_time ON core.referral_audit(org_id, created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. DEFAULT CONTENT — idempotent per-tenant seed
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Mirrors core.seed_crm_defaults(): ON CONFLICT DO NOTHING, so it NEVER clobbers a config an admin
-- edited. Called by the migration for every existing tenant AND lazily by the backend on first
-- Referral access, so a tenant created later self-provisions.
CREATE OR REPLACE FUNCTION core.seed_referral_defaults(p_org UUID)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, pg_catalog
AS $fn$
BEGIN
  INSERT INTO core.referral_config (org_id) VALUES (p_org) ON CONFLICT (org_id) DO NOTHING;
END;
$fn$;

-- Seed every EXISTING tenant. New tenants self-provision on first Referral access (backend calls this).
DO $$
DECLARE o RECORD;
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'storeops' AND table_name = 'tenants') THEN
    FOR o IN EXECUTE 'SELECT DISTINCT org_id FROM storeops.tenants WHERE org_id IS NOT NULL' LOOP
      PERFORM core.seed_referral_defaults(o.org_id);
    END LOOP;
  ELSE
    PERFORM core.seed_referral_defaults('00000000-0000-0000-0000-000000000001'::uuid);
  END IF;
END $$;

-- Register the module in the canonical registry (mig 700), mirroring MODULE_CATALOG in
-- app/modules/core/entitlements.py. The in-code dict is the fallback, so the app is identical whether
-- or not this ran; this is what makes the module appear in the tenant-entitlement + billing pickers.
INSERT INTO core.module_catalog (key, label, sort_order) VALUES
  ('referral', 'Referral / QR Rewards', 130)
ON CONFLICT (key) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 4. SECURITY — RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The backend's service role bypasses RLS and needs no policy; the frontend anon key is auth-only and
-- must never reach these tables; the PUBLIC redemption endpoint authenticates with an HMAC capability
-- token, not the anon key, so it needs no policy either. Migration 731's ddl_command_end auto-lock
-- covers new objects too, but this is explicit so the migration is correct on its own. The pattern
-- catches the bare `referral` table AND referral_config / referral_audit.
DO $$
DECLARE t RECORD;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables
           WHERE schemaname = 'core' AND (tablename = 'referral' OR tablename LIKE 'referral\_%') LOOP
    EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    EXECUTE format('REVOKE ALL ON core.%I FROM anon, authenticated', t.tablename);
    EXECUTE format('GRANT ALL ON core.%I TO service_role', t.tablename);
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION core.seed_referral_defaults(UUID) FROM anon, authenticated, PUBLIC;
GRANT EXECUTE ON FUNCTION core.seed_referral_defaults(UUID) TO service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
