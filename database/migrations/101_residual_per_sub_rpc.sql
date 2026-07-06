-- 101_residual_per_sub_rpc.sql
-- Residual PER SUBSCRIBER, per STORE, per MONTH — for the Accounting "Residual per Subscriber" report.
--
-- Residual = actual_mi_payout + actual_atu_payout per raw_mi row (the recurring per-subscriber income;
-- see 032_mi_atu_by_period_rpc.sql for the company-wide version). This one adds the store dimension
-- (grouped by salesforce_id, which the endpoint joins to store_mapping) and a SUBSCRIBER count:
-- distinct phone numbers we're actually PAID residual on that month (MI+ATU nonzero).
--
-- Why an RPC: raw_mi is ~38k rows/MONTH. Summing + counting-distinct in Python (paginated) is slow;
-- the residual_subs.py helper calls this and falls back to a bounded Python aggregation if it's absent,
-- so the page always works — running this just makes it fast over full history.
--
-- Robust casts: org_id may be uuid or text; payout columns may be numeric or text.
-- p_periods NULL or empty = all periods (the endpoint trims to the last N months).

create or replace function commcalc.residual_per_sub_by_store(p_org_id text, p_periods text[])
returns table(period text, salesforce_id text, sum_mi numeric, sum_atu numeric, subs bigint, lines bigint)
language sql
stable
as $$
  select period,
         coalesce(nullif(trim(salesforce_id), ''), '') as salesforce_id,
         coalesce(sum(mi_v), 0)  as sum_mi,
         coalesce(sum(atu_v), 0) as sum_atu,
         count(distinct nullif(trim(phone_number), '')) filter (where (mi_v + atu_v) <> 0) as subs,
         count(*) as lines
  from (
    select period,
           salesforce_id,
           phone_number,
           coalesce(nullif(trim(actual_mi_payout::text),  ''), '0')::numeric as mi_v,
           coalesce(nullif(trim(actual_atu_payout::text), ''), '0')::numeric as atu_v
    from commcalc.raw_mi
    where org_id::text = p_org_id
      and (p_periods is null or cardinality(p_periods) = 0 or period = any(p_periods))
  ) x
  group by period, coalesce(nullif(trim(salesforce_id), ''), '');
$$;

grant execute on function commcalc.residual_per_sub_by_store(text, text[]) to anon, authenticated, service_role;
