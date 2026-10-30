-- 1106_pos_terms_tender.sql
-- THE TERMS TENDER — a register may take a sale on account (owner ask 2026-10-30, index §POS-15).
--
-- Owner, verbatim: *"also add another tender type as Terms . 15/30/60"*.
--
-- WHAT THIS CHANGES, AND WHY EACH PART IS HERE
--
--  1. `pos.sale_payments.payment_method` carried a CHECK list of five methods (mig 725). That list
--     was the SECOND copy of a fact the browser also held as an array of buttons; it is now
--     declared once in `backend/app/modules/pos/tender_vocabulary.py` and this constraint is
--     GENERATED from it (`harness_pos_tender_vocabulary.py` §E fails the build if the two drift).
--     So this file widens the list by exactly one value: `terms`.
--
--  2. A terms payment carries two facts an immediate one does not: the agreed day count, and the
--     date the money falls due. They go on the payment row, not on the sale, because a sale may one
--     day be split across two tenders and it is the TERMS leg that falls due.
--
--  3. `pos.checkout` (mig 725, last replaced by 745) inserted a payment row without those two
--     columns, so it is replaced here to carry them. Nothing else about it changes — the item loop,
--     the figures it stores and the inventory trigger it fires are byte-for-byte the 745 body.
--
-- NO MONEY MOVES AND NOTHING IS BACKFILLED. Every existing payment row keeps a NULL day count and a
-- NULL due date, which reads correctly as "this was not a terms sale". No stored total, balance or
-- commission figure is touched by this file.
--
-- WHAT A TENANT HAS TO DECLARE: nothing. The Terms tender is on for every org by default with the
-- owner's three day counts (15 / 30 / 60), and an org that does not sell on account switches it off
-- with the `tender_terms_enabled` register setting — a row, never a code branch (RULE TWO).
--
-- REVERT: notes at the foot of the file.

-- ── 1 + 2. the payment row ──────────────────────────────────────────────────────────────────────
alter table pos.sale_payments add column if not exists terms_days integer;
alter table pos.sale_payments add column if not exists due_date date;

comment on column pos.sale_payments.terms_days is
  'Agreed payment term in days for a terms tender (15/30/60 by house default, declared per org on '
  'pos.pos_settings.tender_terms_day_options). NULL on every tender that settled at the till.';
comment on column pos.sale_payments.due_date is
  'When a terms tender falls due = the SALE''s date + terms_days (pos/tender_vocabulary.due_date). '
  'NULL on every tender that settled at the till.';

do $$
begin
  if exists (select 1 from pg_constraint
              where conrelid = 'pos.sale_payments'::regclass
                and conname = 'sale_payments_payment_method_check') then
    alter table pos.sale_payments drop constraint sale_payments_payment_method_check;
  end if;
  alter table pos.sale_payments
    add constraint sale_payments_payment_method_check
    check (payment_method in ('cash','credit_card','debit_card','check','card_external','terms'));
end $$;

-- A terms leg without its day count, or an immediate tender carrying one, is a row no reader can
-- make sense of. The database refuses both rather than storing a sale nobody can explain.
do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'pos.sale_payments'::regclass
                    and conname = 'sale_payments_terms_shape_check') then
    alter table pos.sale_payments
      add constraint sale_payments_terms_shape_check
      check ((payment_method = 'terms' and terms_days is not null and due_date is not null)
          or (payment_method <> 'terms' and terms_days is null and due_date is null));
  end if;
end $$;

create index if not exists pos_sale_payments_due
  on pos.sale_payments(org_id, due_date)
  where payment_method = 'terms';

-- mig 725 created pos.sale_payments before Supabase stopped granting new public tables; it is in
-- the `pos` schema and already granted. Re-stated here because a migration grants what it touches
-- and a repeated grant is harmless (CLAUDE.md house conventions, index §CORE-13).
grant select, insert, update, delete on pos.sale_payments to service_role;

-- ── 3. the checkout function, carrying the two new columns ──────────────────────────────────────
create or replace function pos.checkout(p_org uuid, p_sale jsonb, p_items jsonb, p_payments jsonb)
 returns pos.sales
 language plpgsql
 set search_path to 'pos', 'pg_temp'
as $function$
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
                                     card_last_four, check_number, terms_days, due_date)
      VALUES (
        p_org, v_sale.id,
        pay->>'payment_method',
        COALESCE((pay->>'amount')::numeric, 0),
        NULLIF(pay->>'card_last_four', ''),
        NULLIF(pay->>'check_number', ''),
        NULLIF(pay->>'terms_days', '')::int,
        NULLIF(pay->>'due_date', '')::date
      );
    END LOOP;
  END IF;

  RETURN v_sale;
END;
$function$;

select '1106 complete — the Terms tender is accepted, with its day count and due date' as status;

-- REVERT:
--   drop index if exists pos.pos_sale_payments_due;
--   alter table pos.sale_payments drop constraint if exists sale_payments_terms_shape_check;
--   alter table pos.sale_payments drop constraint if exists sale_payments_payment_method_check;
--   alter table pos.sale_payments add constraint sale_payments_payment_method_check
--     check (payment_method in ('cash','credit_card','debit_card','check','card_external'));
--   -- only once no terms rows remain:
--   alter table pos.sale_payments drop column if exists terms_days;
--   alter table pos.sale_payments drop column if exists due_date;
--   -- and re-apply the pos.checkout body from 745_pos_syscat_config_and_list_price.sql.
