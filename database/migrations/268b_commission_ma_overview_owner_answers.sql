-- 268b_commission_ma_overview_owner_answers.sql — mod-commission · the OWNER'S 2026-08-04 ANSWERS
--
-- ═══ WHICH FILE DO I RUN? ═══════════════════════════════════════════════════════════════════════
--   • You have NOT run 268 yet          →  run **268** only. It already contains everything below.
--   • You HAVE already run 268          →  run **THIS FILE**. It is the complete delta.
--   • Not sure                          →  run **THIS FILE**. It is additive + idempotent and safe on
--                                          either state; running both, in either order, is also safe.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
--
-- WHY: migration 268 shipped the MA "Overview of Accounts" cross-check with the tile definitions we
-- could infer. The owner then answered the open questions in chat on 2026-08-04, and two of the answers
-- CHANGED a definition. This file brings an already-run 268 up to those answers.
--
--   1. RESIDUAL — CONFIRMED unchanged: the portal's Residual is the same basis we already compute (the
--      What-If / finance residual-per-sub definition). Only the tile's explanatory note is refreshed.
--   2. COMMISSIONS PAID — CHANGED. Owner verbatim: "commission is only the current months commission
--      paid out on the activations which would be M1, these are not margins but paid commission based on
--      MRC. the current commission plan for total is M1 = 50%, M2-M6 = 75% each plus any applicable
--      spiff which change from time to time; M3-M6 is also a temporary spiff which can change over a
--      period of time." So the tile is the M1 leg (`spiff_m1`), NOT consumer_margin + device_margin.
--      VERIFIED LIVE 2026-08-04 on luxelink (998 rows, Feb–Jul 2026): M1 = $17,140.91 against an MRC
--      base of $32,366.51 = 53.0%, i.e. the owner's 50% plan plus the occasional flat spiff — while
--      `consumer_margin` is EMPTY (-0.00) and `device_margin` is only $4,700, so the old margin basis
--      would have reported $4.7K where the real commission is $17.1K.
--   3. ACTIVATION COUNT — REFINED. Owner: "total of new activations + port + byod, this does not include
--      any swap or upgrades." Port and BYOD are ATTRIBUTES of a fresh line (port_status /
--      activation_type2), not separate activation types, so the rule is an EXCLUSION of swap/upgrade
--      rather than an include-list that would double-count. VERIFIED LIVE: the real Activation Type
--      vocabulary in this export is ONLY 'New' (393) and 'Add' (605) — no swap/upgrade spelling occurs,
--      so the exclusion removes ZERO rows today. It is kept as a guard for exports that DO carry them.
--   4. APPEAL COUNT — CHANGED from "no source mapped" to a DERIVED WORKLIST. Owner: "that would be the
--      lines which don't get paid and have to be followed up like we did in Boost." The tile now counts
--      qualifying activation lines on which EVERY configured pay column is zero, and drills down to
--      those exact lines. Derived and read-only — it creates no appeal record.
--
-- 💰 THIS FILE MOVES $0. It adds one column, one config table, one read-only function, and UPDATEs the
-- tenant's own tile definitions. Nothing that decides a payout reads any of it: rep pay is POS sales x
-- Commission Plans. The M1–M6 rate plan below is the CARRIER's plan and drives only the EXPECTED column
-- of the cross-check.
--
-- 🔒 HAND-EDITS ARE NEVER CLOBBERED. Every UPDATE is guarded by `updated_by IS NULL`, which is true only
-- for rows written by 268's own seed — the tile-mapping endpoint stamps `updated_by` on every save. A
-- tile a human has already edited is left exactly as they left it.
--
-- ADDITIVE + IDEMPOTENT + RLS-ZERO-POLICY: safe to re-run; RLS on, NO policies, NO anon/authenticated
-- grants (contract §5). Degrades gracefully if never run: the code defaults in
-- backend/app/modules/commcalc/ma_overview.py carry the same definitions, so the page is already correct
-- — running this only makes the stored rows agree with the code.

-- ── 1) the multi-condition filter column ───────────────────────────────────────────────────────────
-- A tile can now AND several conditions ([{field,op,value}, …]); the single filter_field/op/value
-- triplet stays for the simple case and for back-compat (a tile with no `filters` behaves as before).
ALTER TABLE commcalc.ma_overview_tile_config ADD COLUMN IF NOT EXISTS filters JSONB;

COMMENT ON COLUMN commcalc.ma_overview_tile_config.filters IS
  'Row conditions ANDed: [{"field":"activation_type","op":"not_in","value":"Upgrade,Swap"}, …]. Wins '
  'over filter_field/filter_op/filter_value when present. Fields must be filterable dimensions of the '
  'tile''s source table (validated by the backend before a save).';

-- ── 2) the CARRIER's M1–M6 commission plan (rates change; spiffs are temporary → config, not code) ──
CREATE TABLE IF NOT EXISTS commcalc.ma_commission_month_rate (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  month_index    INT  NOT NULL CHECK (month_index BETWEEN 1 AND 6),
  rate_pct       NUMERIC NOT NULL DEFAULT 0,     -- percent OF the line's MRC
  spiff_flat     NUMERIC NOT NULL DEFAULT 0,     -- flat $ per activation ON TOP of the percentage
  effective_from DATE,                           -- NULL = always in force; newest <= period start wins
  note           TEXT,
  updated_by     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, month_index, effective_from)
);
CREATE INDEX IF NOT EXISTS ma_commission_month_rate_org
  ON commcalc.ma_commission_month_rate (org_id, month_index);
ALTER TABLE commcalc.ma_commission_month_rate ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE commcalc.ma_commission_month_rate IS
  'The CARRIER''s M1-M6 commission plan (percent of MRC + any flat spiff), per tenant and effective-'
  'dated. Drives the EXPECTED column of the MA Overview cross-check ONLY. Pays nobody: rep pay is POS '
  'sales x Commission Plans and never reads this table.';

INSERT INTO commcalc.ma_commission_month_rate (org_id, month_index, rate_pct, spiff_flat, note)
VALUES
  ('00000000-0000-0000-0000-000000000001', 1, 50, 0, 'Total plan 2026-08-04 (owner): M1 = 50% of MRC.'),
  ('00000000-0000-0000-0000-000000000001', 2, 75, 0, 'Total plan 2026-08-04 (owner): M2 = 75% of MRC.'),
  ('00000000-0000-0000-0000-000000000001', 3, 75, 0, 'Total plan 2026-08-04 (owner): M3 = 75% of MRC — TEMPORARY spiff, expect changes.'),
  ('00000000-0000-0000-0000-000000000001', 4, 75, 0, 'Total plan 2026-08-04 (owner): M4 = 75% of MRC — TEMPORARY spiff, expect changes.'),
  ('00000000-0000-0000-0000-000000000001', 5, 75, 0, 'Total plan 2026-08-04 (owner): M5 = 75% of MRC — TEMPORARY spiff, expect changes.'),
  ('00000000-0000-0000-0000-000000000001', 6, 75, 0, 'Total plan 2026-08-04 (owner): M6 = 75% of MRC — TEMPORARY spiff, expect changes.')
ON CONFLICT DO NOTHING;

-- ── 3) the follow-up-worklist function (the Appeal tile's source) ──────────────────────────────────
DROP FUNCTION IF EXISTS commcalc.ma_overview_unpaid_lines(uuid, text[], text[], text[], int);
CREATE OR REPLACE FUNCTION commcalc.ma_overview_unpaid_lines(
  p_org uuid, p_periods text[], p_accounts text[], p_pay_cols text[], p_limit int DEFAULT 20000)
RETURNS TABLE (
  merchant_account_id text, activation_order text, imei text, sim text, sku text,
  tx_date date, period text, user_name text, line_status text, suspension_reason text,
  port_status text, activation_type text, activation_type2 text, sub_type text,
  perfect_sale text, is_financed text, mrc_net_discount numeric, paid_total numeric
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    coalesce(nullif(btrim(c.merchant_account_id), ''), '?'),
    coalesce(btrim(c.activation_order), ''), coalesce(btrim(c.imei), ''),
    coalesce(btrim(c.sim), ''), coalesce(btrim(c.sku), ''),
    c.tx_date, coalesce(btrim(c.period), ''), coalesce(btrim(c.user_name), ''),
    coalesce(btrim(c.line_status), ''), coalesce(btrim(c.suspension_reason), ''),
    coalesce(btrim(c.port_status), ''), coalesce(btrim(c.activation_type), ''),
    coalesce(btrim(c.activation_type2), ''), coalesce(btrim(c.sub_type), ''),
    coalesce(btrim(c.perfect_sale), ''), coalesce(btrim(c.is_financed), ''),
    coalesce(c.mrc_net_discount, 0), 0::numeric
  FROM commcalc.raw_ma_commission c
  WHERE c.org_id = p_org
    AND (p_periods IS NULL OR array_length(p_periods, 1) IS NULL OR c.period = ANY (p_periods))
    AND (p_accounts IS NULL OR array_length(p_accounts, 1) IS NULL
         OR coalesce(nullif(btrim(c.merchant_account_id), ''), '?') = ANY (p_accounts))
    AND coalesce(btrim(c.activation_type), '') <> ''
    AND coalesce((SELECT sum(abs(coalesce((to_jsonb(c) ->> col)::numeric, 0)))
                  FROM unnest(coalesce(p_pay_cols, ARRAY[]::text[])) AS col), 0) = 0
  ORDER BY c.tx_date NULLS LAST, c.merchant_account_id
  LIMIT greatest(1, coalesce(p_limit, 20000))
$$;

GRANT EXECUTE ON FUNCTION commcalc.ma_overview_unpaid_lines(uuid, text[], text[], text[], int) TO service_role;

-- ── 4) bring the SEEDED tile rows up to the owner's answers ────────────────────────────────────────
-- `updated_by IS NULL` = never touched by a human through the ⚙ Tile mapping editor (which always
-- stamps updated_by). A hand-edited tile is left alone, in every tenant.

-- 4a. COMMISSIONS PAID → the M1 leg, qualified to real activations.
UPDATE commcalc.ma_overview_tile_config
   SET label        = 'Commissions Paid (M1)',
       value_fields = 'spiff_m1',
       sign         = 'negate',
       agg          = 'sum',
       source_table = 'raw_ma_commission',
       filters      = '[{"field":"activation_type","op":"nonblank"},{"field":"activation_type","op":"not_in","value":"Upgrade,Upgrades,Swap,Swaps,SIM Swap,Sim Swap,Device Swap,Exchange,Handset Upgrade,Equipment Upgrade"}]'::jsonb,
       note         = 'OWNER DEFINITION 2026-08-04: the CURRENT month''s commission on this month''s '
                      'activations = the M1 leg (spiff_m1), an MRC-BASED percentage. NOT consumer_margin '
                      '+ device_margin (proven wrong live: consumer_margin is EMPTY in this feed). The '
                      'EXPECTED column beside it is the tenant''s configured M1 rate x qualifying MRC.',
       updated_at   = now()
 WHERE tile_key = 'commissions_paid'
   AND updated_by IS NULL;

-- 4b. ACTIVATION COUNT → new + port + BYOD, excluding swap/upgrade.
UPDATE commcalc.ma_overview_tile_config
   SET filters    = '[{"field":"activation_type","op":"nonblank"},{"field":"activation_type","op":"not_in","value":"Upgrade,Upgrades,Swap,Swaps,SIM Swap,Sim Swap,Device Swap,Exchange,Handset Upgrade,Equipment Upgrade"}]'::jsonb,
       note       = 'OWNER DEFINITION 2026-08-04: new activations + ports + BYOD, EXCLUDING swaps and '
                    'upgrades. Port and BYOD are ATTRIBUTES of a fresh line (port_status / '
                    'activation_type2), not separate activation types, so the rule is an EXCLUSION list '
                    'rather than an include-list that would double-count. VERIFIED LIVE 2026-08-04: the '
                    'real vocabulary here is only New/Add, so this removes zero rows today.',
       updated_at = now()
 WHERE tile_key = 'activation_count'
   AND updated_by IS NULL;

-- 4c. APPEAL COUNT → the follow-up worklist (was: no source mapped).
UPDATE commcalc.ma_overview_tile_config
   SET label        = 'Appeal / follow-up lines',
       source_table = 'raw_ma_commission',
       agg          = 'unpaid_count',
       value_fields = 'spiff_m1,spiff_m2,spiff_m3,spiff_m4,spiff_m5,spiff_m6,rebate,consumer_margin,device_margin,consumer_financing,fees_margin',
       filters      = '[{"field":"activation_type","op":"nonblank"},{"field":"activation_type","op":"not_in","value":"Upgrade,Upgrades,Swap,Swaps,SIM Swap,Sim Swap,Device Swap,Exchange,Handset Upgrade,Equipment Upgrade"}]'::jsonb,
       note         = 'OWNER DEFINITION 2026-08-04: the source report has no appeal column, so this tile '
                      'IS the follow-up worklist — "the lines which don''t get paid and have to be '
                      'followed up like we did in Boost". It counts QUALIFYING ACTIVATION lines on which '
                      'EVERY configured pay column is zero, and drills down to those exact lines. '
                      'Derived and READ-ONLY: it writes nothing and creates no appeal record.',
       updated_at   = now()
 WHERE tile_key = 'appeal_count'
   AND updated_by IS NULL;

-- 4d. RESIDUAL → definition unchanged (owner-CONFIRMED); refresh the note only.
UPDATE commcalc.ma_overview_tile_config
   SET note       = 'OWNER-CONFIRMED 2026-08-04: the portal''s Residual means the SAME thing we compute '
                    '— the What-If / finance residual-per-sub basis (raw_ma_daily_tx rows whose Order '
                    'Type contains "Postpaid Residual Order", summing retail_cost, sign-negated to '
                    'income). No open question here.',
       updated_at = now()
 WHERE tile_key = 'residual'
   AND updated_by IS NULL;

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 268b complete — owner answers applied: Commissions Paid = M1 (MRC-based), Activation '
       'Count excludes swap/upgrade, Appeal Count = the unpaid follow-up worklist, Residual confirmed '
       'unchanged; + the M1-M6 carrier rate plan and the multi-condition filter column. Hand-edited '
       'tiles (updated_by IS NOT NULL) were left untouched. NO PAYOUT PATH READS ANY OF THIS.' AS status;
