-- MIGRATION 728: TENANT-SCOPED FOREIGN KEYS FOR THE POS SCHEMA
-- Band 700-799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- WHY THIS EXISTS. Migrations 725/726 created the POS schema with single-column foreign keys
-- (`sale_items.sale_id -> pos.sales(id)`). Every table carries `org_id NOT NULL`, but NOTHING at the
-- database level required a child row and its parent to belong to the SAME tenant. A bug, a bad
-- import, or a mis-scoped write could link one tenant's sale line to another tenant's sale and the
-- database would accept it.
--
-- This is not hypothetical here. On 2026-07-14 a Luxelink B2B export was ingested under the HOUSE
-- org; six line items for a Luxelink store landed in house `commcalc.raw_sales` and were re-inserted
-- hourly for three weeks before anyone noticed. Migration 280 added the ingest guard as the control
-- for the FEED. This migration is the equivalent control for the POS schema's OWN relationships.
--
-- WHAT IT DOES. Rewrites all 23 POS foreign keys from `(child_col) -> parent(id)` to
-- `(org_id, child_col) -> parent(org_id, id)`, adding the UNIQUE (org_id, id) keys the composite
-- references require. A cross-tenant link now fails at the database, not in review.
--
-- DELETE SEMANTICS ARE PRESERVED EXACTLY. `ON DELETE CASCADE` stays CASCADE. The five
-- `ON DELETE SET NULL` keys use PostgreSQL 15+ column-list syntax -- `ON DELETE SET NULL (col)` --
-- so only the referencing column is nulled. A plain composite SET NULL would try to null `org_id`
-- too, which is NOT NULL, and would fail at delete time. Verified: prod is PostgreSQL 17.6.
--
-- SAFE NOW, EXPENSIVE LATER. Every POS table is EMPTY at the time of writing (verified: pos.sales,
-- pos.products, pos.customers, pos.pos_settings all 0 rows), so the rewrite validates instantly and
-- cannot fail on existing data. Once these tables carry real sales this becomes a migration with a
-- validation scan and a genuine chance of tripping on legacy rows.
--
-- NO BEHAVIOUR CHANGE. No column is added, dropped or retyped; no row is written. Application code
-- that inserts correctly scoped rows sees no difference.

-- Guard: refuse to run if a cross-tenant link already exists, so the failure is a clear message
-- rather than a raw constraint violation halfway through.
DO $guard$
DECLARE bad BIGINT;
BEGIN
  SELECT count(*) INTO bad FROM pos.sale_items c JOIN pos.sales p ON p.id = c.sale_id
   WHERE p.org_id <> c.org_id;
  IF bad > 0 THEN
    RAISE EXCEPTION 'Migration 728 aborted: % cross-tenant sale_items->sales links already exist. Resolve these before scoping the keys.', bad;
  END IF;
END $guard$;

-- 1. UNIQUE (org_id, id) on every parent -- the target a composite FK requires.


DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'activations_org_id_uniq' AND conrelid = 'pos.activations'::regclass) THEN
    ALTER TABLE pos.activations ADD CONSTRAINT activations_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'categories_org_id_uniq' AND conrelid = 'pos.categories'::regclass) THEN
    ALTER TABLE pos.categories ADD CONSTRAINT categories_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'customers_org_id_uniq' AND conrelid = 'pos.customers'::regclass) THEN
    ALTER TABLE pos.customers ADD CONSTRAINT customers_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'departments_org_id_uniq' AND conrelid = 'pos.departments'::regclass) THEN
    ALTER TABLE pos.departments ADD CONSTRAINT departments_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'products_org_id_uniq' AND conrelid = 'pos.products'::regclass) THEN
    ALTER TABLE pos.products ADD CONSTRAINT products_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'purchase_orders_org_id_uniq' AND conrelid = 'pos.purchase_orders'::regclass) THEN
    ALTER TABLE pos.purchase_orders ADD CONSTRAINT purchase_orders_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sales_org_id_uniq' AND conrelid = 'pos.sales'::regclass) THEN
    ALTER TABLE pos.sales ADD CONSTRAINT sales_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'service_plans_org_id_uniq' AND conrelid = 'pos.service_plans'::regclass) THEN
    ALTER TABLE pos.service_plans ADD CONSTRAINT service_plans_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'store_transfers_org_id_uniq' AND conrelid = 'pos.store_transfers'::regclass) THEN
    ALTER TABLE pos.store_transfers ADD CONSTRAINT store_transfers_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'vendors_org_id_uniq' AND conrelid = 'pos.vendors'::regclass) THEN
    ALTER TABLE pos.vendors ADD CONSTRAINT vendors_org_id_uniq UNIQUE (org_id, id);
  END IF;
END $$;


-- 2. Rewrite each foreign key as (org_id, col) -> parent (org_id, id).

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'activation_notes_activation_id_fkey' AND conrelid = 'pos.activation_notes'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.activation_notes DROP CONSTRAINT activation_notes_activation_id_fkey;
    ALTER TABLE pos.activation_notes ADD CONSTRAINT activation_notes_activation_id_fkey
      FOREIGN KEY (org_id, activation_id) REFERENCES pos.activations (org_id, id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'activations_customer_id_fkey' AND conrelid = 'pos.activations'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.activations DROP CONSTRAINT activations_customer_id_fkey;
    ALTER TABLE pos.activations ADD CONSTRAINT activations_customer_id_fkey
      FOREIGN KEY (org_id, customer_id) REFERENCES pos.customers (org_id, id) ON DELETE SET NULL (customer_id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'activations_sale_id_fkey' AND conrelid = 'pos.activations'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.activations DROP CONSTRAINT activations_sale_id_fkey;
    ALTER TABLE pos.activations ADD CONSTRAINT activations_sale_id_fkey
      FOREIGN KEY (org_id, sale_id) REFERENCES pos.sales (org_id, id) ON DELETE SET NULL (sale_id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'activations_service_plan_id_fkey' AND conrelid = 'pos.activations'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.activations DROP CONSTRAINT activations_service_plan_id_fkey;
    ALTER TABLE pos.activations ADD CONSTRAINT activations_service_plan_id_fkey
      FOREIGN KEY (org_id, service_plan_id) REFERENCES pos.service_plans (org_id, id) ON DELETE SET NULL (service_plan_id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'categories_department_id_fkey' AND conrelid = 'pos.categories'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.categories DROP CONSTRAINT categories_department_id_fkey;
    ALTER TABLE pos.categories ADD CONSTRAINT categories_department_id_fkey
      FOREIGN KEY (org_id, department_id) REFERENCES pos.departments (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'customer_notes_customer_id_fkey' AND conrelid = 'pos.customer_notes'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.customer_notes DROP CONSTRAINT customer_notes_customer_id_fkey;
    ALTER TABLE pos.customer_notes ADD CONSTRAINT customer_notes_customer_id_fkey
      FOREIGN KEY (org_id, customer_id) REFERENCES pos.customers (org_id, id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'inventory_serial_product_id_fkey' AND conrelid = 'pos.inventory_serial'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.inventory_serial DROP CONSTRAINT inventory_serial_product_id_fkey;
    ALTER TABLE pos.inventory_serial ADD CONSTRAINT inventory_serial_product_id_fkey
      FOREIGN KEY (org_id, product_id) REFERENCES pos.products (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pos_inv_serial_sold_sale_fk' AND conrelid = 'pos.inventory_serial'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.inventory_serial DROP CONSTRAINT pos_inv_serial_sold_sale_fk;
    ALTER TABLE pos.inventory_serial ADD CONSTRAINT pos_inv_serial_sold_sale_fk
      FOREIGN KEY (org_id, sold_in_sale_id) REFERENCES pos.sales (org_id, id) ON DELETE SET NULL (sold_in_sale_id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'inventory_standard_product_id_fkey' AND conrelid = 'pos.inventory_standard'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.inventory_standard DROP CONSTRAINT inventory_standard_product_id_fkey;
    ALTER TABLE pos.inventory_standard ADD CONSTRAINT inventory_standard_product_id_fkey
      FOREIGN KEY (org_id, product_id) REFERENCES pos.products (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'products_category_id_fkey' AND conrelid = 'pos.products'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.products DROP CONSTRAINT products_category_id_fkey;
    ALTER TABLE pos.products ADD CONSTRAINT products_category_id_fkey
      FOREIGN KEY (org_id, category_id) REFERENCES pos.categories (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'products_department_id_fkey' AND conrelid = 'pos.products'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.products DROP CONSTRAINT products_department_id_fkey;
    ALTER TABLE pos.products ADD CONSTRAINT products_department_id_fkey
      FOREIGN KEY (org_id, department_id) REFERENCES pos.departments (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'purchase_order_items_po_id_fkey' AND conrelid = 'pos.purchase_order_items'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.purchase_order_items DROP CONSTRAINT purchase_order_items_po_id_fkey;
    ALTER TABLE pos.purchase_order_items ADD CONSTRAINT purchase_order_items_po_id_fkey
      FOREIGN KEY (org_id, po_id) REFERENCES pos.purchase_orders (org_id, id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'purchase_order_items_product_id_fkey' AND conrelid = 'pos.purchase_order_items'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.purchase_order_items DROP CONSTRAINT purchase_order_items_product_id_fkey;
    ALTER TABLE pos.purchase_order_items ADD CONSTRAINT purchase_order_items_product_id_fkey
      FOREIGN KEY (org_id, product_id) REFERENCES pos.products (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'purchase_orders_vendor_id_fkey' AND conrelid = 'pos.purchase_orders'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.purchase_orders DROP CONSTRAINT purchase_orders_vendor_id_fkey;
    ALTER TABLE pos.purchase_orders ADD CONSTRAINT purchase_orders_vendor_id_fkey
      FOREIGN KEY (org_id, vendor_id) REFERENCES pos.vendors (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sale_items_product_id_fkey' AND conrelid = 'pos.sale_items'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.sale_items DROP CONSTRAINT sale_items_product_id_fkey;
    ALTER TABLE pos.sale_items ADD CONSTRAINT sale_items_product_id_fkey
      FOREIGN KEY (org_id, product_id) REFERENCES pos.products (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sale_items_sale_id_fkey' AND conrelid = 'pos.sale_items'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.sale_items DROP CONSTRAINT sale_items_sale_id_fkey;
    ALTER TABLE pos.sale_items ADD CONSTRAINT sale_items_sale_id_fkey
      FOREIGN KEY (org_id, sale_id) REFERENCES pos.sales (org_id, id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sale_payments_sale_id_fkey' AND conrelid = 'pos.sale_payments'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.sale_payments DROP CONSTRAINT sale_payments_sale_id_fkey;
    ALTER TABLE pos.sale_payments ADD CONSTRAINT sale_payments_sale_id_fkey
      FOREIGN KEY (org_id, sale_id) REFERENCES pos.sales (org_id, id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sales_customer_id_fkey' AND conrelid = 'pos.sales'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.sales DROP CONSTRAINT sales_customer_id_fkey;
    ALTER TABLE pos.sales ADD CONSTRAINT sales_customer_id_fkey
      FOREIGN KEY (org_id, customer_id) REFERENCES pos.customers (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'store_transfer_items_product_id_fkey' AND conrelid = 'pos.store_transfer_items'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.store_transfer_items DROP CONSTRAINT store_transfer_items_product_id_fkey;
    ALTER TABLE pos.store_transfer_items ADD CONSTRAINT store_transfer_items_product_id_fkey
      FOREIGN KEY (org_id, product_id) REFERENCES pos.products (org_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'store_transfer_items_transfer_id_fkey' AND conrelid = 'pos.store_transfer_items'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.store_transfer_items DROP CONSTRAINT store_transfer_items_transfer_id_fkey;
    ALTER TABLE pos.store_transfer_items ADD CONSTRAINT store_transfer_items_transfer_id_fkey
      FOREIGN KEY (org_id, transfer_id) REFERENCES pos.store_transfers (org_id, id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_ins_activation_id_fkey' AND conrelid = 'pos.trade_ins'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.trade_ins DROP CONSTRAINT trade_ins_activation_id_fkey;
    ALTER TABLE pos.trade_ins ADD CONSTRAINT trade_ins_activation_id_fkey
      FOREIGN KEY (org_id, activation_id) REFERENCES pos.activations (org_id, id) ON DELETE SET NULL (activation_id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_ins_customer_id_fkey' AND conrelid = 'pos.trade_ins'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.trade_ins DROP CONSTRAINT trade_ins_customer_id_fkey;
    ALTER TABLE pos.trade_ins ADD CONSTRAINT trade_ins_customer_id_fkey
      FOREIGN KEY (org_id, customer_id) REFERENCES pos.customers (org_id, id) ON DELETE SET NULL (customer_id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'trade_ins_sale_id_fkey' AND conrelid = 'pos.trade_ins'::regclass AND array_length(conkey, 1) = 1) THEN
    ALTER TABLE pos.trade_ins DROP CONSTRAINT trade_ins_sale_id_fkey;
    ALTER TABLE pos.trade_ins ADD CONSTRAINT trade_ins_sale_id_fkey
      FOREIGN KEY (org_id, sale_id) REFERENCES pos.sales (org_id, id) ON DELETE SET NULL (sale_id);
  END IF;
END $$;


SELECT 'Migration 728 complete -- 23 POS foreign keys are now tenant-scoped' AS status;
