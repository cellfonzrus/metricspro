-- 745_pos_syscat_config_and_list_price.sql
-- 2026-08-11 — OWNER DIRECTIVE: "in the POS the unit price should be editable and the system
-- category should be addressed or editable." Owner then chose: price editable by ANYONE on the
-- register (no approval step, but record the original list price), and the system-category list
-- TENANT-EDITABLE (config table, seed the current 4 so nothing breaks).
--
-- PART 1 — pos.system_categories. `system_category` was hardcoded in THREE places: this CHECK
-- constraint, SYSTEM_CATEGORIES in pos/products/page.tsx, and _sys_cat() in core/onboarding.py.
-- A tenant could not add "Tablet" or "Watch", which is why 96 of luxelink's 118 products sit in
-- the catch-all "Regular". Per [[saas-sap-configurable-directive]] this becomes a config table
-- with an admin UI. MEASURED before writing: system_category drives NOTHING downstream — no
-- commission or GP classifier reads it; only the product-list filter and the importer. So
-- widening the vocabulary cannot move a payout.
--
-- PART 2 — pos.sale_items.list_price. The register is about to allow price edits, and today a
-- line records only what was CHARGED. Without the original there is no way to see, after the
-- fact, that a rep sold a $199 case for $99 — the margin loss is invisible and indistinguishable
-- from a cheap product. ⚠️ NOTE the downstream effect, deliberate and owner-chosen: commcalc_feed
-- computes ext_price = (unit_price − discount) × qty, so an edited price DOES change commission.
-- That is correct (accessories pay a % of SALE PRICE) but it is why the original must be kept.

BEGIN;

-- ── PART 1a: the config table ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pos.system_categories (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL,
  name        text NOT NULL,
  sort_order  int  NOT NULL DEFAULT 100,
  is_active   boolean NOT NULL DEFAULT true,
  is_builtin  boolean NOT NULL DEFAULT false,   -- the original 4: renameable, never deletable
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT system_categories_org_name_uniq UNIQUE (org_id, name)
);

CREATE INDEX IF NOT EXISTS system_categories_org_idx ON pos.system_categories (org_id, is_active);

-- Seed the existing 4 for every org that already has a POS catalog. Both conflict-target columns
-- are NOT NULL, so ON CONFLICT is a real no-op on re-run ([[on-conflict-nullable-unique-trap]]).
-- New tenants are seeded lazily by GET /pos/system-categories, so no core/onboarding.py change.
INSERT INTO pos.system_categories (org_id, name, sort_order, is_builtin)
SELECT o.org_id, v.name, v.sort_order, true
  FROM (SELECT DISTINCT org_id FROM pos.products) o
 CROSS JOIN (VALUES ('Accessory', 10), ('Cell Phone', 20),
                    ('Regular', 30), ('Service', 40)) AS v(name, sort_order)
    ON CONFLICT (org_id, name) DO NOTHING;

-- ── PART 1b: retire the CHECK ──────────────────────────────────────────────────────────────────
-- The API validates against the org's own active list instead. Dropping the constraint alone
-- would leave the column unvalidated, which is why the router gains that check in the same commit.
ALTER TABLE pos.products DROP CONSTRAINT IF EXISTS products_system_category_check;

-- ── PART 2: the original price, for audit ──────────────────────────────────────────────────────
ALTER TABLE pos.sale_items ADD COLUMN IF NOT EXISTS list_price numeric;

COMMENT ON COLUMN pos.sale_items.list_price IS
  'The product''s retail_price at the moment it was added to the cart. An override is exactly '
  'list_price <> unit_price. NULL on rows written before migration 745.';

-- pos.checkout enumerates its columns explicitly, so the table change alone would silently drop
-- list_price on every sale. Body is byte-identical to the deployed function except the two added
-- tokens. A caller that sends no list_price records the charged price, i.e. "no override" — the
-- honest reading, since a client that cannot override cannot have overridden.
CREATE OR REPLACE FUNCTION pos.checkout(p_org uuid, p_sale jsonb, p_items jsonb, p_payments jsonb)
 RETURNS pos.sales
 LANGUAGE plpgsql
 SET search_path TO 'pos', 'pg_temp'
AS $function$
DECLARE
  v_sale pos.sales;
  it JSONB;
  pay JSONB;
BEGIN
  IF p_org IS NULL THEN
    RAISE EXCEPTION 'org is required';
  END IF;
  IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) = 0 THEN
    RAISE EXCEPTION 'a sale needs at least one item';
  END IF;

  INSERT INTO pos.sales (org_id, store_code, customer_id, employee_id, receipt_type, status,
                         subtotal, discount_total, tax_total, total, balance, is_activation_sale,
                         receipt, notes)
  VALUES (
    p_org,
    NULLIF(btrim(COALESCE(p_sale->>'store_code', '')), ''),
    NULLIF(p_sale->>'customer_id', '')::uuid,
    NULLIF(btrim(COALESCE(p_sale->>'employee_id', '')), ''),
    COALESCE(NULLIF(p_sale->>'receipt_type', ''), 'sale'),
    'completed',
    COALESCE((p_sale->>'subtotal')::numeric, 0),
    COALESCE((p_sale->>'discount_total')::numeric, 0),
    COALESCE((p_sale->>'tax_total')::numeric, 0),
    COALESCE((p_sale->>'total')::numeric, 0),
    COALESCE((p_sale->>'balance')::numeric, 0),
    COALESCE((p_sale->>'is_activation_sale')::boolean, false),
    p_sale->'receipt',
    NULLIF(p_sale->>'notes', '')
  )
  RETURNING * INTO v_sale;

  FOR it IN SELECT * FROM jsonb_array_elements(p_items) LOOP
    INSERT INTO pos.sale_items (org_id, sale_id, product_id, product_type, description,
                                serial_number, qty, unit_price, list_price, cost, discount,
                                tax_rate, tax_value, extended_price)
    VALUES (
      p_org, v_sale.id,
      (it->>'product_id')::uuid,
      NULLIF(it->>'product_type', ''),
      NULLIF(it->>'description', ''),
      NULLIF(btrim(COALESCE(it->>'serial_number', '')), ''),
      COALESCE((it->>'qty')::int, 1),
      COALESCE((it->>'unit_price')::numeric, 0),
      COALESCE((it->>'list_price')::numeric, (it->>'unit_price')::numeric, 0),
      (it->>'cost')::numeric,
      COALESCE((it->>'discount')::numeric, 0),
      COALESCE((it->>'tax_rate')::numeric, 0),
      COALESCE((it->>'tax_value')::numeric, 0),
      COALESCE((it->>'extended_price')::numeric, 0)
    );
  END LOOP;

  IF p_payments IS NOT NULL AND jsonb_typeof(p_payments) = 'array' THEN
    FOR pay IN SELECT * FROM jsonb_array_elements(p_payments) LOOP
      INSERT INTO pos.sale_payments (org_id, sale_id, payment_method, amount,
                                     card_last_four, check_number)
      VALUES (
        p_org, v_sale.id,
        pay->>'payment_method',
        COALESCE((pay->>'amount')::numeric, 0),
        NULLIF(pay->>'card_last_four', ''),
        NULLIF(pay->>'check_number', '')
      );
    END LOOP;
  END IF;

  RETURN v_sale;
END;
$function$;

-- Fail loudly rather than half-apply: the seed must have produced the 4 builtins for every org
-- that has products, the CHECK must be gone, and checkout must still write every original column.
DO $$
DECLARE bad int; def text;
BEGIN
  SELECT count(*) INTO bad
    FROM (SELECT org_id FROM pos.products GROUP BY org_id) p
   WHERE (SELECT count(*) FROM pos.system_categories s
           WHERE s.org_id = p.org_id AND s.is_builtin) <> 4;
  IF bad > 0 THEN RAISE EXCEPTION '% org(s) did not get the 4 builtin categories', bad; END IF;

  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'products_system_category_check') THEN
    RAISE EXCEPTION 'the system_category CHECK survived the drop';
  END IF;

  SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'pos' AND p.proname = 'checkout';
  IF def IS NULL OR def NOT LIKE '%list_price%' OR def NOT LIKE '%sale_payments%'
     OR def NOT LIKE '%extended_price%' THEN
    RAISE EXCEPTION 'pos.checkout was not replaced intact';
  END IF;
END $$;

COMMIT;
