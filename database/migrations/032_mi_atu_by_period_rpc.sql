-- 032_mi_atu_by_period_rpc.sql
-- TRUE RESIDUAL = MI + ATU, aggregated in Postgres.
--
-- The Comprehensive Comp report is ~95% promo/bounty COMPENSATION (Commission + SPIFF), NOT residual.
-- Real residual = recurring per-subscriber income = MI + ATU (raw_mi). The "Total Compensation Trend"
-- page (formerly "Residual Trend") shows this alongside total comp.
--
-- Why an RPC: raw_mi is ~38k rows/MONTH; summing in Python (paginated) made the trend endpoint take
-- ~30s. comp_trend._mi_atu_by_period calls this RPC and falls back to 0 (fast) until it exists, so the
-- page is never blocked — running this just lights up the residual_mi_atu column.
--
-- Robust casts: org_id may be uuid or text; payout columns may be numeric or text.

create or replace function commcalc.mi_atu_by_period(p_org_id text, p_periods text[])
returns table(period text, residual_mi_atu numeric)
language sql
stable
as $$
  select period,
         coalesce(sum(
             coalesce(nullif(trim(actual_mi_payout::text),  ''), '0')::numeric
           + coalesce(nullif(trim(actual_atu_payout::text), ''), '0')::numeric
         ), 0) as residual_mi_atu
  from commcalc.raw_mi
  where org_id::text = p_org_id
    and period = any(p_periods)
  group by period;
$$;

-- Allow the API roles to call it (match the project's other RPC grants).
grant execute on function commcalc.mi_atu_by_period(text, text[]) to anon, authenticated, service_role;
