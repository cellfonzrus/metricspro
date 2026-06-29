-- 067_dynamic_commission_column_fn.sql — let the backend ADD a commission column at runtime (safely).
--
-- WHY: when a tenant uploads a carrier file with a category we don't have, the wizard must be able to
-- CREATE that category — which means adding a real column to commcalc.carrier_commission. PostgREST/the
-- service role can't run arbitrary DDL, and we don't want the user pasting ALTER TABLE per column. This
-- SECURITY DEFINER function is the ONE controlled seam: the backend calls it via .rpc('add_commission_column')
-- and it runs ALTER TABLE ADD COLUMN IF NOT EXISTS as the table owner — but ONLY for the whitelisted table,
-- a sanitised identifier, and an allowed type. Anything else raises. Run ONCE; after that the system extends
-- its own schema with no further SQL from you.
--
-- BOOST-SAFE: the whitelist is exactly carrier_commission (the NEW table). It can NEVER touch rep_commissions
-- or any live Boost table, even if called with a different name. ADD COLUMN IF NOT EXISTS is non-destructive.

CREATE OR REPLACE FUNCTION commcalc.add_commission_column(
  p_column TEXT,
  p_type   TEXT DEFAULT 'numeric',
  p_table  TEXT DEFAULT 'carrier_commission'
) RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = commcalc, pg_temp
AS $$
DECLARE
  v_type TEXT;
BEGIN
  -- 1) table whitelist — ONLY the additive carrier-commission table may be altered.
  IF p_table <> 'carrier_commission' THEN
    RAISE EXCEPTION 'add_commission_column: table "%" is not allowed (only carrier_commission)', p_table;
  END IF;

  -- 2) identifier sanitiser — lowercase, starts with a letter, [a-z0-9_], <=63 chars. Blocks injection.
  IF p_column !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'add_commission_column: invalid column name "%" (must match ^[a-z][a-z0-9_]{0,62}$)', p_column;
  END IF;

  -- 3) type whitelist → canonical Postgres type.
  v_type := CASE lower(coalesce(p_type,'numeric'))
              WHEN 'number'  THEN 'numeric'
              WHEN 'numeric' THEN 'numeric'
              WHEN 'amount'  THEN 'numeric'
              WHEN 'text'    THEN 'text'
              WHEN 'string'  THEN 'text'
              WHEN 'int'     THEN 'integer'
              WHEN 'integer' THEN 'integer'
              WHEN 'date'    THEN 'date'
              WHEN 'date10'  THEN 'date'
              WHEN 'bool'    THEN 'boolean'
              WHEN 'boolean' THEN 'boolean'
              ELSE NULL
            END;
  IF v_type IS NULL THEN
    RAISE EXCEPTION 'add_commission_column: unsupported type "%" (use numeric|text|integer|date|boolean)', p_type;
  END IF;

  -- 4) the only DDL, fully parameterised via format()/%I (quoted identifier) + validated %s type.
  EXECUTE format('ALTER TABLE commcalc.carrier_commission ADD COLUMN IF NOT EXISTS %I %s', p_column, v_type);
  RETURN format('added %I %s', p_column, v_type);
END;
$$;

-- Allow the API roles to invoke it (the function body is the guard; it bypasses RLS as definer).
GRANT EXECUTE ON FUNCTION commcalc.add_commission_column(TEXT, TEXT, TEXT) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 067 complete — commcalc.add_commission_column(p_column,p_type,p_table) installed' AS status;
