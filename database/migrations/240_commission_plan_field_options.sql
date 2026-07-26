-- 240_commission_plan_field_options.sql — Postgres-side aggregates that power the Commission-Plan
--   "pick, don't type" option pickers (RULE THREE §3b; owner directive 2026-07-25).
--   mod-commission, band 200-299. READ-ONLY: three STABLE functions, no table, no data change.
--
-- WHY (owner, 2026-07-25): "Value not entered in commission plan should be from a drop down menu of
-- available options." A Commission-Plan rule pays on a MATCH (match_field / match_op / match_value) against
-- the sales line. Typing that value by hand has two failure modes, both silent and both money:
--   1. a typo (or a value that simply isn't in this tenant's data) matches NOTHING -> the rule pays $0 and
--      nothing on any screen says so;
--   2. two hand-typed CONTAINS patterns that overlap (the real luxelink case: 'home internet' + 'vhi')
--      both match the same lines -> the rep is paid TWICE for one sale (~$10 per line, per month).
-- Both are answerable from the tenant's OWN data, but only if the editor can SEE the observed values and
-- how many lines each rule would match. Pulling 40k+ sale rows into Python per keystroke is not an option
-- (CLAUDE.md: aggregate in Postgres), so these three functions do the aggregation in the database.
--
-- WHAT THIS ADDS (all read-only, org-scoped, additive, idempotent, safe to re-run):
--   1. commcalc.plan_match_facets(org, periods[], source, limit)
--      DISTINCT combinations of the SEVEN real match fields (department, category, contract_type,
--      tender_type, trans_type, product_desc, sku) with a line count each, over one tenant's sales for a
--      set of period spellings. Voided lines and Returns are excluded with the SAME rules the pay path
--      uses (commcalc.gp_report.VOID_TOKENS + trans_type <> 'Return'), so a count here means what the
--      engine would count. The combination grain is deliberate: rule matching depends ONLY on these seven
--      columns, so the editor can compute EXACT matched-line counts and EXACT rule-vs-rule overlaps from a
--      few hundred facet rows instead of every sale line.
--   2. commcalc.plan_match_facet_totals(org, periods[], source) — total lines + total distinct
--      combinations, so a truncated facet list can honestly report the share of lines it covers.
--   3. commcalc.plan_sales_periods(org, limit) — the period labels this tenant actually has sales for
--      (both spellings: 'June 2026' and '2026-06'), so the period box becomes a picker too.
--
-- `source`: 'raw_sales' (default) or 'feed'. The plan engine reads raw_sales and FALLS BACK to
-- daily_sales_feed when raw_sales has no rows for the period (commission_engine._read_sales) — the caller
-- mirrors that by asking for 'raw_sales' first and 'feed' only when it comes back empty. NOTE the daily
-- feed has NO sku column (mig 047), which is exactly why a sku-keyed rule cannot match feed lines; the
-- 'feed' branch returns NULL there so the UI can say so instead of implying options exist.
--
-- MONEY: NONE. Nothing here is called by _run_calculation / calculator.py / commission_engine.preview.
-- These functions only populate dropdowns and a warning line in the editor. The backend degrades to a
-- bounded Python scan when they are absent, so the feature works BEFORE this migration runs (it is just
-- slower and reports `source:'scan'`).

-- ── 1. plan_match_facets: distinct match-field combinations + line counts ─────────────────────────────
CREATE OR REPLACE FUNCTION commcalc.plan_match_facets(
  p_org uuid, p_periods text[], p_source text DEFAULT 'raw_sales', p_limit int DEFAULT 4000)
RETURNS TABLE(department text, category text, contract_type text, tender_type text,
              trans_type text, product_desc text, sku text, lines bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_limit int := greatest(1, least(coalesce(p_limit, 4000), 20000));
BEGIN
  IF p_source = 'feed' THEN
    RETURN QUERY
      SELECT btrim(coalesce(f.department, ''))    AS department,
             btrim(coalesce(f.category, ''))      AS category,
             btrim(coalesce(f.contract_type, '')) AS contract_type,
             btrim(coalesce(f.tender_type, ''))   AS tender_type,
             btrim(coalesce(f.trans_type, ''))    AS trans_type,
             btrim(coalesce(f.product_desc, ''))  AS product_desc,
             NULL::text                           AS sku,   -- daily_sales_feed has no sku column (mig 047)
             count(*)::bigint                     AS lines
        FROM commcalc.daily_sales_feed f
       WHERE f.org_id = p_org
         AND f.period = ANY(p_periods)
         AND lower(btrim(coalesce(f.voided, ''))) NOT IN ('true', 'yes', '1', 'voided', 'void')
         AND btrim(coalesce(f.trans_type, '')) <> 'Return'
       GROUP BY 1, 2, 3, 4, 5, 6, 7
       ORDER BY count(*) DESC, 1, 2, 3, 4, 5, 6   -- deterministic truncation set
       LIMIT v_limit;
  ELSE
    RETURN QUERY
      SELECT btrim(coalesce(s.department, ''))    AS department,
             btrim(coalesce(s.category, ''))      AS category,
             btrim(coalesce(s.contract_type, '')) AS contract_type,
             btrim(coalesce(s.tender_type, ''))   AS tender_type,
             btrim(coalesce(s.trans_type, ''))    AS trans_type,
             btrim(coalesce(s.product_desc, ''))  AS product_desc,
             btrim(coalesce(s.sku, ''))           AS sku,
             count(*)::bigint                     AS lines
        FROM commcalc.raw_sales s
       WHERE s.org_id = p_org
         AND s.period = ANY(p_periods)
         AND lower(btrim(coalesce(s.voided, ''))) NOT IN ('true', 'yes', '1', 'voided', 'void')
         AND btrim(coalesce(s.trans_type, '')) <> 'Return'
       GROUP BY 1, 2, 3, 4, 5, 6, 7
       ORDER BY count(*) DESC, 1, 2, 3, 4, 5, 6
       LIMIT v_limit;
  END IF;
END $$;
GRANT EXECUTE ON FUNCTION commcalc.plan_match_facets(uuid, text[], text, int) TO anon, authenticated, service_role;

-- ── 2. plan_match_facet_totals: how much of the tenant's data a truncated facet list represents ──────
CREATE OR REPLACE FUNCTION commcalc.plan_match_facet_totals(
  p_org uuid, p_periods text[], p_source text DEFAULT 'raw_sales')
RETURNS TABLE(lines bigint, combos bigint)
LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF p_source = 'feed' THEN
    RETURN QUERY
      SELECT count(*)::bigint,
             count(DISTINCT (btrim(coalesce(f.department, '')), btrim(coalesce(f.category, '')),
                             btrim(coalesce(f.contract_type, '')), btrim(coalesce(f.tender_type, '')),
                             btrim(coalesce(f.trans_type, '')), btrim(coalesce(f.product_desc, ''))))::bigint
        FROM commcalc.daily_sales_feed f
       WHERE f.org_id = p_org
         AND f.period = ANY(p_periods)
         AND lower(btrim(coalesce(f.voided, ''))) NOT IN ('true', 'yes', '1', 'voided', 'void')
         AND btrim(coalesce(f.trans_type, '')) <> 'Return';
  ELSE
    RETURN QUERY
      SELECT count(*)::bigint,
             count(DISTINCT (btrim(coalesce(s.department, '')), btrim(coalesce(s.category, '')),
                             btrim(coalesce(s.contract_type, '')), btrim(coalesce(s.tender_type, '')),
                             btrim(coalesce(s.trans_type, '')), btrim(coalesce(s.product_desc, '')),
                             btrim(coalesce(s.sku, ''))))::bigint
        FROM commcalc.raw_sales s
       WHERE s.org_id = p_org
         AND s.period = ANY(p_periods)
         AND lower(btrim(coalesce(s.voided, ''))) NOT IN ('true', 'yes', '1', 'voided', 'void')
         AND btrim(coalesce(s.trans_type, '')) <> 'Return';
  END IF;
END $$;
GRANT EXECUTE ON FUNCTION commcalc.plan_match_facet_totals(uuid, text[], text) TO anon, authenticated, service_role;

-- ── 3. plan_sales_periods: the period labels this tenant actually has sales for (period picker) ──────
-- Both spellings are returned as they are STORED ('June 2026' from raw_sales, '2026-06' where a loader
-- wrote the ISO form) — the caller de-duplicates through commission_engine._pvariants, which is the same
-- helper the pay path uses, so the picker can never offer a spelling the engine wouldn't read.
CREATE OR REPLACE FUNCTION commcalc.plan_sales_periods(p_org uuid, p_limit int DEFAULT 36)
RETURNS TABLE(period text, lines bigint, source text)
LANGUAGE sql STABLE AS $$
  SELECT p.period, p.lines, p.source FROM (
    SELECT s.period AS period, count(*)::bigint AS lines, 'raw_sales'::text AS source
      FROM commcalc.raw_sales s
     WHERE s.org_id = p_org AND coalesce(btrim(s.period), '') <> ''
     GROUP BY s.period
    UNION ALL
    SELECT f.period AS period, count(*)::bigint AS lines, 'feed'::text AS source
      FROM commcalc.daily_sales_feed f
     WHERE f.org_id = p_org AND coalesce(btrim(f.period), '') <> ''
     GROUP BY f.period
  ) p
  ORDER BY p.lines DESC
  LIMIT greatest(1, least(coalesce(p_limit, 36), 500));
$$;
GRANT EXECUTE ON FUNCTION commcalc.plan_sales_periods(uuid, int) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 240 complete — commission-plan field-option aggregates (read-only)' AS status;
