-- 942_billpay_pickup.sql — Bill Payment Pickup & Deposit (owner directive 2026-09-02)
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "for the cash pick up we need one more pick up for the bill payment pickup and
-- deposit menu, just under the cash pick up module, the same process same wiring as the cash pick
-- up." Plus the same-day follow-up: the declared cash total IS "Total cash in store including
-- Bill Payments", the system should cross-check declared bill payments against the POS reports,
-- and management (market manager AND ABOVE — employees and DMs gated out) gets a one-screen cash
-- reconciliation.
--
-- WHAT SHIPS HERE (all additive):
--   1. commcalc.billpay_pickup — a SIBLING table of commcalc.cash_pickup (mig 034 + the mig-089
--      deposit-tracking columns folded in), holding the bill-payment-cash pickups. A sibling
--      table, NOT a kind column, deliberately: cash_pickup's UNIQUE(org_id, close_date,
--      store_code, employee_name) upsert key is load-bearing in three live upserts and
--      `_cash_position_core` reads the whole table as general-cash outflows — a kind column
--      would need a constraint swap (non-additive) and a `.eq(kind)` filter on EVERY existing
--      read, where one missed filter silently folds billpay rows into general cash (a money
--      defect, fail-open). The sibling table is fail-closed by construction. The PROCESS is
--      still one machinery: the confirm/undo/deposit endpoints are the same parameterized
--      implementations, pointed at this table.
--   2. commcalc.billpay_pickup_config — the notification recipient for billpay pickups (same
--      shape as cash_pickup_config; when unset, the billpay notify FALLS BACK to the cash-pickup
--      recipient so the flow works day one).
--   3. commcalc.cash_pickup_config.billpay_relieves_cash (BOOLEAN, default false) — the
--      no-double-count knob. EVIDENCE (LuxeLink live, 2026-09-02): the declared cash total
--      (daily_closing.t_cash) INCLUDES the ePay-on-cash dollars — epay_on_cash is a SUBSET
--      breakdown of t_cash (231 rows with epay_on_cash>0: 177 have epay_on_cash ≤ t_cash; the 54
--      exceptions are exactly what the mig-939 coverage recon flags), the deposit-recon split is
--      store_cash = t_cash − epay_on_cash BY DEFINITION (deposit_recon.cash_for_basis), and the
--      owner confirmed it verbatim ("Total cash in store including Bill Payments"). The general
--      cash-pickup envelope already sweeps the FULL declared cash (store_cash + epay_cash
--      snapshot, mig 034), so by default a billpay pickup must NOT also relieve the general
--      cash-on-hand movement (`_cash_position_core`) — that would relieve the same physical
--      dollars twice (double-count). Default false = today's behavior, byte-identical: billpay
--      pickups track purely against the billpay-cash side (epay_on_cash) as the physical
--      counterpart of the mig-939 remittance/coverage side. An org that operates SPLIT envelopes
--      (general envelope excludes ePay cash; billpay envelope picked up separately) flips this to
--      true and billpay pickups then fold into the general outflows exactly once (riding the
--      mig-938 verified-basis symmetry + zero floor unchanged).
--   4. storeops.tenants.cash_recon_visible_roles (TEXT[]) — WHO sees the management one-screen
--      cash recon (GET /closing/cash-recon-management). NULL = the house default "market manager
--      and above" (pay_visibility.DEFAULT_VISIBLE_ROLES — the mig-434 precedent, same fail-closed
--      resolution); a tenant that names roles differently lists them here. Config, never code
--      (RULE TWO).
--
-- MONEY: nothing here moves a booked number for any org. The relief knob defaults to false
-- (today's behavior); there are ZERO billpay_pickup rows at creation, so even a true value would
-- move nothing until pickups are recorded. No org seed required; the (non-money) example below is
-- commented out per the mig-622/933/938/939 owner-gate convention anyway.
-- Additive + idempotent. Run in the Supabase SQL editor.

-- ── 1. the sibling pickup table (mig 034 shape + mig 089 deposit columns) ───────────────────────
CREATE TABLE IF NOT EXISTS commcalc.billpay_pickup (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  close_date        DATE NOT NULL,
  store_code        TEXT NOT NULL DEFAULT '',
  store_name        TEXT,
  employee_name     TEXT NOT NULL DEFAULT '',
  amount            NUMERIC DEFAULT 0,          -- bill-pay cash snapshot (daily_closing.epay_on_cash)
  picked_up         BOOLEAN DEFAULT true,
  picked_up_by      TEXT,                       -- DM name
  picked_up_at      TIMESTAMPTZ DEFAULT now(),
  note              TEXT,
  created_at        TIMESTAMPTZ DEFAULT now(),
  -- deposit tracking (the mig-089 columns, same semantics)
  dm_employee_id    TEXT,
  disposition       TEXT,                       -- 'deposited' | 'handed_to_mgmt'
  handed_to         TEXT,
  deposit_slip_path TEXT,
  deposit_amount    NUMERIC,
  declared_amount   NUMERIC,                    -- the declared ePay-on-cash it should match
  deposit_ocr       JSONB,
  deposit_matched   BOOLEAN,
  deposit_flagged   BOOLEAN DEFAULT false,
  deposit_note      TEXT,
  deposited_at      TIMESTAMPTZ,
  UNIQUE (org_id, close_date, store_code, employee_name)
);
CREATE INDEX IF NOT EXISTS billpay_pickup_date ON commcalc.billpay_pickup (org_id, close_date);

-- ── 2. the notification recipient (same shape as cash_pickup_config) ────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.billpay_pickup_config (
  org_id             UUID PRIMARY KEY,
  recipient_name     TEXT,
  recipient_email    TEXT,
  recipient_whatsapp TEXT,
  notify_email       BOOLEAN DEFAULT true,
  notify_whatsapp    BOOLEAN DEFAULT true,
  updated_at         TIMESTAMPTZ DEFAULT now()
);

-- ── 3. the no-double-count knob (house default = today's behavior) ──────────────────────────────
ALTER TABLE commcalc.cash_pickup_config
  ADD COLUMN IF NOT EXISTS billpay_relieves_cash BOOLEAN DEFAULT false;
COMMENT ON COLUMN commcalc.cash_pickup_config.billpay_relieves_cash IS
  'false (default) = the general cash-pickup envelope carries the FULL declared cash (which '
  'includes ePay-on-cash), so billpay pickups track only the bill-pay side and never relieve the '
  'general cash-on-hand movement (no double-count). true = this org operates split envelopes: '
  'billpay pickups fold into the general cash outflows (_cash_position_core) exactly once.';

-- ── 4. management cash-recon visibility roles (mig-434 pay_visible_roles precedent) ─────────────
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS cash_recon_visible_roles TEXT[];
COMMENT ON COLUMN storeops.tenants.cash_recon_visible_roles IS
  'Roles allowed to open the management one-screen cash recon (/closing/cash-recon-management). '
  'NULL = house default "market manager and above" (pay_visibility.DEFAULT_VISIBLE_ROLES); '
  'scope-all roles always pass. Employees and DMs are gated out by default (owner 2026-09-02).';

-- ── RLS: blanket open_all to match the sibling commcalc tables (mig 034 precedent) ──────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.billpay_pickup', 'commcalc.billpay_pickup_config'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- ⛔ ORG EXAMPLE — COMMENTED OUT (owner-gate convention, mig 622/933/938/939). Only needed if an
-- org moves to SPLIT envelopes (general envelope excludes ePay cash). LuxeLink today operates
-- combined envelopes (cash_pickup.amount snapshots store_cash + epay_cash), so the default false
-- is correct for them and NO seed is required.
--
-- INSERT INTO commcalc.cash_pickup_config (org_id, billpay_relieves_cash)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', true)
-- ON CONFLICT (org_id) DO UPDATE
--   SET billpay_relieves_cash = EXCLUDED.billpay_relieves_cash, updated_at = now();
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 942 complete — billpay_pickup (+config), billpay_relieves_cash knob, cash_recon_visible_roles' AS status;

-- REVERT:
--   DROP TABLE IF EXISTS commcalc.billpay_pickup;
--   DROP TABLE IF EXISTS commcalc.billpay_pickup_config;
--   ALTER TABLE commcalc.cash_pickup_config DROP COLUMN IF EXISTS billpay_relieves_cash;
--   ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS cash_recon_visible_roles;
--   (the billpay endpoints degrade to empty lists / defaults; _cash_position_core's fold reads
--    the knob defensively and degrades to false — byte-identical general cash movement.)
