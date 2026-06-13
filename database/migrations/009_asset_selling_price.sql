-- 009_asset_selling_price.sql
-- Adds the customer selling price (from sales transactions, matched by IMEI) to the
-- asset ledger so every asset report can show whether the rep charged enough, and
-- powers the "undercharge" flag (owed_to_vip > reimbursement + selling_price).
--
-- Run this in the Supabase SQL editor (Claude cannot run SQL).

ALTER TABLE commcalc.asset_ledger ADD COLUMN IF NOT EXISTS selling_price NUMERIC;

-- Backfill selling_price from raw_sales: per device IMEI, the device line's Ext Price
-- (the priciest valid, non-voided, non-return line carrying that serial). One UPDATE...FROM
-- join — fast — so it can run on every asset/sales upload without pulling rows into Python.
CREATE OR REPLACE FUNCTION commcalc.backfill_asset_selling_price(p_org_id uuid)
RETURNS integer AS $$
DECLARE n integer;
BEGIN
  WITH sales AS (
    SELECT
      upper(regexp_replace(coalesce(serial_1, ''), '\.0$', '')) AS imei_key,
      max(ext_price) AS price
    FROM commcalc.raw_sales
    WHERE org_id = p_org_id
      AND coalesce(serial_1, '') <> ''
      AND upper(coalesce(voided, '')) <> 'YES'
      AND coalesce(trans_type, '') <> 'Return'
    GROUP BY 1
  )
  UPDATE commcalc.asset_ledger a
  SET selling_price = s.price
  FROM sales s
  WHERE a.org_id = p_org_id
    AND a.esn_imei IS NOT NULL
    AND upper(regexp_replace(a.esn_imei, '\.0$', '')) = s.imei_key;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END;
$$ LANGUAGE plpgsql;

GRANT EXECUTE ON FUNCTION commcalc.backfill_asset_selling_price(uuid) TO anon, authenticated;
