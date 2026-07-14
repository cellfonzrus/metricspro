-- 304_asset_charges_summary_rpc.sql — asset-6: checked-in reconstruction of commcalc.asset_charges_summary
--
-- WHY: this RPC powers the Charges Dashboard (backend/app/modules/asset/router.py:1653
-- GET /asset/charges-summary, called from frontend/src/app/(platform)/commcalc/asset/dashboard/page.tsx
-- and .../commcalc/asset/charges/[group]/page.tsx) and exists ONLY in live Supabase — there is no
-- checked-in migration that creates it. A fresh project / DR restore would silently lose the Charges
-- Dashboard (the endpoint would 42883 "function does not exist" the moment it's hit). This migration
-- closes that gap by RECONSTRUCTING the function from its call-site contract (below) rather than from
-- the real live source, which nobody here can read directly (Supabase SQL Editor is web-only /
-- operator-run — see CLAUDE.md "Infrastructure").
--
-- ⚠️ THIS IS A RECONSTRUCTION, NOT A CONFIRMED COPY OF THE LIVE FUNCTION. Before running this against
-- the live project, the OWNER must diff it against what's actually live:
--
--     select pg_get_functiondef(oid)
--     from pg_proc
--     where proname = 'asset_charges_summary';
--
-- Paste that alongside this file. If they materially agree (same params, same WHERE/GROUP BY logic,
-- same returned columns) this migration is a safe no-op CREATE OR REPLACE on the live project and a
-- real function on a fresh one. If they DIVERGE, the live definition wins — update this file to match
-- it (don't just run this blind and overwrite verified-correct live logic; see asset.md "never change
-- verified-correct behaviour"). See "AMBIGUITIES" below for the specific places drift is most likely.
--
-- ── CALL-SITE CONTRACT (from backend/app/modules/asset/router.py, the only caller) ────────────────
-- backend/app/modules/asset/router.py:1653-1665, GET /asset/charges-summary:
--   params = {
--       "p_org_id": org_id,          -- str (uuid), always present, query-param-sourced (multi-tenant
--                                        rule — never a Form field or the module ORG_ID constant)
--       "p_store": store or None,    -- str store address (= asset_ledger.store) or NULL for "all stores"
--       "p_market": market or None,  -- str market name (= asset_ledger.market) or NULL for "all markets"
--       "p_month": month,            -- int 1-12 or NULL
--       "p_year": year,              -- int or NULL
--       "p_week_friday": week_friday or None,  -- 'YYYY-MM-DD' str or NULL
--   }
--   agg = client.schema("commcalc").rpc("asset_charges_summary", params).execute().data or []
--
-- Router then does two passes over `agg`, consuming exactly these row fields — nothing else:
--   row["category"], row["store"], row["market"], row["cnt"], row["owed"], row["reimb"]
--
--   Pass 1 (lines 1667-1696): for every row whose `category` maps to a CHARGE_GROUPS bucket
--   (vip_fees / stock_balance / appeals / recon_oddity — see router.py CHARGE_GROUPS, the single
--   source of truth for that mapping, intentionally NOT duplicated into this SQL — see AMBIGUITY 1),
--   accumulate cnt/owed into that bucket's totals, by_category (keyed on `category`) and by_store
--   (keyed on `store`, carrying `market` along).
--
--   Pass 2 (lines 1699-1708): for every row whose `category` == 'RMA' (RMA is NOT one of the
--   CHARGE_GROUPS buckets, but IS folded into the "Total Loss" headline — see AMBIGUITY 2), derive a
--   per-row-group RMA loss: reimb<=0 -> add the full `owed`; 0 < reimb < owed-0.01 -> add the
--   shortfall (owed-reimb); reimb >= owed-0.01 -> add nothing (fully reimbursed). Total Loss =
--   appeals-group owed + this RMA loss.
--
-- Consequence: the RPC must NOT category-filter its result set — it has to return every category
-- (or at minimum: the 13 CHARGE_GROUPS categories PLUS 'RMA') for the router's two passes to see
-- everything they need. Filtering categories in SQL would be a second, hidden copy of CHARGE_GROUPS
-- that could silently drift from the Python one (SAP-configurable rule: don't duplicate a
-- single-source-of-truth mapping). This migration returns ALL categories, grouped, and leaves the
-- category selection entirely to the router's existing Python logic (which already discards anything
-- _cat_to_group() doesn't recognize).
--
-- Period-filter semantics replicated EXACTLY from router.py _in_period() (line 1364-1381) and
-- _row_period_date() (line 1359-1361), which are the same rules used by /owed-weekly, /charge-rows,
-- and the flag-sync functions, so the RPC must not diverge from them:
--   - if p_week_friday is given: filter is `billing_friday = p_week_friday` ONLY — p_month/p_year are
--     IGNORED entirely when a week filter is present (this is an if/elif in Python, not independent
--     ANDed filters — replicated below as mutually exclusive branches).
--   - else: "period date" = COALESCE(payg_date, date_sold, acquired_date) (PAYG > date_sold >
--     acquired, first non-null wins). p_month and p_year are INDEPENDENT optional filters (either can
--     be given alone) — a row with a NULL period date is excluded whenever either filter is given, and
--     included when neither is given. EXTRACT(...) against a NULL date naturally yields NULL, and
--     NULL = p_month is NULL (falsy), so this falls out of the WHERE clause automatically with no
--     special-casing needed.
--   - if neither p_month, p_year, nor p_week_friday is given: no period filter (all rows).
--
-- "owed" is uniformly asset_ledger.owed_to_vip and "reimb" is uniformly asset_ledger.reimbursement for
-- EVERY category, not just RMA — confirmed by cross-referencing every other place the router computes
-- "owed" from asset_ledger (get_charge_rows total_owed, _sync_appeal_flags amount, _classify_rma /
-- get_rma buckets, _sync_rma_flags) — all of them use owed_to_vip/reimbursement, never a different
-- column per category.
--
-- ── AMBIGUITIES (flagged per contract instruction, not silently guessed) ───────────────────────────
--
-- AMBIGUITY 1 — category filtering. Reconstructed as "no category filter, return everything grouped"
-- (see "Consequence" above). It is POSSIBLE the live function instead hardcodes the same 13+1
-- category list router.py's CHARGE_GROUPS encodes, as a WHERE category = ANY(...) clause, purely as a
-- payload-size optimization (fewer grouped rows returned). Functionally both choices produce identical
-- output through the router's existing Python filtering, so this is very likely a safe reconstruction
-- either way — flagged for completeness, not because behavior is expected to differ.
--
-- AMBIGUITY 2 — RMA "Total Loss" grouping granularity (the one place a real numeric drift is possible).
-- This migration groups by (category, store, market) and returns raw SUM(owed_to_vip)/SUM(reimbursement)
-- per group, exactly mirroring what router.py's Pass 2 arithmetic expects (it applies the reimb<=0 /
-- reimb<owed-0.01 bucket logic to the GROUP's summed owed/reimb, not to individual devices). This is a
-- known coarser approximation than the /rma endpoint's `_classify_rma()` (router.py line 1912), which
-- buckets full/short/none PER DEVICE before summing. If a single (category='RMA', store, market) group
-- contains RMA devices in different reimbursement buckets (e.g. one device fully reimbursed, another
-- not reimbursed at all, same store+market+month), grouped SUM-then-bucket can misclassify that group's
-- net contribution versus summing each device's own correctly-classified net loss. Whether the LIVE
-- function has this same limitation (i.e., whether "Total Loss" on the Charges Dashboard has ever been
-- expected to reconcile exactly against /rma's `net_loss` figure, or is understood as a coarser
-- approximation) is unconfirmed — router.py's own Pass 2 code (SUM then bucket, not per-device) is the
-- strongest evidence this matches live behavior, since the router was clearly written against whatever
-- shape the real RPC already returns. NOT changed/fixed here — reconstruction target is "what the live
-- function likely returns", not "what would be most accurate". If the pg_get_functiondef diff shows the
-- live function groups at a finer grain (e.g. by device id) specifically for the RMA category, update
-- this migration to match rather than "fixing" it unilaterally.
--
-- AMBIGUITY 3 — column types on asset_ledger's date fields (acquired_date, due_date, payg_date,
-- reimbursement_date, date_sold, billing_friday, trigger_date). commcalc.asset_ledger predates the
-- checked-in migration history (see 300_asset_ledger_staging_swap.sql's own note on this same gap) —
-- there is no CREATE TABLE to read the real types from, and this agent cannot query live
-- information_schema.columns. asset_parser.py writes these as Python strings formatted 'YYYY-MM-DD'
-- (pandas .dt.strftime), and application code elsewhere defensively does str(value)[:10] before
-- comparing (router.py _in_period, owed-weekly), which is consistent with EITHER a `date` column (the
-- str() is just defensive) or a `text` column storing ISO date strings. To be correct under both
-- possibilities, every date computation below explicitly casts with `::date` (a no-op if the column
-- is already `date`; a correct parse if it's `text` in 'YYYY-MM-DD' form). p_week_friday is declared
-- `date` for the same reason PostgREST already accepts an ISO string for a `date`-typed RPC parameter
-- from the Python caller today.
--
-- Safe to run: additive CREATE OR REPLACE FUNCTION, no table/column changes, no data mutation.
-- Idempotent: re-running just replaces the function definition with the same one.

create or replace function commcalc.asset_charges_summary(
  p_org_id       uuid,
  p_store        text default null,
  p_market       text default null,
  p_month        int  default null,
  p_year         int  default null,
  p_week_friday  date default null
)
returns table (
  category  text,
  store     text,
  market    text,
  cnt       bigint,
  owed      numeric,
  reimb     numeric
)
language sql
stable
as $$
  select
    al.category,
    al.store,
    al.market,
    count(*)                              as cnt,
    coalesce(sum(al.owed_to_vip), 0)      as owed,
    coalesce(sum(al.reimbursement), 0)    as reimb
  from commcalc.asset_ledger al
  where al.org_id = p_org_id
    and (p_store  is null or al.store  = p_store)
    and (p_market is null or al.market = p_market)
    and (
          -- week filter present: billing_friday match is the ONLY period condition, p_month/p_year
          -- are ignored (mirrors router.py _in_period()'s if-week/elif-month-year branching).
          (p_week_friday is not null and al.billing_friday::date = p_week_friday)
          or
          -- no week filter: independent optional month/year filters against the coalesced period
          -- date (PAYG > date_sold > acquired). A NULL period date fails any month/year filter that
          -- is actually supplied, and passes when neither is supplied.
          (
            p_week_friday is null
            and (p_month is null or extract(month from coalesce(al.payg_date::date, al.date_sold::date, al.acquired_date::date)) = p_month)
            and (p_year  is null or extract(year  from coalesce(al.payg_date::date, al.date_sold::date, al.acquired_date::date)) = p_year)
          )
        )
  group by al.category, al.store, al.market;
$$;

grant execute on function commcalc.asset_charges_summary(uuid, text, text, int, int, date) to anon, authenticated, service_role;

notify pgrst, 'reload schema';
select '304 complete — commcalc.asset_charges_summary reconstructed from call-site contract; DIFF AGAINST LIVE before relying on it, see header comment' as status;
