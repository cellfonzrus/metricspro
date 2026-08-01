-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- OWNER / OPERATOR SQL — MA PRODUCT CLASS → MONEY (mig 265, branch agent/commission/ma-class-money-wiring)
--
-- READ-ONLY. Every statement below is a SELECT. Nothing here changes a single row; §6 is the only
-- block that writes and it is commented out, deliberately, because the same flip is one dropdown on
-- /commcalc/ma-class-wiring.
--
-- WHY IT EXISTS. This codespace has no Supabase credentials (Supabase SQL is web-only, operator-run),
-- so no number in the park report is a live number. Paste these into the Supabase SQL Editor to get
-- the REAL before/after per bucket per month, per tenant, before anything is flipped.
--
-- SET THE TENANT ONCE. Replace the org below (house/Boost shown; Luxelink/Total has its own org_id —
-- get it from storeops.tenants). Run §0 first if you do not know which org has MA data.
-- ════════════════════════════════════════════════════════════════════════════════════════════════

-- ── §0 · WHICH TENANTS EVEN HAVE MA DAILY TX DATA (run this first) ──────────────────────────────
SELECT org_id, count(*) AS daily_tx_rows, min(period) AS first_period, max(period) AS last_period
FROM commcalc.raw_ma_daily_tx
GROUP BY org_id
ORDER BY 2 DESC;


-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- §1 · THE MONEY GATE — what is CONFIRMED, what is only proposed
-- Only status='confirmed' rows can classify a dollar. Everything else classifies NOTHING, by design.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
WITH org AS (SELECT '00000000-0000-0000-0000-000000000001'::uuid AS id)
SELECT m.status,
       count(*)                                                   AS names,
       count(*) FILTER (WHERE m.note ILIKE '%AMBIGUOUS%')          AS ambiguous_flagged
FROM commcalc.ma_product_class_map m, org
WHERE m.org_id = org.id AND m.source_report = 'ma_daily_tx'
GROUP BY m.status
ORDER BY 1;

-- the AMBIGUOUS judgement calls, by name — anything still 'proposed' classifies nothing
WITH org AS (SELECT '00000000-0000-0000-0000-000000000001'::uuid AS id)
SELECT m.product_name, m.product_class, m.status, m.note
FROM commcalc.ma_product_class_map m, org
WHERE m.org_id = org.id AND m.source_report = 'ma_daily_tx' AND m.note ILIKE '%AMBIGUOUS%'
ORDER BY (m.status <> 'confirmed') DESC, m.product_name;

-- names present in the DATA with no CONFIRMED class — these leave the carrier-income total in class
-- mode, so this list is the work queue on /commcalc/ma-product-class
WITH org AS (SELECT '00000000-0000-0000-0000-000000000001'::uuid AS id)
SELECT btrim(t.product_name)                       AS product_name,
       count(*)                                    AS lines,
       round(sum(coalesce(t.merchant_discount,0)),2) AS merchant_discount,
       round(sum(coalesce(t.retail_cost,0)),2)       AS retail_cost,
       max(m.status)                               AS mapping_status
FROM commcalc.raw_ma_daily_tx t, org
LEFT JOIN commcalc.ma_product_class_map m
       ON m.org_id = t.org_id AND m.source_report = 'ma_daily_tx'
      AND btrim(m.product_name) = btrim(t.product_name) AND m.status = 'confirmed'
WHERE t.org_id = org.id AND m.id IS NULL
GROUP BY 1
ORDER BY abs(sum(coalesce(t.merchant_discount,0))) DESC, 2 DESC;


-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- §2 · CONSUMER 2 (What-If carrier income) — THE BEFORE/AFTER, per month
--
-- OLD: a row feeds RESIDUAL when its order_type contains the configured residual order type
--      (default 'Postpaid Residual Order'); EVERY other row's merchant_discount feeds airtime margin.
-- NEW: a row feeds the leg its CONFIRMED product class is mapped to in commcalc.ma_class_income_leg
--      (default: residual -> residual, billpayment -> airtime, everything else -> excluded).
--
-- NOTE ON SIGN: the residual leg is sign-normalised in the app by whatif_source_config.residual_sign
-- (default 'negate', so a -100 retail_cost shows as +100). This SQL reports the RAW signed sum so it
-- can be reconciled either way; multiply the residual columns by -1 for a 'negate' tenant.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
WITH org AS (SELECT '00000000-0000-0000-0000-000000000001'::uuid AS id),
cfg AS (
  SELECT coalesce(max(w.residual_order_type), 'Postpaid Residual Order') AS rot,
         coalesce(max(w.residual_amount_field), 'retail_cost')           AS ramt
  FROM commcalc.whatif_source_config w, org
  WHERE w.org_id = org.id AND w.carrier_mode = 'plan'
),
rows AS (
  SELECT t.period,
         coalesce(t.merchant_discount, 0)                        AS disc,
         CASE WHEN cfg.ramt = 'merchant_discount' THEN coalesce(t.merchant_discount,0)
              ELSE coalesce(t.retail_cost,0) END                 AS ramt_val,
         (lower(coalesce(t.order_type,'')) LIKE '%' || lower(cfg.rot) || '%') AS legacy_residual,
         coalesce(l.income_leg,
                  CASE m.product_class WHEN 'residual' THEN 'residual'
                                       WHEN 'billpayment' THEN 'airtime'
                                       ELSE 'excluded' END,
                  'unclassified')                                AS class_leg,
         m.product_class
  FROM commcalc.raw_ma_daily_tx t, org, cfg
  LEFT JOIN commcalc.ma_product_class_map m
         ON m.org_id = t.org_id AND m.source_report = 'ma_daily_tx'
        AND btrim(m.product_name) = btrim(t.product_name) AND m.status = 'confirmed'
  LEFT JOIN commcalc.ma_class_income_leg l
         ON l.org_id = t.org_id AND l.product_class = m.product_class
  WHERE t.org_id = org.id
)
SELECT period,
       count(*)                                                                   AS daily_tx_rows,
       round(sum(ramt_val) FILTER (WHERE legacy_residual), 2)                     AS old_residual_raw,
       round(sum(disc)     FILTER (WHERE NOT legacy_residual), 2)                 AS old_airtime,
       round(sum(ramt_val) FILTER (WHERE class_leg = 'residual'), 2)              AS new_residual_raw,
       round(sum(disc)     FILTER (WHERE class_leg = 'airtime'), 2)               AS new_airtime,
       round(sum(disc)     FILTER (WHERE class_leg = 'excluded'), 2)              AS leaves_total_classified,
       round(sum(disc)     FILTER (WHERE class_leg = 'unclassified'), 2)          AS leaves_total_unclassified,
       count(*)            FILTER (WHERE class_leg = 'unclassified')              AS unclassified_lines
FROM rows
GROUP BY period
ORDER BY period;

-- the same thing broken out PER CLASS — this is the "who takes the $30 device sale out of airtime"
-- answer, and it is the table to paste into the Gate-2 note
WITH org AS (SELECT '00000000-0000-0000-0000-000000000001'::uuid AS id)
SELECT coalesce(m.product_class, '(unclassified)')                  AS product_class,
       coalesce(l.income_leg,
                CASE m.product_class WHEN 'residual' THEN 'residual'
                                     WHEN 'billpayment' THEN 'airtime'
                                     ELSE 'excluded' END, 'excluded') AS income_leg,
       count(*)                                                     AS lines,
       round(sum(coalesce(t.merchant_discount,0)), 2)               AS merchant_discount,
       round(sum(coalesce(t.retail_cost,0)), 2)                     AS retail_cost
FROM commcalc.raw_ma_daily_tx t, org
LEFT JOIN commcalc.ma_product_class_map m
       ON m.org_id = t.org_id AND m.source_report = 'ma_daily_tx'
      AND btrim(m.product_name) = btrim(t.product_name) AND m.status = 'confirmed'
LEFT JOIN commcalc.ma_class_income_leg l
       ON l.org_id = t.org_id AND l.product_class = m.product_class
WHERE t.org_id = org.id
GROUP BY 1, 2
ORDER BY abs(sum(coalesce(t.merchant_discount,0))) DESC;


-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- §3 · CONSUMER 1 (Commission Ledger) — where the CONFIRMED classes sit today
--
-- This shows, per canonical bucket per month, how many STORED ledger lines carry each confirmed class.
-- A class spread across two buckets is exactly what a class rule would consolidate; a bucket holding
-- several classes is what a class rule would split.
--
-- The in-app panel (/commcalc/ma-class-wiring → "① Ledger buckets — today vs with the classes") runs
-- the REAL classifier both ways and is the authoritative delta; this query is the SQL cross-check.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
WITH org AS (SELECT '00000000-0000-0000-0000-000000000001'::uuid AS id)
SELECT g.period,
       g.category                                    AS bucket_today,
       coalesce(m.product_class, '(no confirmed class)') AS product_class,
       count(*)                                      AS lines,
       round(sum(coalesce(g.payout_total,0)), 2)     AS payout_total
FROM commcalc.commission_ledger g, org
LEFT JOIN commcalc.ma_product_class_map m
       ON m.org_id = g.org_id AND m.source_report = 'ma_daily_tx'
      AND btrim(m.product_name) = btrim(g.product_name) AND m.status = 'confirmed'
WHERE g.org_id = org.id
GROUP BY 1, 2, 3
ORDER BY g.period, g.category, 5 DESC;

-- the one-line summary: how much payout money would change bucket if each class got its own rule
WITH org AS (SELECT '00000000-0000-0000-0000-000000000001'::uuid AS id)
SELECT coalesce(m.product_class, '(no confirmed class)') AS product_class,
       count(DISTINCT g.category)                        AS buckets_it_is_spread_across,
       string_agg(DISTINCT g.category, ', ')             AS buckets,
       count(*)                                          AS lines,
       round(sum(coalesce(g.payout_total,0)), 2)         AS payout_total
FROM commcalc.commission_ledger g, org
LEFT JOIN commcalc.ma_product_class_map m
       ON m.org_id = g.org_id AND m.source_report = 'ma_daily_tx'
      AND btrim(m.product_name) = btrim(g.product_name) AND m.status = 'confirmed'
WHERE g.org_id = org.id
GROUP BY 1
ORDER BY 5 DESC;


-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- §4 · THE DOUBLE-COUNT CHECK — a row must never be in a ledger income bucket AND the residual leg
--
-- The shipped guard excludes a ledger line whose ORDER TYPE matches the residual order type. The class
-- wiring extends it: while carrier income is class-selected, a line whose CONFIRMED CLASS feeds the
-- residual leg is excluded too. Rows returned by this query are the ones the new half of the guard
-- catches — they should be non-zero only if your data really has residual-class lines under a
-- non-residual order type (that is the whole reason the class selector is more honest).
-- ════════════════════════════════════════════════════════════════════════════════════════════════
WITH org AS (SELECT '00000000-0000-0000-0000-000000000001'::uuid AS id),
cfg AS (SELECT coalesce(max(w.residual_order_type), 'Postpaid Residual Order') AS rot
        FROM commcalc.whatif_source_config w, org WHERE w.org_id = org.id AND w.carrier_mode = 'plan')
SELECT g.period, g.category, g.order_type, btrim(g.product_name) AS product_name,
       count(*) AS lines, round(sum(coalesce(g.payout_total,0)),2) AS payout_total,
       'caught ONLY by the new class half of the guard' AS note
FROM commcalc.commission_ledger g, org, cfg
JOIN commcalc.ma_product_class_map m
     ON m.org_id = g.org_id AND m.source_report = 'ma_daily_tx'
    AND btrim(m.product_name) = btrim(g.product_name) AND m.status = 'confirmed'
LEFT JOIN commcalc.ma_class_income_leg l
     ON l.org_id = g.org_id AND l.product_class = m.product_class
WHERE g.org_id = org.id
  AND coalesce(l.income_leg, CASE m.product_class WHEN 'residual' THEN 'residual' ELSE 'excluded' END) = 'residual'
  AND lower(coalesce(g.order_type,'')) NOT LIKE '%' || lower(cfg.rot) || '%'
  AND coalesce(g.payout_total,0) <> 0
GROUP BY 1,2,3,4
ORDER BY 6 DESC;


-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- §5 · CURRENT WIRING STATE (after mig 265 has been run)
-- ════════════════════════════════════════════════════════════════════════════════════════════════
SELECT org_id, consumer, mode, source_report, updated_by, updated_at
FROM commcalc.ma_class_wiring_config ORDER BY org_id, consumer;

SELECT org_id, product_class, income_leg, updated_at
FROM commcalc.ma_class_income_leg ORDER BY org_id, product_class;

-- the class rules on the ledger's own rule table (match_op='product_class' is the new one)
SELECT org_id, source_report, priority, match_field, match_op, pattern, category, sign_rule
FROM commcalc.commission_category_map
WHERE match_op = 'product_class'
ORDER BY org_id, source_report, priority;


-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- §6 · FLIPPING A SWITCH — DELIBERATELY COMMENTED OUT
--
-- Do this on /commcalc/ma-class-wiring instead: the page shows the delta first, records who flipped it
-- and when, and reverting is the same dropdown. Only run SQL if the UI is unavailable.
--
-- INSERT INTO commcalc.ma_class_wiring_config (org_id, consumer, mode)
-- VALUES ('<ORG_ID>', 'carrier_income', 'class')
-- ON CONFLICT (org_id, consumer) DO UPDATE SET mode = EXCLUDED.mode, updated_at = now();
--
-- REVERT (identical shape, mode back to 'legacy' — this is a complete, instant revert; no recompute,
-- no redeploy, and for the ledger nothing was ever re-written unless a refresh ran while it was on):
-- INSERT INTO commcalc.ma_class_wiring_config (org_id, consumer, mode)
-- VALUES ('<ORG_ID>', 'carrier_income', 'legacy')
-- ON CONFLICT (org_id, consumer) DO UPDATE SET mode = EXCLUDED.mode, updated_at = now();
-- ════════════════════════════════════════════════════════════════════════════════════════════════
