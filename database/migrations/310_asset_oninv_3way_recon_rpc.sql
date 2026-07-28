-- 310_asset_oninv_3way_recon_rpc.sql — mod-asset · On-Inventory 3-Way Rebate Recon
-- (OWNER DIRECTIVE 2026-07-28: "the on inventory per store report seems to be off than the actual
-- inventory in each store, the imei which show up in the on inventory must be check with the imei
-- rebate report under asset landing and the commission report where it shows the rebate got paid,
-- a 3 way recon needs to be done to find the missing phones and the non activated phones")
--
-- THE THREE LEGS (see backend/app/modules/asset/oninv_recon.py module docstring for the full
-- citation trail — this header gives the short version):
--   1. ON-INVENTORY — commcalc.asset_ledger, the EXACT predicate already used by
--      GET /asset/on-inventory-by-store and GET /asset/aging (router.py): unsold
--      (date_sold IS NULL) AND category ILIKE '%On Inventory%', org-scoped. This RPC reuses that
--      identical predicate so the recon reconciles the SAME set the owner already sees on those
--      two pages, not a rederived one.
--   2. THE IMEI REBATE REPORT ("under asset landing") — turned out to be data already sitting on
--      the SAME on-inventory row: asset_ledger.reimbursement / reimbursement_date (VIP's own
--      per-device "Reimbursement" / "Reimbursement Date" columns, parsed by asset_parser.py). This
--      is exactly what GET /asset/aging-rebate ("💵 Aging — Rebate Received", the closest existing
--      page to "the IMEI rebate report") already treats as leg 2: `reimbursement > 0` on an
--      on-inventory row means VIP recorded a rebate for a device the ledger STILL shows as unsold.
--      Because asset_ledger is a full wipe-and-replace snapshot (one row per device as of the last
--      upload — asset-2/mig 300), there is no separate "leg 2 table" to join; it is the row itself.
--   3. THE COMMISSION REPORT WHERE THE REBATE GOT PAID — commcalc.raw_payment_detail (ePay Payment
--      Detail Report), joined by IMEI. This reuses the SAME join shape as router.py's
--      `_epay_payments_map` (already used by the Appeals charge-group report to show "what Boost
--      actually paid" per device) — ANY payment row for that IMEI is leg-3 evidence, returned with
--      its type/date/amount so a reader can see exactly what it was, not a pre-filtered subset that
--      could silently diverge from what `_epay_payments_map` shows elsewhere.
--
-- MATCHING: normalize both sides identically — upper, trimmed, trailing ".0" stripped (mirrors
-- router.py's `_norm_imei` exactly; no fuzzy/invented matching). Normalizing BOTH sides is
-- equivalent to `_epay_payments_map`'s raw+normalized+normalized-with-.0 candidate-set approach
-- (same collisions caught) and is simpler to express as one join predicate.
--
-- CLASSIFICATION (decision tree — see oninv_recon.py for the full worked rationale):
--   1. esn_imei blank/null on the on-inventory row        -> 'unmatchable'  (can't check leg 3 at
--      all, and leg 2 alone can't be corroborated; NEVER silently dropped)
--   2. leg2_paid AND leg3_paid                             -> 'missing_phone_candidate' (both legs
--      agree the device was reimbursed/paid for — inventory record is wrong or the phone left)
--   3. leg2_paid AND NOT leg3_paid (ePay HAS data for this org but zero rows for this IMEI)
--                                                           -> 'conflict' (ledger says reimbursed,
--      commission side shows nothing for this device — state which leg says what)
--   4. NOT leg2_paid AND leg3_paid                         -> 'conflict' (commission side shows a
--      payment, but the distributor ledger's own reimbursement field is blank/zero)
--   5. leg2_paid AND leg3 = 'na' (raw_payment_detail has ZERO rows for the WHOLE org — ePay simply
--      not loaded, not "checked and negative")             -> 'missing_phone_candidate' (leg 3
--      wasn't checkable at all, so this is single-leg evidence, not a disagreement)
--   6. NOT leg2_paid AND leg3 = 'na'                        -> 'non_activated' (no evidence in the
--      only leg we could check; leg3 explicitly marked 'na', never silently 'not_paid')
--   7. NOT leg2_paid AND NOT leg3_paid                      -> 'non_activated' (true stock — no
--      rebate evidence anywhere)
--
-- DEVICE $ VALUE: owed_to_vip — the SAME column every other asset report (Charges Dashboard, RMA,
-- Owed-Weekly, On-Inventory-by-Store, and the 2026-07-28 Aging footer-totals addition) already
-- treats as the device's $ exposure. Not selling_price/reimbursement/commissions — those don't
-- apply to an unsold on-inventory device the way owed_to_vip does.
--
-- PERF: the entire 3-way join happens HERE, in Postgres, in one query — never a fetch-all-into-
-- Python join across asset_ledger (44k rows) x raw_payment_detail. The Python endpoint only
-- aggregates this function's OUTPUT (bounded to the on-inventory subset, the same bound
-- GET /asset/on-inventory-by-store already accepts) into per-store summary counts/dollars.
--
-- MULTI-TENANT: p_org_id is required and scopes every table read; no default, no house-org
-- constant. SAFE: purely additive (CREATE OR REPLACE), idempotent, read-only (never writes to any
-- ledger/table). Degrades gracefully — the endpoint treats a missing-function error (mig not run)
-- as "not yet available" rather than a 500 (see oninv_recon.py `_is_missing_schema_error`).

CREATE OR REPLACE FUNCTION commcalc.asset_oninv_3way_recon(
  p_org_id uuid,
  p_stores text[] DEFAULT NULL,     -- NULL or empty = all stores; otherwise exact-match allow-list
  p_market text DEFAULT NULL,       -- NULL = no market filter; ignored when p_no_market_only
  p_no_market_only boolean DEFAULT false,   -- true = select ONLY rows with no/blank market
  p_date_from date DEFAULT NULL,    -- acquired_date >= (inclusive)
  p_date_to date DEFAULT NULL       -- acquired_date <= (inclusive)
)
RETURNS TABLE (
  store text,
  market text,
  esn_imei text,
  device_model text,
  acquired_date date,
  aging_days integer,
  device_value numeric,
  leg2_paid boolean,
  leg2_amount numeric,
  leg2_date date,
  leg3_status text,          -- 'paid' | 'not_paid' | 'na'
  leg3_amount numeric,
  leg3_last_date date,
  leg3_payment_count integer,
  leg3_payment_types text,
  classification text        -- 'missing_phone_candidate' | 'non_activated' | 'conflict' | 'unmatchable'
)
LANGUAGE sql
STABLE
AS $$
  WITH epay_source AS (
    -- Whether raw_payment_detail has ANY data for this org at all — distinguishes "leg 3 checked
    -- and found nothing for this device" (leg3_status='not_paid') from "leg 3 not loaded, can't
    -- check anything" (leg3_status='na'). See classification rule 5/6 above.
    SELECT EXISTS (
      SELECT 1 FROM commcalc.raw_payment_detail WHERE org_id = p_org_id
    ) AS loaded
  ),
  oninv AS (
    -- Leg 1: the EXACT predicate GET /asset/on-inventory-by-store and GET /asset/aging already use.
    SELECT
      al.store,
      al.market,
      al.esn_imei,
      al.device_model,
      al.acquired_date,
      al.owed_to_vip,
      al.reimbursement,
      al.reimbursement_date,
      upper(regexp_replace(btrim(al.esn_imei), '\.0$', '')) AS norm_imei
    FROM commcalc.asset_ledger al
    WHERE al.org_id = p_org_id
      AND al.date_sold IS NULL
      AND al.category ILIKE '%On Inventory%'
      AND (p_stores IS NULL OR cardinality(p_stores) = 0 OR al.store = ANY(p_stores))
      AND (
        (p_no_market_only AND (al.market IS NULL OR btrim(al.market) = ''))
        OR (NOT p_no_market_only AND (p_market IS NULL OR al.market = p_market))
      )
      AND (p_date_from IS NULL OR al.acquired_date >= p_date_from)
      AND (p_date_to IS NULL OR al.acquired_date <= p_date_to)
  ),
  leg3 AS (
    -- Leg 3: ePay Payment Detail Report, aggregated per normalized IMEI, org-scoped. Only ever
    -- touches raw_payment_detail rows that could possibly match an on-inventory IMEI (bounded via
    -- the join below, not a full-table Python pull).
    SELECT
      upper(regexp_replace(btrim(pd.imei), '\.0$', '')) AS norm_imei,
      COUNT(*)                                            AS payment_count,
      SUM(pd.amount)                                       AS total_amount,
      MAX(pd.payment_date)                                  AS last_date,
      string_agg(DISTINCT NULLIF(btrim(pd.payment_type), ''), ', ' ORDER BY NULLIF(btrim(pd.payment_type), '')) AS payment_types
    FROM commcalc.raw_payment_detail pd
    WHERE pd.org_id = p_org_id
      AND pd.imei IS NOT NULL AND btrim(pd.imei) <> ''
    GROUP BY 1
  )
  SELECT
    o.store,
    o.market,
    o.esn_imei,
    o.device_model,
    o.acquired_date,
    CASE WHEN o.acquired_date IS NULL THEN NULL
         ELSE (CURRENT_DATE - o.acquired_date) END          AS aging_days,
    COALESCE(o.owed_to_vip, 0)                               AS device_value,
    (COALESCE(o.reimbursement, 0) > 0)                       AS leg2_paid,
    o.reimbursement                                          AS leg2_amount,
    o.reimbursement_date                                     AS leg2_date,
    CASE
      WHEN o.norm_imei = '' OR o.norm_imei IS NULL THEN 'na'  -- unmatchable row; leg3_status moot
      WHEN l.norm_imei IS NOT NULL THEN 'paid'
      WHEN (SELECT loaded FROM epay_source) THEN 'not_paid'
      ELSE 'na'
    END                                                       AS leg3_status,
    l.total_amount                                            AS leg3_amount,
    l.last_date                                               AS leg3_last_date,
    COALESCE(l.payment_count, 0)                              AS leg3_payment_count,
    l.payment_types                                           AS leg3_payment_types,
    CASE
      WHEN o.norm_imei = '' OR o.norm_imei IS NULL THEN 'unmatchable'
      WHEN COALESCE(o.reimbursement, 0) > 0 AND l.norm_imei IS NOT NULL THEN 'missing_phone_candidate'
      WHEN COALESCE(o.reimbursement, 0) > 0 AND l.norm_imei IS NULL AND (SELECT loaded FROM epay_source) THEN 'conflict'
      WHEN COALESCE(o.reimbursement, 0) <= 0 AND l.norm_imei IS NOT NULL THEN 'conflict'
      WHEN COALESCE(o.reimbursement, 0) > 0 AND l.norm_imei IS NULL AND NOT (SELECT loaded FROM epay_source) THEN 'missing_phone_candidate'
      ELSE 'non_activated'
    END                                                       AS classification
  FROM oninv o
  LEFT JOIN leg3 l ON l.norm_imei = o.norm_imei AND o.norm_imei <> ''
  ORDER BY o.store, COALESCE(o.owed_to_vip, 0) DESC;
$$;

GRANT EXECUTE ON FUNCTION commcalc.asset_oninv_3way_recon(uuid, text[], text, boolean, date, date)
  TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT '310 complete — commcalc.asset_oninv_3way_recon(uuid,...) — on-inventory x rebate x commission 3-way recon' AS status;
