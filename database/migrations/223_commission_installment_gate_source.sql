-- 223_commission_installment_gate_source.sql — carrier-agnostic paid-gate evidence source (RULE TWO)
--
-- WHY: the sale-triggered installment engine (mig 201, commcalc.sale_installment_engine) proves the
-- "dealer paid this month" gate ONLY from raw_mi — a Boost/ePay-only table. For master-agent-fed tenants
-- (Total Wireless via VidaPay; data in raw_ma_* from mig 083) raw_mi is EMPTY, so every gated installment
-- month is withheld_unpaid FOREVER (luxelink "3MR Commission Payment" → all M1-M3 withheld tenant-wide).
-- That is one carrier's statement table baked into the gate. This table makes the gate's EVIDENCE SOURCE
-- config, not a code constant, exactly like mig-209 whatif_source_config does for the What-If page:
--   • boost mode  → prove paid from raw_mi (MI+ATU residual) — byte-identical to today.
--   • plan  mode  → prove paid from raw_ma_commission (per-month spiff / rebate / device-margin per IMEI).
-- Which raw_ma_commission columns count as month-N evidence, which device columns to join on, the min
-- amount, and the highest month with a per-month payout column are all EDITABLE, never constants.
--
-- RESOLUTION (in sale_installment_engine._resolve_gate_cfg): (1) the org's row for the exact carrier_id →
-- (2) the org's mode-default row (carrier_id = nil, carrier_mode = 'boost'|'plan') → (3) the HOUSE
-- mode-default row (every tenant inherits the two seeds) → (4) a code fallback per mode. So every tenant
-- gets sensible defaults from the two seeded house rows with NO per-tenant seed, and an admin can override
-- per carrier. gate_source resolves to 'boost_mi' for every Boost carrier with ZERO owner action.
--
-- SAFE: additive + idempotent. Nothing existing changes; the engine degrades to the SAME code defaults when
-- this table is absent (Boost byte-identical; plan-mode still resolves to raw_ma_commission from the code
-- default, so the fix is live from code and this table only adds per-carrier override + explicit seeds).
-- RLS open_all (matches every commcalc table today).

CREATE TABLE IF NOT EXISTS commcalc.installment_gate_source_config (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  carrier_id    UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',  -- nil = mode-default row
  carrier_mode  TEXT NOT NULL DEFAULT 'boost',   -- used when carrier_id is nil: 'boost' | 'plan'
  -- WHICH statement table proves "dealer paid this month" for the gate:
  gate_source   TEXT NOT NULL DEFAULT 'boost_mi', -- 'boost_mi' (raw_mi MI+ATU) | 'ma_commission' (raw_ma_commission)
  -- MA-source join + evidence config (ignored when gate_source='boost_mi'):
  ma_device_fields       TEXT[] NOT NULL DEFAULT ARRAY['imei','sim'],   -- raw_ma_commission cols the sold device serial (raw_sales.serial_1) is digit-matched against
  ma_month_field_prefix  TEXT   NOT NULL DEFAULT 'spiff_m',             -- month N payout col = prefix || N  (spiff_m1..spiff_m6)
  ma_max_month           INT    NOT NULL DEFAULT 6,                     -- highest month with a per-month payout column
  ma_month1_extra_fields TEXT[] NOT NULL DEFAULT ARRAY['rebate','device_margin'], -- extra activation-time payout cols that ALSO count for month 1
  ma_min_amount          NUMERIC NOT NULL DEFAULT 0.01,                 -- min amount (in the payout direction) on a month's evidence col that counts as PAID. NOTE: 0 is NOT a "no minimum" sentinel — the engine CLAMPS a resolved value <= 0 back to its code default (0.01), else an all-zero device would read as paid.
  ma_payout_sign         NUMERIC NOT NULL DEFAULT -1,                   -- DIRECTION of a payout in the MA columns (MA amounts are NEGATIVE = payout to dealer → -1). The gate is DIRECTION-AWARE: paid iff (net * ma_payout_sign) >= ma_min_amount, so a net CLAWBACK (a reversal that flips the net to a CHARGE) does NOT prove paid. Coerced to +/-1 (0/invalid → -1).
  is_active     BOOLEAN NOT NULL DEFAULT true,
  notes         TEXT,
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, carrier_id, carrier_mode)
);
CREATE INDEX IF NOT EXISTS installment_gate_source_config_org ON commcalc.installment_gate_source_config (org_id);

COMMENT ON TABLE commcalc.installment_gate_source_config IS
  'Config-driven paid-gate evidence source for the sale-triggered installment engine (mig 201). One row per (org, carrier) or a mode-default row (carrier_id=nil). Every tenant inherits the two seeded HOUSE mode-default rows unless it overrides. Resolved in commcalc.sale_installment_engine._resolve_gate_cfg. Boost carriers resolve to raw_mi (byte-identical); master-agent-fed carriers to raw_ma_commission per-month spiffs.';

-- Seed the two HOUSE mode-default rows (every tenant inherits them; admins override per carrier).
-- boost mode = today's behavior byte-identical (raw_mi MI+ATU residual gate).
-- plan mode  = master-agent-fed carriers (raw_ma_commission: net per-month spiff IN THE PAYOUT DIRECTION =
--              dealer paid that month; month 1 also honors rebate/device_margin activation payouts).
-- NOTE (MA gate semantics): the MA feed carries no reliable per-month line status (line_status is NULL in
-- real rows), so in MA mode ALL schedule gate_modes (active_status/nonzero_residual/paid_residual) collapse
-- to the SAME evidence test — a posted payout IS the proof the line is active + paying.
INSERT INTO commcalc.installment_gate_source_config
  (org_id, carrier_id, carrier_mode, gate_source, ma_device_fields, ma_month_field_prefix,
   ma_max_month, ma_month1_extra_fields, ma_min_amount, ma_payout_sign, notes)
VALUES
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'boost',
   'boost_mi', ARRAY['imei','sim'], 'spiff_m', 6, ARRAY['rebate','device_margin'], 0.01, -1,
   'House/Boost default — gate proven from ePay raw_mi (subscriber Active + MI+ATU residual > 0). Byte-identical to pre-mig-223.'),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 'plan',
   'ma_commission', ARRAY['imei','sim'], 'spiff_m', 6, ARRAY['rebate','device_margin'], 0.01, -1,
   'Master-agent (VidaPay/Total) default — gate proven from raw_ma_commission: month N is paid when the device''s (IMEI-matched, adjustment rows summed) net spiff_mN is a payout of >= ma_min_amount in the ma_payout_sign direction (MA amounts negative = payout → sign -1; a net clawback does NOT pay); month 1 also counts rebate/device_margin activation payouts.')
ON CONFLICT (org_id, carrier_id, carrier_mode) DO NOTHING;

ALTER TABLE commcalc.installment_gate_source_config ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='installment_gate_source_config' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.installment_gate_source_config FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 223 complete — installment_gate_source_config seeded ('
       || (SELECT count(*) FROM commcalc.installment_gate_source_config
             WHERE org_id='00000000-0000-0000-0000-000000000001') || ' house rows)' AS status;
