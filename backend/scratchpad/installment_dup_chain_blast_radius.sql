-- installment_dup_chain_blast_radius.sql — READ-ONLY. Quantifies the double-paid multi-month
-- installments before the fix ships (agent/commission/installment-plan-line-only, mig 233).
--
-- WHY THESE SHAPES: the duplicate pair does NOT share a serial. The RATE-PLAN / airtime line carries the
-- MDN with a BLANK serial_1; the DEVICE line carries the IMEI with (usually) a blank MDN. So a
-- `GROUP BY serial_1` puts the two halves of one activation in DIFFERENT groups — the pairs only line up
-- on trans_id. Every block below therefore pairs on (trans_id, month_index, pay_period).
--
-- Replace the org + period at the top of each block. Nothing here writes.
--   luxelink org = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'   ·   period label = 'July 2026'

-- ═══ BLOCK 1 — the duplicate chains themselves (one row per over-paid activation) ═══════════════
-- Expect: one row per activation whose transaction rang both a handset line and a rate-plan line.
-- `mrcs` shows the two MRCs the engine resolved (e.g. {575.00, 65.00}); `paid_now` is what July
-- currently pays for that ONE activation.
SELECT l.trans_id,
       l.month_index,
       l.pay_period,
       COUNT(*)                                              AS chains,
       ROUND(SUM(l.amount), 2)                               AS paid_now,
       ROUND(SUM(l.amount) FILTER (WHERE l.status = 'paid'), 2) AS paid_status_only,
       ARRAY_AGG(DISTINCT l.mrc_at_pay ORDER BY l.mrc_at_pay DESC) AS mrcs,
       ARRAY_AGG(DISTINCT l.mrc_source)                      AS mrc_sources,
       ARRAY_AGG(DISTINCT COALESCE(NULLIF(l.serial_1, ''), '(blank)')) AS serials,
       ARRAY_AGG(DISTINCT COALESCE(NULLIF(l.mdn, ''), '(blank)'))      AS mdns,
       ARRAY_AGG(DISTINCT l.epay_salesperson)                AS reps,
       ARRAY_AGG(DISTINCT l.store)                           AS stores
  FROM commcalc.sale_installment_ledger l
 WHERE l.org_id   = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
   AND l.pay_period = 'July 2026'
 GROUP BY l.trans_id, l.month_index, l.pay_period
HAVING COUNT(*) > 1
 ORDER BY paid_now DESC;

-- ═══ BLOCK 2 — the money: how much of July is inflated, and by whom ═════════════════════════════
-- `overpay_estimate` = every chain in a duplicate group EXCEPT the one with the LOWEST MRC. That
-- matches the fix's outcome in the observed data (the rate-plan MRC is the small one: 65 vs 575) and is
-- an ESTIMATE — the shipped engine picks the rate-plan line by identity, not by size. Compare it against
-- GET /api/v1/commcalc/plan-installments/preview/July%202026 after deploying, which is authoritative.
WITH j AS (
  SELECT *
    FROM commcalc.sale_installment_ledger
   WHERE org_id     = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
     AND pay_period = 'July 2026'
), ranked AS (
  SELECT j.*,
         COUNT(*)   OVER (PARTITION BY trans_id, month_index) AS grp_chains,
         ROW_NUMBER() OVER (PARTITION BY trans_id, month_index
                            ORDER BY mrc_at_pay ASC NULLS FIRST, id) AS keep_rank
    FROM j
)
SELECT COALESCE(epay_salesperson, '(unattributed)')                      AS rep,
       COUNT(*)                                                          AS chains_now,
       COUNT(*) FILTER (WHERE grp_chains > 1)                            AS chains_in_dup_groups,
       ROUND(SUM(amount), 2)                                             AS paid_now,
       ROUND(SUM(amount) FILTER (WHERE grp_chains > 1 AND keep_rank > 1), 2) AS overpay_estimate,
       ROUND(SUM(amount) - COALESCE(SUM(amount) FILTER (WHERE grp_chains > 1 AND keep_rank > 1), 0), 2)
                                                                         AS expected_after_fix
  FROM ranked
 GROUP BY ROLLUP (epay_salesperson)
 ORDER BY overpay_estimate DESC NULLS LAST;

-- ═══ BLOCK 3 — paid $ split by how the MRC was resolved (the prefill exposure) ══════════════════
-- 'prefill' = the $ was scraped out of the product description. That is the path that turned a device
-- PRICE into an "MRC". 'product_catalog' = user-confirmed (safe). 'none' = $0 rows (noise, no money).
-- After the fix, prefill only survives on lines that identify as a rate plan.
SELECT mrc_source,
       payout_kind,
       COUNT(*)                                           AS chains,
       COUNT(*) FILTER (WHERE status = 'paid')            AS paid_chains,
       ROUND(SUM(amount), 2)                              AS paid_now,
       ROUND(MIN(mrc_at_pay), 2)                          AS min_mrc,
       ROUND(MAX(mrc_at_pay), 2)                          AS max_mrc,
       COUNT(*) FILTER (WHERE mrc_at_pay > 200)           AS chains_with_mrc_over_200
  FROM commcalc.sale_installment_ledger
 WHERE org_id     = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
   AND pay_period = 'July 2026'
 GROUP BY mrc_source, payout_kind
 ORDER BY paid_now DESC;

-- ═══ BLOCK 4 — the top inflated rows (the ones that disappear), device by device ════════════════
-- A monthly charge above ~$200 on a prepaid rate plan is almost certainly a handset price. Sanity-check
-- a few of these against the sale in Device History before the recalculation.
WITH j AS (
  SELECT *
    FROM commcalc.sale_installment_ledger
   WHERE org_id     = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
     AND pay_period = 'July 2026'
), ranked AS (
  SELECT j.*,
         COUNT(*) OVER (PARTITION BY trans_id, month_index) AS grp_chains,
         MIN(mrc_at_pay) OVER (PARTITION BY trans_id, month_index) AS sibling_min_mrc
    FROM j
)
SELECT trans_id, month_index, sale_period, epay_salesperson AS rep, store,
       COALESCE(NULLIF(serial_1, ''), '(blank)') AS imei,
       COALESCE(NULLIF(mdn, ''), '(blank)')      AS mdn,
       mrc_at_pay, mrc_source, status, amount,
       sibling_min_mrc                            AS rate_plan_mrc_on_same_sale,
       ROUND(amount - (amount / NULLIF(mrc_at_pay, 0)) * sibling_min_mrc, 2) AS estimated_overpay
  FROM ranked
 WHERE grp_chains > 1
   AND mrc_at_pay > sibling_min_mrc
 ORDER BY amount DESC
 LIMIT 50;

-- ═══ BLOCK 5 — REACH: is any other tenant exposed to the same bug? ══════════════════════════════
-- Any org with an ACTIVE installment schedule is in scope. A schedule whose months are all 'flat' pays a
-- fixed amount, so only the DUPLICATE-CHAIN half of the bug applies to it; a 'pct_mrc' month is exposed
-- to both halves. Run this org-wide (no org filter) as a super-admin.
SELECT s.org_id,
       s.name                                              AS schedule,
       s.trigger_match_field, s.trigger_match_op, s.trigger_match_value,
       s.gate_from_month,
       COUNT(*) FILTER (WHERE il.payout_kind = 'pct_mrc')  AS pct_mrc_months,
       COUNT(*) FILTER (WHERE il.payout_kind = 'flat')     AS flat_months,
       (SELECT COUNT(*) FROM commcalc.sale_installment_ledger g
         WHERE g.org_id = s.org_id AND g.schedule_id = s.id) AS ledger_rows
  FROM commcalc.plan_installment_schedule s
  LEFT JOIN commcalc.plan_installment_line il
         ON il.schedule_id = s.id AND il.org_id = s.org_id
 WHERE s.is_active
 GROUP BY s.org_id, s.id, s.name, s.trigger_match_field, s.trigger_match_op,
          s.trigger_match_value, s.gate_from_month
 ORDER BY s.org_id;

-- ═══ BLOCK 6 — (after the fix ships + Calculate) confirm the duplicates are gone ════════════════
-- Should return ZERO rows. If it does not, send the output back before paying anyone.
SELECT trans_id, month_index, pay_period, COUNT(*) AS chains, ROUND(SUM(amount), 2) AS paid
  FROM commcalc.sale_installment_ledger
 WHERE org_id     = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
   AND pay_period = 'July 2026'
 GROUP BY trans_id, month_index, pay_period
HAVING COUNT(*) > 1;
