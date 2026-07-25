-- luxelink_plan_config_diagnostic.sql — READ-ONLY. Paste into the Supabase SQL editor and send back the
-- output of every block. Nothing here writes, and nothing triggers a calculation.
--
-- Purpose: confirm whether luxelink's "commissions are not calculating per the tier commissions and
-- Commission Plans" is CONFIG or CODE. Each block maps to a specific failure mode proven in
-- backend/scratchpad/luxelink_tier_multimonth_proof.py.
--
-- Set the tenant + the period you are questioning:
\set LUX '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
-- (psql \set doesn't work in the Supabase web editor — if so, replace :'LUX' with the quoted uuid below.)

-- ── 1. CARRIER MODE — which engine pays this tenant at all ────────────────────────────────────────
-- Expect exactly ONE is_default=true, and it must be NON-Boost for plan-mode pay.
SELECT id, name, code, is_default
FROM commcalc.carrier
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
ORDER BY is_default DESC, name;

-- ── 2. PLANS + TIER CONFIG (incl. the mig-232 columns; they simply won't exist until 232 is run) ──
-- base_tier_metric NULL/'none' => the tier multiplier is FORCED to 1.0 no matter what the tiers say.
SELECT p.id, p.name, p.is_active, p.carrier_id, p.base_tier_metric,
       to_jsonb(p) -> 'tier_count_basis'          AS tier_count_basis,
       to_jsonb(p) -> 'tier_match_field'          AS tier_match_field,
       to_jsonb(p) -> 'tier_match_op'             AS tier_match_op,
       to_jsonb(p) -> 'tier_match_value'          AS tier_match_value,
       to_jsonb(p) -> 'tier_below_min_multiplier' AS tier_below_min_multiplier,
       (SELECT count(*) FROM commcalc.commission_rule r
         WHERE r.plan_id = p.id AND r.org_id = p.org_id)                          AS rules,
       (SELECT count(*) FROM commcalc.commission_rule r
         WHERE r.plan_id = p.id AND r.org_id = p.org_id AND r.tiered)             AS tiered_rules,
       (SELECT count(*) FROM commcalc.commission_tier t
         WHERE t.plan_id = p.id AND t.org_id = p.org_id)                          AS tiers,
       (SELECT count(*) FROM commcalc.commission_plan_assignment a
         WHERE a.plan_id = p.id AND a.org_id = p.org_id)                          AS assignments
FROM commcalc.commission_plan p
WHERE p.org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
ORDER BY p.name;

-- ── 3. RULES — what each plan actually pays on ────────────────────────────────────────────────────
-- match_field='contract_type' + a POS that leaves Contract Type blank (block 7) = silently $0 lines.
SELECT p.name AS plan, r.sort, r.label, r.match_field, r.match_op, r.match_value,
       r.qualifies, r.payout_kind, r.amount, r.pct, r.tiered
FROM commcalc.commission_rule r
JOIN commcalc.commission_plan p ON p.id = r.plan_id
WHERE r.org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
ORDER BY p.name, r.sort;

-- ── 4. TIERS ──────────────────────────────────────────────────────────────────────────────────────
SELECT p.name AS plan, t.metric, t.min_count, t.multiplier, t.sort
FROM commcalc.commission_tier t
JOIN commcalc.commission_plan p ON p.id = t.plan_id
WHERE t.org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
ORDER BY p.name, t.min_count;

-- ── 5. ASSIGNMENTS vs WHO ACTUALLY SOLD — the uncovered-seller gap ───────────────────────────────
-- Any row with plan_name NULL sells but is paid $0 by design (carrier_mode='plan').
WITH sellers AS (
  SELECT DISTINCT btrim(salesperson) AS rep
  FROM commcalc.raw_sales
  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
    AND period IN ('July 2026', '2026-07')          -- <<< set the period you are questioning
    AND coalesce(btrim(salesperson), '') <> ''
    AND lower(coalesce(voided, '')) NOT IN ('true','yes','1','voided','void')
    AND coalesce(trans_type, '') <> 'Return'
),
emp AS (
  SELECT a.scope, a.scope_value, p.name AS plan_name
  FROM commcalc.commission_plan_assignment a
  JOIN commcalc.commission_plan p ON p.id = a.plan_id
  WHERE a.org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
)
SELECT s.rep,
       (SELECT string_agg(e.plan_name || ' [' || e.scope || ']', ', ')
          FROM emp e
         WHERE e.scope = 'default'
            OR (e.scope = 'employee' AND lower(btrim(e.scope_value)) = lower(s.rep))
            -- name-order bridge: assignments are usually "First Last", the POS emits "Last, First"
            OR (e.scope = 'employee' AND position(',' IN s.rep) > 0
                AND lower(btrim(split_part(s.rep, ',', 2)) || ' ' || btrim(split_part(s.rep, ',', 1)))
                    = lower(btrim(e.scope_value)))
       ) AS plan_name
FROM sellers s
ORDER BY plan_name NULLS FIRST, s.rep;

-- ── 6. MULTI-MONTH SCHEDULE — M1 gating + the month amounts ──────────────────────────────────────
-- gate_from_month = 1 => M1 IS gated on carrier evidence (the owner wants M1 paid at activation => 2).
SELECT s.id, p.name AS plan, s.name, s.is_active, s.num_months, s.gate_mode, s.gate_from_month,
       s.m1_gate, s.trigger_match_field, s.trigger_match_op, s.trigger_match_value,
       s.effective_from, s.effective_to, s.eligible_sale_periods,
       (SELECT jsonb_agg(jsonb_build_object('m', l.month_index, 'kind', l.payout_kind,
                                            'flat', l.flat_amount, 'pct', l.mrc_pct)
                         ORDER BY l.month_index)
          FROM commcalc.plan_installment_line l
         WHERE l.schedule_id = s.id AND l.org_id = s.org_id) AS months
FROM commcalc.plan_installment_schedule s
JOIN commcalc.commission_plan p ON p.id = s.plan_id
WHERE s.org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
ORDER BY p.name, s.name;

-- ── 7. GATE EVIDENCE SOURCE (mig 223 + the mig-232 ma_lookup_periods column) ─────────────────────
-- No luxelink row => it inherits the HOUSE 'plan' row => ma_commission / 'sale' lookup.
SELECT org_id, carrier_id, carrier_mode, gate_source, ma_month_field_prefix, ma_max_month,
       ma_month1_extra_fields, ma_min_amount, ma_payout_sign,
       to_jsonb(c) -> 'ma_lookup_periods' AS ma_lookup_periods, is_active, notes
FROM commcalc.installment_gate_source_config c
WHERE org_id IN ('854f6d7b-6590-4e4d-88ab-646f560d4f4c',
                 '00000000-0000-0000-0000-000000000001')
ORDER BY org_id, carrier_mode;

-- ── 8. CONTRACT TYPE reality + the tenant's classification config ────────────────────────────────
SELECT coalesce(nullif(btrim(contract_type), ''), '(BLANK)') AS contract_type,
       count(*) AS lines, count(DISTINCT trans_id) AS transactions
FROM commcalc.raw_sales
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
  AND period IN ('July 2026', '2026-07')
  AND lower(coalesce(voided, '')) NOT IN ('true','yes','1','voided','void')
  AND coalesce(trans_type, '') <> 'Return'
GROUP BY 1 ORDER BY lines DESC;

SELECT org_id,
       to_jsonb(a) -> 'contract_type_map' AS contract_type_map,
       jsonb_array_length(coalesce((to_jsonb(a) -> 'activation_rules')::jsonb, '[]'::jsonb))
         AS activation_rules_count,
       to_jsonb(a) -> 'activation_rules'  AS activation_rules
FROM commcalc.accessory_config a
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

SELECT org_id, pay_disabled, residual_visibility,
       to_jsonb(c) -> 'plan_ct_resolution' AS plan_ct_resolution
FROM commcalc.commission_org_config c
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

-- ── 9. VOIDED spellings actually present (does the shared-token fix move anything here?) ─────────
SELECT coalesce(nullif(btrim(voided), ''), '(BLANK)') AS voided_value, count(*) AS lines
FROM commcalc.raw_sales
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
  AND period IN ('July 2026', '2026-07')
GROUP BY 1 ORDER BY lines DESC;
-- Same question on the open-month feed:
SELECT coalesce(nullif(btrim(voided), ''), '(BLANK)') AS voided_value, count(*) AS lines
FROM commcalc.daily_sales_feed
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
  AND period IN ('July 2026', '2026-07')
GROUP BY 1 ORDER BY lines DESC;

-- ── 10. RESIDUAL EVIDENCE — is the master-agent file even in, and for which months? ──────────────
SELECT period, count(*) AS rows, count(DISTINCT imei) AS devices,
       sum((coalesce(spiff_m1,0) <> 0)::int) AS with_m1,
       sum((coalesce(spiff_m2,0) <> 0)::int) AS with_m2,
       sum((coalesce(spiff_m3,0) <> 0)::int) AS with_m3,
       sum((coalesce(rebate,0) <> 0)::int)   AS with_rebate
FROM commcalc.raw_ma_commission
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
GROUP BY period ORDER BY period;

-- ── 11. STALE SNAPSHOT — when was the stored answer last written? ────────────────────────────────
SELECT period, count(*) AS rep_rows, round(sum(total_payout)::numeric, 2) AS stored_total,
       max(created_at) AS last_written
FROM commcalc.rep_commissions
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
GROUP BY period ORDER BY period;

SELECT period, calc_status, updated_at
FROM commcalc.calc_status
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
ORDER BY updated_at DESC LIMIT 10;

-- ── 12. WHAT THE MULTI-MONTH LEDGER CURRENTLY SAYS (per pay month) ───────────────────────────────
SELECT pay_period, sale_period, month_index, status, count(*) AS rows,
       round(sum(amount)::numeric, 2) AS paid_amount
FROM commcalc.sale_installment_ledger
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
GROUP BY 1, 2, 3, 4
ORDER BY pay_period, sale_period, month_index, status;
