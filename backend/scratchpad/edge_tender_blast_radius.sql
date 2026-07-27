-- edge_tender_blast_radius.sql — READ-ONLY. Paste into the Supabase SQL editor and send back the output
-- of every block. Nothing here writes, and nothing triggers a calculation.
--
-- CONTEXT (owner ruling 2026-07-27): the pay bucket "edge" means the device-FINANCING program and must
-- key on the sale's TENDER METHOD. It was matching the WORD "edge" in the item description, which catches
-- "Motorola Edge 2025" — a phone MODEL. Those sales belong to the multi-month incentive instead.
--
-- BLOCKS 1-4 = EVIDENCE (what the matcher actually is, and what identifies TW financing in the data).
-- BLOCKS 5-8 = BLAST RADIUS (who is paid what today, and what changes).
-- BLOCK  9   = the PROPOSED config change, commented out. Do NOT run it until Gate-2.
--
-- The two orgs in play:
--   house/Boost  00000000-0000-0000-0000-000000000001   (Total Wireless is a CARRIER inside this org)
--   luxelink     854f6d7b-6590-4e4d-88ab-646f560d4f4c
-- Period spelling is a recurring bug class here: raw_sales stores 'July 2026', other places '2026-07'.
-- Every block below matches BOTH spellings on purpose.


-- ══ 1. THE MATCHER — which rule is named "edge", and what does it actually match on? ═════════════
-- `label` is free text and DISPLAY ONLY: the drill-down's "Rule" column shows it, but what pays is
-- match_field/match_op/match_value. Read them, don't assume.
SELECT p.org_id,
       CASE p.org_id::text
         WHEN '00000000-0000-0000-0000-000000000001' THEN 'house/Boost'
         WHEN '854f6d7b-6590-4e4d-88ab-646f560d4f4c' THEN 'luxelink' ELSE 'other' END AS tenant,
       p.name  AS plan, p.is_active,
       r.id    AS rule_id, r.sort, r.label,
       r.match_field, r.match_op, r.match_value,
       r.qualifies, r.payout_kind, r.amount, r.pct, r.tiered
FROM commcalc.commission_rule r
JOIN commcalc.commission_plan p ON p.id = r.plan_id AND p.org_id = r.org_id
WHERE p.org_id IN ('00000000-0000-0000-0000-000000000001',
                   '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND (lower(coalesce(r.label,'')) LIKE '%edge%' OR lower(coalesce(r.match_value,'')) LIKE '%edge%')
ORDER BY tenant, p.name, r.sort;

-- 1b. The same question for MULTI-MONTH triggers (a schedule matcher is the same shape).
SELECT s.org_id, p.name AS plan, s.id AS schedule_id, s.name AS schedule, s.is_active, s.num_months,
       s.trigger_match_field, s.trigger_match_op, s.trigger_match_value,
       s.gate_mode, s.gate_from_month, s.m1_gate, s.effective_from, s.effective_to
FROM commcalc.plan_installment_schedule s
JOIN commcalc.commission_plan p ON p.id = s.plan_id AND p.org_id = s.org_id
WHERE s.org_id IN ('00000000-0000-0000-0000-000000000001',
                   '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
ORDER BY s.org_id, p.name, s.name;

-- 1c. The mig-078 payout_schedule rows also carry an activation_type 'edge'. They are a DIFFERENT
-- system (raw_mi-driven, house org, Total Wireless carrier) and the engine only resolves '*', so they
-- are almost certainly NOT what produced the owner's $25/unit rows. Confirm they are inert.
SELECT ps.org_id, c.name AS carrier, ps.activation_type, ps.num_months, ps.gate_signal, ps.is_active,
       count(l.id) AS lines
FROM commcalc.payout_schedule ps
LEFT JOIN commcalc.carrier c ON c.id = ps.carrier_id
LEFT JOIN commcalc.payout_schedule_line l ON l.schedule_id = ps.id
WHERE ps.org_id IN ('00000000-0000-0000-0000-000000000001',
                    '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
GROUP BY 1,2,3,4,5,6 ORDER BY 1,2,3;


-- ══ 2. THE TENDER SIGNAL — what identifies TW financing in THIS tenant's own data? ═══════════════
-- raw_sales.tender_type is mapped straight from the export's `Tender Type` column and is stamped on
-- every line of the transaction (same shape as contract_type). It already carries lease/financing
-- program names elsewhere ("ACIMA", "ACIMA Lease", "Acima Leasing" — see calculator.py's acima spiff).
-- DO NOT GUESS THE STRING. Read it here and pick it from the rule editor's dropdown.
SELECT org_id,
       coalesce(nullif(trim(tender_type),''), '(blank)') AS tender_type,
       count(*)                                          AS lines,
       count(DISTINCT trans_id)                          AS transactions,
       round(sum(coalesce(ext_price,0))::numeric, 2)     AS ext_price
FROM commcalc.raw_sales
WHERE org_id IN ('00000000-0000-0000-0000-000000000001',
                 '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND period IN ('July 2026','2026-07')
GROUP BY 1,2 ORDER BY 1, lines DESC;

-- 2b. Same, from the DAILY FEED — the open month reads the feed first, so the two can disagree.
SELECT org_id, coalesce(nullif(trim(tender_type),''),'(blank)') AS tender_type,
       count(*) AS lines, count(DISTINCT trans_id) AS transactions
FROM commcalc.daily_sales_feed
WHERE org_id IN ('00000000-0000-0000-0000-000000000001',
                 '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND period IN ('July 2026','2026-07')
GROUP BY 1,2 ORDER BY 1, lines DESC;

-- 2c. THE DECIDING QUESTION — the owner's five lines. What tender do THEY carry, and does a
-- financing-looking tender exist at all on the transactions we believe are Edge-financed?
-- If tender_type is BLANK on these rows, a tender-keyed rule pays $0 and the fix needs a different
-- signal (see 2d) — say so rather than shipping a rule that silently matches nothing.
SELECT org_id, trans_id, trans_date, salesperson, store, contract_type,
       coalesce(nullif(trim(tender_type),''),'(blank)') AS tender_type,
       department, category, product_desc, sku, ext_price, gp, mdn, serial_1, trans_type, voided
FROM commcalc.raw_sales
WHERE org_id IN ('00000000-0000-0000-0000-000000000001',
                 '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND period IN ('July 2026','2026-07')
  AND trans_id IN ('4045','3411','4130','3451')
ORDER BY org_id, trans_id, product_desc;

-- 2d. FALLBACK SIGNALS, in case tender_type is blank on financed sales. Look for a financing SKU /
-- department / category / promo marker that co-occurs with the same transactions. Pick from what is
-- REALLY there — do not invent a mapping.
SELECT org_id, department, category,
       count(*) AS lines, count(DISTINCT trans_id) AS transactions,
       min(product_desc) AS example_item
FROM commcalc.raw_sales
WHERE org_id IN ('00000000-0000-0000-0000-000000000001',
                 '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND period IN ('July 2026','2026-07')
  AND (lower(coalesce(department,'')) ~ 'financ|lease|edge|installment'
    OR lower(coalesce(category,''))   ~ 'financ|lease|edge|installment'
    OR lower(coalesce(product_desc,''))~ 'financ|lease|installment')
GROUP BY 1,2,3 ORDER BY 1, lines DESC;

-- 2e. Cross-check against the closing/POS tender tables (a transaction-level tender source, if the
-- line-level column turns out to be unusable). Empty result = this path is not available either.
SELECT org_id, close_date, store, tender_type, tender_class,
       count(*) AS row_count, round(sum(coalesce(amount,0))::numeric,2) AS amount
FROM commcalc.pos_tender_summary
WHERE org_id IN ('00000000-0000-0000-0000-000000000001',
                 '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND close_date >= date '2026-07-01' AND close_date < date '2026-08-01'
GROUP BY 1,2,3,4,5 ORDER BY 1,2,3 LIMIT 200;


-- ══ 3. WHAT THE "edge" PATTERN ACTUALLY HITS (the model-name collision, in data) ═════════════════
-- Every July line whose ITEM DESCRIPTION contains 'edge', with its tender. If these are all handsets,
-- the rule is matching a MODEL name.
SELECT org_id, product_desc,
       coalesce(nullif(trim(tender_type),''),'(blank)') AS tender_type,
       count(*) AS lines, count(DISTINCT trans_id) AS transactions,
       count(DISTINCT salesperson) AS reps,
       round(sum(coalesce(ext_price,0))::numeric,2) AS ext_price
FROM commcalc.raw_sales
WHERE org_id IN ('00000000-0000-0000-0000-000000000001',
                 '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND period IN ('July 2026','2026-07')
  AND lower(coalesce(product_desc,'')) LIKE '%edge%'
GROUP BY 1,2,3 ORDER BY 1, lines DESC;


-- ══ 4. THE REPS AFFECTED — per-rep exposure of the "edge" rule TODAY ═════════════════════════════
-- This is the BEFORE column of the blast radius. Replace :RULE_AMOUNT with the rule's `amount` from
-- block 1 (it is a flat $/unit rule, so pay = matched lines × amount, before any tier multiplier).
WITH edge_rule AS (
  SELECT r.org_id, r.plan_id, r.id AS rule_id, r.label, r.match_field, r.match_op,
         lower(coalesce(r.match_value,'')) AS pat, r.amount, r.tiered
  FROM commcalc.commission_rule r
  JOIN commcalc.commission_plan p ON p.id = r.plan_id AND p.org_id = r.org_id
  WHERE r.org_id IN ('00000000-0000-0000-0000-000000000001',
                     '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
    AND lower(coalesce(r.label,'')) LIKE '%edge%'
    AND r.match_field IN ('product_desc','sku') AND r.match_op = 'contains'
)
SELECT s.org_id, e.label AS rule, s.salesperson, s.store,
       count(*)                                   AS matched_lines,
       count(DISTINCT s.trans_id)                 AS matched_transactions,
       round((count(*) * e.amount)::numeric, 2)   AS pays_today,
       count(*) FILTER (WHERE coalesce(trim(s.tender_type),'') <> '') AS lines_with_a_tender
FROM commcalc.raw_sales s
JOIN edge_rule e ON e.org_id = s.org_id
WHERE s.period IN ('July 2026','2026-07')
  AND coalesce(upper(trim(s.voided)),'') NOT IN ('YES','TRUE','1','Y','VOID','VOIDED')
  AND coalesce(trim(s.trans_type),'') <> 'Return'
  AND position(e.pat in lower(coalesce(
        CASE WHEN e.match_field='sku' THEN s.sku ELSE s.product_desc END, ''))) > 0
GROUP BY 1,2,3,4, e.amount
ORDER BY 1, pays_today DESC;


-- ══ 5. WOULD MULTI-MONTH CATCH THEM? ════════════════════════════════════════════════════════════
-- CRITICAL: plan rules have NO exclusivity and the multi-month engine is a SEPARATE, ADDITIVE
-- component with its OWN trigger (rep pay = plan_comm + residual_installment_comm + installment_comm_sale).
-- So a line that stops matching the "edge" rule is only re-paid if a schedule trigger matches it.
-- 5a. Do any of these transactions ALREADY have an installment chain? (If yes they are being paid
--     TWICE today — $25 edge AND the multi-month M1.)
SELECT l.org_id, l.sale_period, l.pay_period, l.trans_id, l.mdn, l.serial_1, l.month_index,
       l.status, l.amount, l.paid_gate_met, l.gate_mode, l.mrc_at_pay, l.mrc_source,
       l.epay_salesperson
FROM commcalc.sale_installment_ledger l
WHERE l.org_id IN ('00000000-0000-0000-0000-000000000001',
                   '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND l.pay_period IN ('July 2026','2026-07')
  AND l.trans_id IN ('4045','3411','4130','3451')
ORDER BY 1,4,7;

-- 5b. …and more broadly: how many of the 'edge'-matched July transactions have a chain at all?
SELECT s.org_id,
       count(DISTINCT s.trans_id)                                            AS edge_matched_tx,
       count(DISTINCT l.trans_id)                                            AS tx_with_a_chain,
       count(DISTINCT s.trans_id) - count(DISTINCT l.trans_id)               AS tx_with_NO_chain
FROM commcalc.raw_sales s
LEFT JOIN commcalc.sale_installment_ledger l
       ON l.org_id = s.org_id AND l.trans_id = s.trans_id
      AND l.pay_period IN ('July 2026','2026-07')
WHERE s.org_id IN ('00000000-0000-0000-0000-000000000001',
                   '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND s.period IN ('July 2026','2026-07')
  AND lower(coalesce(s.product_desc,'')) LIKE '%edge%'
GROUP BY 1;


-- ══ 6. WHAT A TENDER-KEYED RULE WOULD PAY INSTEAD (the AFTER column) ════════════════════════════
-- Replace 'TW Financing' with the REAL tender value from block 2 before reading the numbers.
SELECT s.org_id, s.salesperson, s.store,
       count(*)                    AS lines_on_that_tender,
       count(DISTINCT s.trans_id)  AS transactions
FROM commcalc.raw_sales s
WHERE s.org_id IN ('00000000-0000-0000-0000-000000000001',
                   '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND s.period IN ('July 2026','2026-07')
  AND coalesce(upper(trim(s.voided)),'') NOT IN ('YES','TRUE','1','Y','VOID','VOIDED')
  AND coalesce(trim(s.trans_type),'') <> 'Return'
  AND lower(trim(s.tender_type)) = lower('TW Financing')     -- ← the real value from block 2
GROUP BY 1,2,3 ORDER BY 1, lines_on_that_tender DESC;


-- ══ 7. STORED SNAPSHOT — what the reps are being paid RIGHT NOW (so a delta is measurable) ═══════
SELECT org_id, period, epay_salesperson, storeops_name, store, plan_name,
       plan_comm, residual_installment_comm, installment_comm_sale, carrier_statement_comm,
       subtotal, tier, total_payout
FROM commcalc.rep_commissions
WHERE org_id IN ('00000000-0000-0000-0000-000000000001',
                 '854f6d7b-6590-4e4d-88ab-646f560d4f4c')
  AND period IN ('July 2026','2026-07')
ORDER BY org_id, total_payout DESC;


-- ══ 8. THE BETTER TOOL — run the blast radius IN THE APP, not here ══════════════════════════════
-- The SQL above cannot reproduce the tier multiplier, the assignment precedence or the multi-month
-- gate. These endpoints run the REAL engine twice and are READ-ONLY (no writes, no recalculation):
--
--   GET  /api/v1/commcalc/commission-plans/keyword-collisions?period=July%202026&org_id=<ORG>
--        → every description-keyword rule, the ITEMS it really hits, and the other field carrying
--          the same word (i.e. "edge is also a tender value").
--
--   POST /api/v1/commcalc/commission-plans/rule-impact?org_id=<ORG>
--        {"period":"July 2026",
--         "overrides":{"<rule_id from block 1>":
--            {"match_field":"tender_type","match_op":"equals","match_value":"<real tender>"}}}
--        → per-rep before/after, every freed line WITH its tender, and per line whether a multi-month
--          schedule actually picks it up (`freed_paying_nothing` = the ones that would pay $0).
--
--   GET  /api/v1/commcalc/commission-plans/pay-warnings?period=July%202026&org_id=<ORG>
--        → the activations no rule and no schedule pays.


-- ══ 9. THE PROPOSED CONFIG CHANGE — DO NOT RUN UNTIL GATE-2 ═════════════════════════════════════
-- Prefer the ADMIN UI (/commcalc/commission-plans → the plan → the "edge" rule row):
--     Match field  = tender_type        (already offered; the engine has supported it since mig 059)
--     Op           = equals   (or `in` for several financing tenders, comma-separated)
--     Value        = picked from the dropdown, which lists this tenant's REAL tender values
-- The editor now also shows, inline, WHICH ITEMS a description pattern hits and whether the same word
-- is a value of another field — the guard that would have caught this.
--
-- If SQL is preferred, this is the exact equivalent. It is per-tenant and per-rule-id: a tenant with no
-- 'edge' rule is untouched, because the WHERE clause matches nothing there.
--
-- (a) RE-KEY the edge rule to the financing tender  ── uncomment, set the two literals, then run.
-- UPDATE commcalc.commission_rule
--    SET match_field = 'tender_type',
--        match_op    = 'equals',                         -- or 'in' with a comma list
--        match_value = 'TW Financing'                    -- ← the REAL value from block 2
--  WHERE org_id  = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'   -- ← one tenant at a time
--    AND id      = '<rule_id from block 1>';
--
-- (b) MAKE SURE THE FREED ACTIVATIONS ARE ACTUALLY PAID. This does NOT happen by itself. Either a
--     multi-month schedule already triggers on them (block 5a shows a chain) — in which case they were
--     being DOUBLE-paid and (a) alone is the whole fix — or a trigger must be added/widened:
-- UPDATE commcalc.plan_installment_schedule
--    SET trigger_match_field = 'activation_bucket',   -- or 'contract_type'
--        trigger_match_op    = 'in',
--        trigger_match_value = 'premium,byod'
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
--    AND id     = '<schedule_id from block 1b>';
--
-- (c) VERIFY BEFORE RECALCULATING: re-run block 8's rule-impact with the same values and confirm
--     `freed_paying_nothing` is 0. Only then run Calculate for the period.
