-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- READ-ONLY DIAGNOSTICS — luxelink July 2026: FWA month-1 $0 + accessory %-GP inconsistency
-- Paste into the Supabase SQL Editor (https://supabase.com → project etxdalernqqtwjcrtcuj → SQL).
--
-- EVERY STATEMENT BELOW IS A SELECT. Nothing writes, nothing recomputes, nothing changes a payout.
-- Safe to run at any time, including mid-month.
--
-- HOW TO USE: run STEP 0 first, copy the org_id it returns for the luxelink tenant, and paste it
-- into the `:ORG` line of each block below (replace the whole quoted string, keep the ::uuid cast).
-- The org_id is NEVER hard-coded here on purpose — it is a different value in every environment and
-- filing a query under the wrong tenant is exactly how another tenant's data gets read.
--
-- PERIOD SPELLING: raw_sales stores 'July 2026' on some paths and '2026-07' on others. Every block
-- below matches BOTH spellings, because a query that filters on one spelling silently returns zero
-- rows and reads as "there is no data".
-- ═══════════════════════════════════════════════════════════════════════════════════════════════


-- ─── STEP 0 — find the tenant's org_id ────────────────────────────────────────────────────────
select org_id, name, slug, is_active
from storeops.tenants
order by name;


-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- ISSUE 1 — "Home Internet / FWA month 1 pays $0 although it passed the gate"
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

-- ─── 1A — EVERY sale line of the owner's FWA transaction ──────────────────────────────────────
-- WHAT TO LOOK FOR: is there a SEPARATE rate-plan / airtime line (one carrying a monthly charge,
-- typically with the MDN and a BLANK Serial 1)? The engine pays month 1 as a PERCENTAGE OF THE
-- MONTHLY RATE PLAN, and it can only find that percentage on a line that identifies as the plan.
-- The expectation from the drill-down label is that this transaction has only a DEVICE line (whose
-- description contains the whole FWA promo text) plus an "Activation payment" line with $0 Ext Price.
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org)
select rs.trans_id, rs.trans_date, rs.salesperson, rs.store,
       rs.department, rs.category, rs.product_desc, rs.sku,
       rs.mdn, rs.serial_1, rs.contract_type,
       rs.ext_price, rs.gp, (rs.ext_price - rs.gp) as implied_cost,
       rs.voided, rs.trans_type, rs.period
from commcalc.raw_sales rs, p
where rs.org_id = p.org
  and rs.period in ('July 2026', '2026-07')
  and rs.trans_id in (
        select trans_id from commcalc.raw_sales
        where org_id = p.org and period in ('July 2026', '2026-07')
          and serial_1 = '358835493293747')
order by rs.ext_price desc;


-- ─── 1B — every HOME-INTERNET / FWA line in the month, and what wording it carries ────────────
-- WHAT TO LOOK FOR: the distinct product descriptions to map. Each row here is a candidate for the
-- "Plan Installments → MRC mapping" entry that makes month 1 pay.
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org)
select rs.product_desc, rs.department, rs.category, rs.sku,
       count(*) as lines,
       count(distinct rs.trans_id) as transactions,
       round(avg(rs.ext_price)::numeric, 2) as avg_ext_price,
       min(rs.serial_1) filter (where coalesce(rs.serial_1, '') <> '') as sample_serial
from commcalc.raw_sales rs, p
where rs.org_id = p.org
  and rs.period in ('July 2026', '2026-07')
  and (lower(rs.product_desc) like '%home internet%'
    or lower(rs.product_desc) like '%internet gateway%'
    or lower(rs.product_desc) like '%fwa%'
    or lower(rs.product_desc) like '%router%'
    or lower(rs.category)     like '%home internet%')
group by 1, 2, 3, 4
order by lines desc;


-- ─── 1C — the installment schedule that is paying that month, and HOW ─────────────────────────
-- WHAT TO LOOK FOR: `payout_kind`. If month 1 is `pct_mrc`, the amount is mrc_pct × the resolved
-- MRC — and a $0 MRC pays $0 no matter what the gate says. If it is `flat`, look at flat_amount.
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org)
select s.id as schedule_id, s.name, s.plan_id, cp.name as plan_name,
       s.num_months, s.trigger_match_field, s.trigger_match_op, s.trigger_match_value,
       s.gate_mode, s.gate_from_month, s.m1_gate, s.is_active,
       s.qualifying_categories,
       l.month_index, l.payout_kind, l.flat_amount, l.mrc_pct, l.mrc_source
from commcalc.plan_installment_schedule s, p
left join commcalc.plan_installment_line l on l.schedule_id = s.id and l.org_id = s.org_id
left join commcalc.commission_plan cp on cp.id = s.plan_id and cp.org_id = s.org_id
where s.org_id = p.org
order by s.name, l.month_index;


-- ─── 1D — the MRC mappings that exist today ───────────────────────────────────────────────────
-- WHAT TO LOOK FOR: whether ANY row would match the FWA wording. `match_op='contains'` matches when
-- plan_pattern appears inside the line's description; `equals` needs the whole description.
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org)
select pm.plan_pattern, pm.match_op, pm.mrc, pm.carrier_id, pm.priority,
       pm.confirmed, pm.classification, pm.is_active
from commcalc.product_mrc pm, p
where pm.org_id = p.org
order by pm.priority nulls last, pm.plan_pattern;


-- ─── 1E — the tenant's rate-plan LINE MATCHER (what wording counts as "the plan line") ────────
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org)
select c.installment_mrc_basis, c.plan_line_matcher, c.installment_mrc_hardware_guard,
       c.hardware_line_matcher, c.installment_category_qualification
from commcalc.commission_org_config c, p
where c.org_id = p.org;


-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- ISSUE 2 — "accessory % GP payouts are inconsistent"
-- THE DECISIVE PAIR IS 2A + 2B. Run them together and read them side by side.
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

-- ─── 2A — THE RATE. This single row decides which explanation is true. ────────────────────────
-- The engine treats `pct` as a FRACTION: 0.10 = 10%. There is NO clamp on save.
--   • pct ≈ 0.175  → the rate is right; a $210 payout would have to come from an inflated GP.
--   • pct = 17.5   → the rate was typed as a whole percent and the engine is paying 1750% of GP.
--                    17.5 × 12.00 = 210.00 and 17.5 × 18.00 = 315.00 — exactly the owner's numbers.
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org)
select cp.name as plan_name, r.label, r.payout_kind,
       r.pct as stored_rate,
       round((r.pct * 100)::numeric, 4) as reads_as_percent,
       r.amount, r.match_field, r.match_op, r.match_value, r.qualifies, r.tiered, r.sort,
       case when r.payout_kind in ('pct_gp','pct_price_over_cost','pct_mrc') and r.pct > 1
            then 'SUSPECT — a fraction greater than 1 pays more than the whole basis'
            when r.payout_kind in ('pct_gp','pct_price_over_cost','pct_mrc') and r.pct = 0
            then 'SUSPECT — a percentage rule with a 0 rate pays $0 on every line'
            else 'ok' end as rate_check
from commcalc.commission_rule r
join commcalc.commission_plan cp on cp.id = r.plan_id and cp.org_id = r.org_id, p
where r.org_id = p.org
order by cp.name, r.sort;


-- ─── 2B — the owner's EXACT accessory lines, with the cost each one implies ───────────────────
-- raw_sales has NO cost column, so cost is implied: cost = ext_price − gp.
--   • gp = 0            → the POS catalog cost equals the retail price ("* BYOD" class) → %-of-GP = $0
--   • gp > ext_price    → the implied cost is NEGATIVE (impossible) → the payout inflates
-- `paid_at_stored_rate` replays what the engine would pay for that line, using the tenant's own rate.
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org),
     rate as (select max(r.pct) as pct
              from commcalc.commission_rule r, p
              where r.org_id = p.org and r.payout_kind = 'pct_gp')
select rs.trans_date, rs.trans_id, rs.salesperson, rs.store,
       rs.department, rs.category, rs.product_desc, rs.sku,
       rs.ext_price, rs.gp,
       (rs.ext_price - rs.gp) as implied_cost,
       round((rate.pct * rs.gp)::numeric, 2) as paid_at_stored_rate,
       case when rs.ext_price < 0.01                    then 'zero-value line'
            when rs.gp > rs.ext_price + 0.005           then 'IMPOSSIBLE — implied cost is negative'
            when abs(rs.gp) <= 0.005                    then 'GP $0 — catalog cost equals retail'
            when abs(rs.gp - rs.ext_price) <= 0.005     then 'implied cost is $0'
            when rs.gp < -0.005                         then 'negative GP — sold below cost'
            else 'ok' end as data_check
from commcalc.raw_sales rs, p, rate
where rs.org_id = p.org
  and rs.period in ('July 2026', '2026-07')
  and coalesce(upper(rs.voided), 'NO') <> 'YES'
  and (lower(rs.product_desc) like '%screen protector%'
    or lower(rs.product_desc) like '%pop socket%'
    or lower(rs.product_desc) like '%headphone%'
    or lower(rs.product_desc) like '%case%'
    or lower(rs.product_desc) like '%earphone%'
    or lower(rs.product_desc) like '%charging block%'
    or lower(rs.product_desc) like '%ryder%')
order by rs.trans_date, rs.trans_id, rs.product_desc;


-- ─── 2C — the FULL accessory item list (the Option-A worksheet) ───────────────────────────────
-- Every item that a %-of-GP rule can pay on, with how many lines carry an unusable cost and how
-- much was sold under each. THIS IS THE LIST TO FIX IN THE POS.
-- NOTE the department/category filter is a REPORTING convenience, not the pay classifier — the
-- engine uses the plan rule's own matcher (see 2A). Widen the LIKE list if your accessory
-- department is spelled differently.
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org)
select rs.product_desc, rs.sku, rs.department, rs.category,
       count(*)                                                   as lines,
       round(sum(rs.ext_price)::numeric, 2)                       as sold_dollars,
       round(sum(rs.gp)::numeric, 2)                              as gp_dollars,
       round(min(rs.ext_price - rs.gp)::numeric, 2)               as implied_cost_min,
       round(max(rs.ext_price - rs.gp)::numeric, 2)               as implied_cost_max,
       count(*) filter (where abs(rs.gp) <= 0.005)                as lines_gp_zero,
       count(*) filter (where rs.gp > rs.ext_price + 0.005)       as lines_cost_negative,
       count(*) filter (where rs.gp < -0.005)                     as lines_gp_negative
from commcalc.raw_sales rs, p
where rs.org_id = p.org
  and rs.period in ('July 2026', '2026-07')
  and coalesce(upper(rs.voided), 'NO') <> 'YES'
  and (lower(rs.department) like '%accessor%' or lower(rs.category) like '%accessor%')
group by 1, 2, 3, 4
having count(*) filter (where abs(rs.gp) <= 0.005) > 0
    or count(*) filter (where rs.gp > rs.ext_price + 0.005) > 0
    or count(*) filter (where rs.gp < -0.005) > 0
order by sold_dollars desc;


-- ─── 2D — what the POS product catalog says the cost is, next to what the sales imply ─────────
-- WHAT TO LOOK FOR: rows where `catalog_cost` equals `retail`-ish — that is the "* BYOD" class the
-- module has hit before. A catalog row is what makes Option A computable; items with no catalog row
-- stay "unknown until the owner sets a cost" and are never guessed.
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org),
     s as (select rs.sku, upper(btrim(rs.product_desc)) as dkey,
                  count(*) as lines,
                  round(min(rs.ext_price - rs.gp)::numeric, 2) as implied_cost_min,
                  round(max(rs.ext_price - rs.gp)::numeric, 2) as implied_cost_max,
                  round(avg(rs.ext_price)::numeric, 2) as avg_price
           from commcalc.raw_sales rs, p
           where rs.org_id = p.org
             and rs.period in ('July 2026', '2026-07')
             and coalesce(upper(rs.voided), 'NO') <> 'YES'
             and (lower(rs.department) like '%accessor%' or lower(rs.category) like '%accessor%')
           group by 1, 2)
select s.dkey as product_desc, s.sku, s.lines, s.avg_price,
       s.implied_cost_min, s.implied_cost_max,
       c.cost as catalog_cost, c.retail_price as catalog_retail,
       case when c.cost is null then 'no catalog row — Option A cannot be computed for this item'
            when abs(coalesce(c.cost,0) - coalesce(c.retail_price, s.avg_price)) <= 0.005
                 then 'CATALOG COST == RETAIL — this is what makes GP $0'
            else 'catalog has a real cost' end as catalog_check
from s
left join lateral (
    select rc.cost, rc.retail_price
    from commcalc.raw_catalog rc, p
    where rc.org_id = p.org
      and (upper(btrim(rc.sku)) = upper(btrim(s.sku))
        or upper(btrim(rc.product_desc)) = s.dkey)
    limit 1) c on true
order by s.lines desc;


-- ─── 2E — period-spelling sanity (run this if any block above returns ZERO rows) ──────────────
-- If one spelling has rows and the other does not, that is normal. If BOTH are zero the month's
-- sales are simply not in raw_sales yet (they may still be in the daily feed).
with p as (select 'REPLACE-WITH-ORG-ID-FROM-STEP-0'::uuid as org)
select 'raw_sales' as source, rs.period, count(*) as rows_present,
       round(sum(rs.ext_price)::numeric, 2) as ext_price, round(sum(rs.gp)::numeric, 2) as gp
from commcalc.raw_sales rs, p
where rs.org_id = p.org and rs.period in ('July 2026', '2026-07')
group by 1, 2
union all
select 'daily_sales_feed', f.period, count(*),
       round(sum(f.ext_price)::numeric, 2), round(sum(f.gp)::numeric, 2)
from commcalc.daily_sales_feed f, p
where f.org_id = p.org and f.period in ('July 2026', '2026-07')
group by 1, 2
order by 1, 2;
