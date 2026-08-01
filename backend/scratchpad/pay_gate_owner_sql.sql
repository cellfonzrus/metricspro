-- ══════════════════════════════════════════════════════════════════════════════════════════════════
-- PAY GATE — READ-ONLY OWNER SQL (agent/commission/edge-per-sale-dedup)
-- Paste into the Supabase SQL editor. EVERY statement is a SELECT. Nothing here writes, and nothing
-- here recalculates. Run it BEFORE the package is merged to see the real July dollars.
--
-- Replace :ORG with the tenant's org_id (luxelink) and :PERIOD stays as-is — raw_sales is written in
-- BOTH spellings, so every query below matches 'July 2026' AND '2026-07'.
-- ══════════════════════════════════════════════════════════════════════════════════════════════════

-- ── 0. THE PLAN RULES AS THEY STAND ───────────────────────────────────────────────────────────────
-- Which rules exist, what they key on, and what they pay. `edge` should show match_field='tender_type'
-- and payout_kind='flat_per_unit'; that combination is the defect signature.
SELECT r.id, p.name AS plan, r.label, r.match_field, r.match_op, r.match_value,
       r.payout_kind, r.amount, r.pct, r.qualifies, r.tiered
  FROM commcalc.commission_rule r
  JOIN commcalc.commission_plan p ON p.id = r.plan_id
 WHERE r.org_id = :ORG
 ORDER BY p.name, r.sort;


-- ══ ① EDGE UNIT DEDUP — how many transactions pay more than once, and for how much ════════════════

-- 1a. THE HEADLINE. Per $/unit rule keyed on the tender: transactions that matched more than one line,
--     the number of DISTINCT devices on them, and the dollars that would stop being paid.
--     `extra_amount` is the ORG-WIDE July overpayment this package removes.
WITH r AS (
  SELECT id, label, match_op, lower(match_value) AS mv, amount
    FROM commcalc.commission_rule
   WHERE org_id = :ORG AND match_field = 'tender_type' AND payout_kind = 'flat_per_unit'
), m AS (
  SELECT r.id AS rule_id, r.label, r.amount, s.trans_id, s.salesperson, s.store,
         count(*) AS matched_lines,
         count(DISTINCT nullif(regexp_replace(coalesce(s.serial_1,''), '[^0-9]', '', 'g'), ''))
           FILTER (WHERE length(regexp_replace(coalesce(s.serial_1,''), '[^0-9]', '', 'g'))
                        BETWEEN 14 AND 17) AS devices
    FROM commcalc.raw_sales s
    JOIN r ON (CASE r.match_op WHEN 'contains' THEN lower(coalesce(s.tender_type,'')) LIKE '%'||r.mv||'%'
                               WHEN 'in'       THEN lower(coalesce(s.tender_type,'')) = ANY (
                                                      string_to_array(r.mv, ','))
                               ELSE lower(coalesce(s.tender_type,'')) = r.mv END)
   WHERE s.org_id = :ORG
     AND s.period IN ('July 2026', '2026-07')
     AND coalesce(lower(s.voided::text),'') NOT IN ('true','t','1','yes','y','void','voided')
     AND coalesce(s.trans_type,'') <> 'Return'
   GROUP BY 1,2,3,4,5,6
)
SELECT label AS rule,
       count(*)                                              AS transactions,
       sum(matched_lines)                                    AS matched_lines,
       sum(greatest(devices, 1))                             AS units_after_fix,
       sum(matched_lines - greatest(devices, 1))              AS lines_that_stop_paying,
       round(sum((matched_lines - greatest(devices, 1)) * amount)::numeric, 2) AS extra_amount_removed
  FROM m
 WHERE matched_lines > greatest(devices, 1)
 GROUP BY 1
 ORDER BY 6 DESC;

-- 1b. THE SAME, PER REP — this is the per-rep July delta to show the owner before shipping.
WITH r AS (
  SELECT id, label, match_op, lower(match_value) AS mv, amount
    FROM commcalc.commission_rule
   WHERE org_id = :ORG AND match_field = 'tender_type' AND payout_kind = 'flat_per_unit'
), m AS (
  SELECT r.label, r.amount, s.trans_id, s.salesperson, s.store,
         count(*) AS matched_lines,
         count(DISTINCT nullif(regexp_replace(coalesce(s.serial_1,''), '[^0-9]', '', 'g'), ''))
           FILTER (WHERE length(regexp_replace(coalesce(s.serial_1,''), '[^0-9]', '', 'g'))
                        BETWEEN 14 AND 17) AS devices
    FROM commcalc.raw_sales s
    JOIN r ON (CASE r.match_op WHEN 'contains' THEN lower(coalesce(s.tender_type,'')) LIKE '%'||r.mv||'%'
                               WHEN 'in'       THEN lower(coalesce(s.tender_type,'')) = ANY (
                                                      string_to_array(r.mv, ','))
                               ELSE lower(coalesce(s.tender_type,'')) = r.mv END)
   WHERE s.org_id = :ORG
     AND s.period IN ('July 2026', '2026-07')
     AND coalesce(lower(s.voided::text),'') NOT IN ('true','t','1','yes','y','void','voided')
     AND coalesce(s.trans_type,'') <> 'Return'
   GROUP BY 1,2,3,4,5
)
SELECT salesperson, store, count(*) AS transactions,
       sum(matched_lines - greatest(devices, 1)) AS lines_that_stop_paying,
       round(sum((matched_lines - greatest(devices, 1)) * amount)::numeric, 2) AS delta
  FROM m
 WHERE matched_lines > greatest(devices, 1)
 GROUP BY 1,2
 ORDER BY 5 DESC;

-- 1c. THE OWNER'S OWN TRANSACTION, line by line. Confirms which line carries the IMEI (the one that
--     keeps the payment) and which seven do not.
SELECT trans_id, trans_date, salesperson, product_desc, contract_type, tender_type,
       ext_price, gp, serial_1,
       CASE WHEN length(regexp_replace(coalesce(serial_1,''), '[^0-9]', '', 'g')) BETWEEN 14 AND 17
            THEN 'DEVICE (keeps the payment)' ELSE 'no device serial (stops paying)' END AS after_fix
  FROM commcalc.raw_sales
 WHERE org_id = :ORG AND trans_id = '3207' AND period IN ('July 2026', '2026-07')
 ORDER BY ext_price DESC;

-- 1d. THE DATA-GAP CHECK. Tender-matched transactions where NO line carries a device serial: these pay
--     ONCE (not zero) and raise a `unit_no_device_id` warning. If this count is large, fix the import
--     before shipping — the fallback is a safety net, not a plan.
WITH r AS (
  SELECT lower(match_value) AS mv, match_op
    FROM commcalc.commission_rule
   WHERE org_id = :ORG AND match_field = 'tender_type' AND payout_kind = 'flat_per_unit'
)
SELECT s.trans_id, count(*) AS lines,
       count(*) FILTER (WHERE length(regexp_replace(coalesce(s.serial_1,''),'[^0-9]','','g'))
                              BETWEEN 14 AND 17) AS device_lines
  FROM commcalc.raw_sales s, r
 WHERE s.org_id = :ORG AND s.period IN ('July 2026','2026-07')
   AND lower(coalesce(s.tender_type,'')) LIKE '%'||r.mv||'%'
 GROUP BY 1
HAVING count(*) FILTER (WHERE length(regexp_replace(coalesce(s.serial_1,''),'[^0-9]','','g'))
                              BETWEEN 14 AND 17) = 0
 ORDER BY 2 DESC;


-- ══ ② RTR EXCLUSION — how many lines stop paying, and are any of them NOT bill payments? ══════════

-- 2a. Every July line whose product description carries the WORD 'RTR' (the same word-anchored test
--     the engine uses — note the \m / \M word boundaries, NOT a LIKE '%RTR%').
SELECT trans_id, trans_date, salesperson, store, product_desc, category, department,
       ext_price, gp
  FROM commcalc.raw_sales
 WHERE org_id = :ORG AND period IN ('July 2026','2026-07')
   AND product_desc ~* '\mRTR\M'
 ORDER BY trans_date, trans_id;

-- 2b. THE FALSE-POSITIVE CHECK, and it matters. Anything a naive `%RTR%` would catch that the
--     word-anchored rule correctly does NOT — if this returns rows (CARTRIDGE, PARTRIDGE …), the
--     word operator just saved those payouts.
SELECT product_desc, count(*) AS lines, round(sum(ext_price)::numeric, 2) AS ext_price
  FROM commcalc.raw_sales
 WHERE org_id = :ORG AND period IN ('July 2026','2026-07')
   AND product_desc ILIKE '%RTR%' AND product_desc !~* '\mRTR\M'
 GROUP BY 1 ORDER BY 2 DESC;

-- 2c. Per rep: how much of July's pay is sitting on RTR lines. (Upper bound — the engine only removes
--     what a rule was actually paying on them; use /commission-plans/exclusion-impact for the exact
--     figure, which runs the real engine.)
SELECT salesperson, count(*) AS rtr_lines, round(sum(ext_price)::numeric,2) AS ext_price,
       round(sum(gp)::numeric,2) AS gp
  FROM commcalc.raw_sales
 WHERE org_id = :ORG AND period IN ('July 2026','2026-07') AND product_desc ~* '\mRTR\M'
 GROUP BY 1 ORDER BY 2 DESC;


-- ══ ③ RULE SCOPE — who is collecting the "NY only" rule, and where do they work? ══════════════════

-- 3a. Every store that sold in July, with the market the tenant has mapped it to. THIS IS THE LIST THE
--     OWNER CONFIRMS: which of these are "NY". Nothing is assumed.
SELECT s.store, coalesce(m.market, '(no market mapped)') AS market,
       count(*) AS lines, count(DISTINCT s.salesperson) AS reps,
       min(s.trans_date) AS first_sale, max(s.trans_date) AS last_sale
  FROM commcalc.raw_sales s
  LEFT JOIN commcalc.store_mapping m
         ON m.org_id = s.org_id AND lower(m.store_address) = lower(s.store)
 WHERE s.org_id = :ORG AND s.period IN ('July 2026','2026-07')
 GROUP BY 1,2 ORDER BY 2, 3 DESC;

-- 3b. Reps collecting the $10 activation-style rule, with their store and mapped market. Anyone whose
--     market is NOT the one the owner names is being overpaid by that rule.
--     EDIT the ILIKE pattern to the rule's actual match_value from query 0.
SELECT s.salesperson, s.store, coalesce(m.market,'(unmapped)') AS market,
       count(*) AS matching_lines
  FROM commcalc.raw_sales s
  LEFT JOIN commcalc.store_mapping m
         ON m.org_id = s.org_id AND lower(m.store_address) = lower(s.store)
 WHERE s.org_id = :ORG AND s.period IN ('July 2026','2026-07')
   AND s.product_desc ILIKE '%activation payment%'
 GROUP BY 1,2,3 ORDER BY 3, 4 DESC;


-- ══ ④ UPGRADE PAYS $10 — the CONFIG fix, and the evidence for it ══════════════════════════════════

-- 4a. The lines the product-text rule matches, split by contract_type. Everything in the 'Upgrade'
--     rows is what the re-key removes; everything else keeps paying.
SELECT coalesce(nullif(trim(contract_type),''), '(blank)') AS contract_type,
       count(*) AS lines, count(DISTINCT trans_id) AS transactions,
       count(DISTINCT salesperson) AS reps
  FROM commcalc.raw_sales
 WHERE org_id = :ORG AND period IN ('July 2026','2026-07')
   AND product_desc ILIKE '%activation payment%'
 GROUP BY 1 ORDER BY 2 DESC;

-- 4b. READ THIS BEFORE RE-KEYING. If '(blank)' is a large share, a contract_type rule will MISS those
--     lines and pay nobody for them — use the mig-232 `activation_bucket` match field instead (Plan
--     Installments → Tenant pay settings → contract-type resolution), or map the blanks first.
SELECT round(100.0 * count(*) FILTER (WHERE coalesce(trim(contract_type),'') = '') / nullif(count(*),0), 1)
         AS pct_blank_contract_type,
       count(*) AS july_lines
  FROM commcalc.raw_sales
 WHERE org_id = :ORG AND period IN ('July 2026','2026-07');


-- ══ ⑤ ACCESSORY BASIS GUARD — how many accessory lines have an unusable GP ════════════════════════

-- 5a. The accessory lines a %-of-GP rule pays on, bucketed by the cost-integrity condition. `cost` is
--     IMPLIED: raw_sales has no cost column, so cost = ext_price - gp.
SELECT CASE WHEN gp > ext_price + 0.005            THEN 'cost_negative (payout inflated)'
            WHEN gp < -0.005                        THEN 'gp_negative (payout NEGATIVE today)'
            WHEN abs(gp) <= 0.005 AND ext_price > 0 THEN 'cost_equals_price (payout $0 today)'
            WHEN abs(gp - ext_price) <= 0.005 AND ext_price > 0 THEN 'cost_zero'
            ELSE 'believable' END AS condition,
       count(*) AS lines,
       round(sum(ext_price)::numeric, 2) AS ext_price,
       round(sum(gp)::numeric, 2)        AS gp
  FROM commcalc.raw_sales
 WHERE org_id = :ORG AND period IN ('July 2026','2026-07')
   AND (category ILIKE '%accessor%' OR department ILIKE '%accessor%')
 GROUP BY 1 ORDER BY 2 DESC;

-- 5b. Per rep, what the guard would add — at the tenant's OWN rate. EDIT 0.175 to the rule's real pct
--     from query 0. This is an ESTIMATE on the full selling price; the exact figure comes from
--     /commission-plans/accessory-basis-impact, which runs the real engine.
SELECT salesperson,
       count(*) AS unusable_gp_lines,
       round(sum(0.175 * greatest(gp, 0))::numeric, 2)  AS paid_today,
       round(sum(0.175 * ext_price)::numeric, 2)        AS would_pay_on_price,
       round(sum(0.175 * (ext_price - greatest(gp, 0)))::numeric, 2) AS increase
  FROM commcalc.raw_sales
 WHERE org_id = :ORG AND period IN ('July 2026','2026-07')
   AND (category ILIKE '%accessor%' OR department ILIKE '%accessor%')
   AND (abs(gp) <= 0.005 OR gp < -0.005)
 GROUP BY 1 ORDER BY 5 DESC;

-- 5c. THE RATE SANITY CHECK (the 17.5-vs-0.175 class). `pct` is a FRACTION: anything > 1 pays more
--     than 100% of the basis. This should return ZERO rows.
SELECT id, label, payout_kind, pct
  FROM commcalc.commission_rule
 WHERE org_id = :ORG AND payout_kind IN ('pct_gp','pct_mrc','pct_price_over_cost') AND pct > 1;
