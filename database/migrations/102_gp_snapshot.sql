-- 102_gp_snapshot.sql
-- Per-period Gross Profit snapshot, so the Trends hub can chart Net Profit / Revenue month-over-month
-- WITHOUT recomputing the GP engine (40k+ rows) for every month on every page load.
--
-- Written best-effort each time GET /commcalc/gp/{period} runs (and by the gp-trend endpoint when it
-- computes a missing month), keyed by (org_id, period). store_rows holds the per-store totals the
-- trend needs. Safe to run more than once.

create table if not exists commcalc.gp_snapshot (
  org_id      uuid        not null,
  period      text        not null,
  computed_at timestamptz not null default now(),
  store_rows  jsonb       not null default '[]',  -- [{store, store_code, market, total_rev, net_profit}]
  primary key (org_id, period)
);

grant select, insert, update, delete on commcalc.gp_snapshot to anon, authenticated, service_role;
