-- multimonth_category_blast_radius.sql — READ-ONLY. Paste into the Supabase SQL Editor.
--
-- WHY: this codespace has no database credentials, so the per-rep July numbers in the Gate-1 note are
-- derived from the engine + the owner's pasted rows, not from production. These four queries produce the
-- REAL per-org / per-rep blast radius from what is already stored, BEFORE anything is recalculated.
-- (The same numbers are available live, per tenant, at
--  GET /api/v1/commcalc/plan-installments/category-impact/July%202026?org_id=<org> — three in-memory
--  engine runs, writes nothing. Prefer that; this SQL is the no-deploy fallback.)
--
-- Period spelling matters: raw_sales stores 'July 2026' while some callers pass '2026-07'. Every query
-- below matches BOTH (the _pvariants rule).
-- The classification mirrors installment_category.DEFAULT_CATEGORY_RULES in priority order
-- (home internet → tablet → phone → SIM → structural IMEI). It is an approximation of the engine for
-- eyeballing; the endpoint above is authoritative.

-- ── 1. WHAT IS AT STAKE, per org / rep / category (July 2026, month 1..6) ────────────────────────
WITH lines AS (
  SELECT l.org_id, l.epay_salesperson AS rep, l.trans_id, l.mdn, l.serial_1, l.month_index,
         l.mrc_at_pay, l.amount, l.status,
         COALESCE(string_agg(DISTINCT s.product_desc, ' | '), '') AS products,
         COALESCE(string_agg(DISTINCT s.department,  ' | '), '') AS departments,
         COALESCE(string_agg(DISTINCT s.category,    ' | '), '') AS categories,
         MAX(CASE WHEN s.serial_1 IS NOT NULL AND s.serial_1 <> '' THEN s.ext_price END) AS device_ext_price
    FROM commcalc.sale_installment_ledger l
    LEFT JOIN commcalc.raw_sales s
           ON s.org_id = l.org_id AND s.trans_id = l.trans_id
          AND s.period IN ('July 2026', '2026-07')
   WHERE l.pay_period IN ('July 2026', '2026-07')
   GROUP BY 1,2,3,4,5,6,7,8,9
), classified AS (
  SELECT *,
    CASE
      WHEN products ILIKE '%home internet%' OR products ILIKE '%internet gateway%' THEN 'home_internet'
      WHEN products ~* '(^|[^a-z0-9])(tablet|tab|ipad)([^a-z0-9]|$)'
        OR departments ILIKE '%tablet%' OR categories ILIKE '%tablet%'                THEN 'tablet'
      WHEN categories ILIKE '%kittedbranded%' OR departments ILIKE '%brandedhandset%'
        OR departments ILIKE '%handset%' OR categories ILIKE '%handset%'
        OR products ~* '(^|[^a-z0-9])phone([^a-z0-9]|$)'                              THEN 'phone'
      WHEN products ILIKE '%sim kit%' OR products ILIKE '%sim card%'
        OR categories ILIKE '%simmarketplace%'
        OR length(regexp_replace(COALESCE(serial_1,''), '[^0-9]', '', 'g')) BETWEEN 18 AND 22 THEN 'sim'
      WHEN length(regexp_replace(COALESCE(serial_1,''), '[^0-9]', '', 'g')) BETWEEN 14 AND 17 THEN 'phone'
      ELSE 'unknown'
    END AS device_category
    FROM lines
)
SELECT org_id, device_category, rep,
       count(*)                                   AS installments,
       round(sum(amount)::numeric, 2)             AS paid_now,
       round(sum(amount) FILTER (WHERE device_category IN ('tablet','sim'))::numeric, 2)
                                                  AS at_risk_under_the_new_defaults
  FROM classified
 GROUP BY ROLLUP (org_id, device_category, rep)
 ORDER BY org_id NULLS LAST, device_category NULLS LAST, at_risk_under_the_new_defaults DESC NULLS LAST;

-- ── 2. THE TABLET MRC BUG: installments whose monthly charge IS a device price ───────────────────
--    (mrc_at_pay equals the Ext Price of a line on the same transaction that carries a device serial)
SELECT l.org_id, l.epay_salesperson AS rep, l.trans_id, l.serial_1, l.month_index,
       l.mrc_at_pay, l.amount,
       s.product_desc AS device_line, s.ext_price,
       (SELECT p.product_desc FROM commcalc.raw_sales p
         WHERE p.org_id = l.org_id AND p.trans_id = l.trans_id
           AND p.period IN ('July 2026','2026-07')
           AND (p.serial_1 IS NULL OR p.serial_1 = '')
           AND p.product_desc ~* '(^|[^a-z0-9])(plan|unlimited|airtime)([^a-z0-9]|$)'
         ORDER BY p.ext_price DESC LIMIT 1)                       AS rate_plan_line_it_should_have_used
  FROM commcalc.sale_installment_ledger l
  JOIN commcalc.raw_sales s
    ON s.org_id = l.org_id AND s.trans_id = l.trans_id
   AND s.period IN ('July 2026','2026-07')
   AND s.serial_1 IS NOT NULL AND s.serial_1 <> ''
   AND abs(COALESCE(s.ext_price,0) - COALESCE(l.mrc_at_pay,0)) < 0.01
 WHERE l.pay_period IN ('July 2026','2026-07')
   AND COALESCE(l.mrc_at_pay,0) > 0
 ORDER BY l.amount DESC;

-- ── 3. THE DOUBLE ROW: one device, one month, two installments ──────────────────────────────────
SELECT org_id, serial_1, month_index,
       count(*)                       AS installments,
       count(DISTINCT schedule_id)    AS distinct_schedules,   -- >1 ⇒ a duplicate ACTIVE schedule
       count(DISTINCT trans_id)       AS distinct_transactions,-- >1 ⇒ sold twice (return + re-sale)
       count(DISTINCT mdn)            AS distinct_mdns,        -- >1 ⇒ two subscribers, one borrowed IMEI
       round(sum(amount)::numeric, 2) AS paid,
       string_agg(DISTINCT epay_salesperson, ', ') AS reps
  FROM commcalc.sale_installment_ledger
 WHERE pay_period IN ('July 2026','2026-07')
   AND serial_1 IS NOT NULL AND serial_1 <> ''
 GROUP BY 1,2,3
HAVING count(*) > 1
 ORDER BY paid DESC;

-- ── 4. Sanity: how many ACTIVE multi-month schedules does each tenant have? ─────────────────────
--    (two active schedules on the SAME plan is the most likely cause of query 3's duplicates)
SELECT s.org_id, p.name AS plan, s.id AS schedule_id, s.name AS schedule, s.num_months,
       s.trigger_match_field, s.trigger_match_op, s.trigger_match_value, s.is_active
  FROM commcalc.plan_installment_schedule s
  LEFT JOIN commcalc.commission_plan p ON p.id = s.plan_id
 WHERE s.is_active
 ORDER BY s.org_id, p.name;
