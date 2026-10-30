-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- 1108 — THE CASH / CREDIT MISMATCH AN EDIT LEAVES BEHIND, LOGGED (index §POS-17)
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
--
-- OWNER ASK 2026-10-30, verbatim:
--   "i need the pos POS — Cash Register to have the ability to edit the sale from the prior day and
--    create log of the cash or credit mismatch"
--
-- Editing a prior-day sale already worked when he asked: §POS-12 (mig 1103) shipped the edit and its
-- window is DECLARED, with a house default of one day that already admits yesterday. This migration
-- is the second half of the sentence — the half that did not exist.
--
-- THE CLASS, NOT THE INSTANCE. `pos.sale_payments` records what was taken. `pos.sales.total` records
-- what is owed. NOTHING in the platform ever compared the two, so BOTH writes that can move them
-- apart left money unaccounted for and unlogged:
--   · pos.amend_sale (mig 1103) restates the totals — what is owed moves, what was taken does not.
--   · pos.void_sale  (mig 725)  stands the sale down  — nothing is owed, and the money is still in.
-- The one home `backend/app/modules/pos/sale_settlement.py` answers for both. This migration gives
-- the AMEND path somewhere to file its answer; the VOID path is covered by the same one home read
-- live on the report, which is why mig 725's function is deliberately NOT touched here (its stock
-- and money handling is correct and reopening it would risk money to add a column it does not need).
--
-- WHAT THIS CHANGES, AND WHAT IT DOES NOT
--   · It ADDS nullable columns to pos.sale_amendments — mig 1103's OWN table, extended. No sibling
--     table is created: the mismatch is a fact ABOUT an amendment and belongs on its row.
--   · It REPLACES pos.amend_sale at its EXISTING signature. No parameter is added and no overload is
--     created, so a backend deployed before this migration keeps working and simply files no tender
--     columns. The tender record travels inside p_sale_patch under `tender`, alongside the
--     change_summary and change_detail that already travel there.
--   · NO EXISTING ROW IS TOUCHED. No sale moves, no total changes, no figure on any report moves.
--     The 4,605 sales and 2 payment rows live today are left exactly as they are, and every existing
--     pos.sale_amendments row (there are none) would keep NULL tender columns, which read as
--     "not measured" and never as "the tender matched".
--
-- 💰 MOVES NO MONEY. Additive schema + one function body. Books nothing and pays nothing.
--
-- Idempotent: safe to run twice.
--
-- REVERT:
--   alter table pos.sale_amendments
--     drop column if exists tender_total,       drop column if exists tender_expected,
--     drop column if exists tender_variance,    drop column if exists tender_verdict,
--     drop column if exists tender_direction,   drop column if exists tender_measured,
--     drop column if exists tender_methods,     drop column if exists tender_reason,
--     drop column if exists tender_attribution, drop column if exists settled_at,
--     drop column if exists settled_by,         drop column if exists settled_method,
--     drop column if exists settled_amount,     drop column if exists settled_note;
--   -- then re-run mig 1103 to restore pos.amend_sale's previous body.

-- ── 1) the log columns, on mig 1103's own table ─────────────────────────────────────────────────
-- Every one is NULLABLE and every one is NULL for an unmeasured comparison. A variance stored as
-- 0.00 when nothing could be measured would read as "the tender matched" on the log and on the
-- report, which is the fallback-basis defect index §66 already closed on the commission side: a
-- placement of last resort must never read as evidence.
alter table pos.sale_amendments
  add column if not exists tender_total       numeric(12,2),   -- what was tendered, as recorded
  add column if not exists tender_expected    numeric(12,2),   -- total − balance AFTER the edit
  add column if not exists tender_variance    numeric(12,2),   -- tendered − expected; NEGATIVE = short
  add column if not exists tender_verdict     text,            -- deposit_recon.status_for: short|over|ok
  add column if not exists tender_direction   text,            -- collect | refund | settled
  add column if not exists tender_measured    boolean,         -- NULL/false = no figure may be read
  add column if not exists tender_methods     jsonb,           -- the methods it was tendered on
  add column if not exists tender_reason      text,            -- why it could not be measured
  add column if not exists tender_attribution jsonb,           -- which method, or that it is ambiguous
  -- How the mismatch was SETTLED, when somebody settles it. Separate from the measurement above so
  -- the log never loses what the till saw at the moment of the edit.
  add column if not exists settled_at         timestamptz,
  add column if not exists settled_by         text,            -- storeops employee_id
  add column if not exists settled_method     text,            -- the tenant's own tender word
  add column if not exists settled_amount     numeric(12,2),
  add column if not exists settled_note       text;

-- The words the verdict and the direction may take. A CHECK rather than a lookup table: these are
-- the platform's existing vocabularies (deposit_recon's three states, and §POS-17's three
-- directions), so a fourth spelling arriving here is a defect, not a tenant's choice.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'pos_sale_amend_tender_verdict_ck') then
    alter table pos.sale_amendments add constraint pos_sale_amend_tender_verdict_ck
      check (tender_verdict is null or tender_verdict in ('short', 'over', 'ok'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'pos_sale_amend_tender_dir_ck') then
    alter table pos.sale_amendments add constraint pos_sale_amend_tender_dir_ck
      check (tender_direction is null or tender_direction in ('collect', 'refund', 'settled'));
  end if;
  -- A row may not carry BOTH a reason it could not be measured AND a variance. That is the
  -- un-pickable-fallback rule as a database constraint, so no later writer can file both and let a
  -- reader choose whichever suits it.
  if not exists (select 1 from pg_constraint where conname = 'pos_sale_amend_tender_either_ck') then
    alter table pos.sale_amendments add constraint pos_sale_amend_tender_either_ck
      check (tender_reason is null or tender_variance is null);
  end if;
end $$;

-- The log is read by store and by day, newest first — the shape the report asks for.
create index if not exists pos_sale_amend_tender_open
  on pos.sale_amendments (org_id, created_at desc)
  where tender_variance is not null and settled_at is null;

-- A migration grants what it touches. The backend reaches every relation through PostgREST, and
-- service_role only: the browser never reads this table, so an anon grant would widen exposure
-- rather than fix anything (enforced by harness_data_api_grant_guard.py, index §CORE-13).
grant select, insert, update, delete on pos.sale_amendments to service_role;

comment on column pos.sale_amendments.tender_variance is
  'tendered − expected at the moment of this edit; NEGATIVE is short, as on every other recon '
  'surface. NULL means the comparison was not measurable — never that it matched (index §POS-17).';

-- ── 2) pos.amend_sale files the tender record with the superseded version ───────────────────────
-- SAME SIGNATURE, so no overload is created and a backend deployed before this migration keeps
-- working. Everything else in this function is mig 1103's body unchanged: the snapshot still goes in
-- first, stock is still reversed by void_sale's loop and re-applied by checkout's insert, and the
-- header is still restated from the one home's figures.
create or replace function pos.amend_sale(
  p_org       uuid,
  p_sale      uuid,
  p_sale_patch jsonb,      -- the restated header: subtotal / discount_total / tax_total / total
  p_items     jsonb,       -- the NEW full set of lines, already priced by the one home
  p_employee  text default null,
  p_reason    text default null
)
returns pos.sales
language plpgsql
set search_path = pos, pg_temp
as $$
declare
  s             pos.sales;
  it            record;
  itj           jsonb;
  v_before      jsonb;
  v_items       jsonb;
  v_revision    integer;
  v_total_before numeric(12,2);
  v_tender      jsonb;
begin
  if p_org is null then
    raise exception 'org is required';
  end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then
    -- Emptying a sale is a VOID, and the void path is the one that leaves a record. An edit must
    -- never become a second, silent way to cancel a sale.
    raise exception 'a sale needs at least one item — void it instead of emptying it';
  end if;

  -- FOR UPDATE: two cashiers editing one sale would otherwise both file revision N and the second
  -- insert would fail on the unique key AFTER its stock had moved.
  select * into s from pos.sales where id = p_sale and org_id = p_org for update;
  if s.id is null then
    raise exception 'sale not found';
  end if;
  if s.status = 'voided' or s.voided_at is not null then
    raise exception 'transaction #% is voided and cannot be edited', s.transaction_id;
  end if;

  v_revision := greatest(coalesce(s.revision, 1), 1);
  v_total_before := s.total;

  -- (a) FILE THE SNAPSHOT FIRST. Everything after this can fail and the superseded version is still
  --     on record. `to_jsonb(s)` is the row as it stands, figures included.
  select coalesce(jsonb_agg(to_jsonb(i) order by i.created_at), '[]'::jsonb)
    into v_items
    from pos.sale_items i
   where i.sale_id = p_sale;

  v_before := to_jsonb(s);

  -- THE TENDER RECORD TRAVELS IN THE PATCH (mig 1108), under `tender`. The signature does not
  -- change and no overload is created, so a backend deployed before this migration still calls this
  -- function successfully and simply files no tender columns. `pos/sale_settlement.record` is the
  -- ONE home that builds this object; nothing is re-derived here, because a second derivation in
  -- plpgsql is exactly the sibling the house rules forbid.
  v_tender := coalesce(p_sale_patch->'tender', '{}'::jsonb);

  insert into pos.sale_amendments (org_id, sale_id, revision, transaction_id, before_sale,
                                   before_items, change_summary, change_detail, total_before,
                                   total_after, reason, amended_by,
                                   tender_total, tender_expected, tender_variance, tender_verdict,
                                   tender_direction, tender_measured, tender_methods,
                                   tender_reason, tender_attribution)
  values (p_org, p_sale, v_revision, s.transaction_id, v_before, v_items,
          nullif(btrim(coalesce(p_sale_patch->>'change_summary', '')), ''),
          p_sale_patch->'change_detail',
          v_total_before,
          coalesce((p_sale_patch->>'total')::numeric, 0),
          nullif(btrim(coalesce(p_reason, '')), ''),
          nullif(btrim(coalesce(p_employee, '')), ''),
          -- NULL, never 0.00, when the one home could not measure it: an unmeasured figure that
          -- stored as zero would read on the log as "the tender matched" (index §POS-17).
          (v_tender->>'tender_total')::numeric,
          (v_tender->>'tender_expected')::numeric,
          (v_tender->>'tender_variance')::numeric,
          nullif(btrim(coalesce(v_tender->>'tender_verdict', '')), ''),
          nullif(btrim(coalesce(v_tender->>'tender_direction', '')), ''),
          (v_tender->>'tender_measured')::boolean,
          case when jsonb_typeof(v_tender->'tender_methods') = 'array'
               then v_tender->'tender_methods' else null end,
          nullif(btrim(coalesce(v_tender->>'tender_reason', '')), ''),
          v_tender->'tender_attribution');

  -- (b) REVERSE the old lines — the pos.void_sale loop, unchanged in substance, so stock comes back
  --     exactly as it would if the sale had been voided.
  for it in select * from pos.sale_items where sale_id = p_sale loop
    if it.serial_number is not null and btrim(it.serial_number) <> '' then
      update pos.inventory_serial i
         set status = 'in_stock', sold_at = null, sold_in_sale_id = null, updated_at = now()
       where i.org_id = s.org_id
         and i.serial_number = it.serial_number
         and i.sold_in_sale_id = p_sale;
    elsif s.store_code is not null then
      update pos.inventory_standard st
         set qty_on_hand = st.qty_on_hand + coalesce(it.qty, 0), updated_at = now()
       where st.org_id = s.org_id
         and st.product_id = it.product_id
         and st.store_code = s.store_code;
    end if;
  end loop;

  delete from pos.sale_items where sale_id = p_sale;

  -- (c) APPLY the new lines — the pos.checkout insert, so the same inventory trigger takes stock in
  --     the same way. A new line with no stock raises here and the WHOLE edit rolls back.
  for itj in select * from jsonb_array_elements(p_items) loop
    insert into pos.sale_items (org_id, sale_id, product_id, product_type, description,
                                serial_number, qty, unit_price, list_price, cost, discount,
                                tax_rate, tax_value, extended_price)
    values (
      p_org, p_sale,
      (itj->>'product_id')::uuid,
      nullif(itj->>'product_type', ''),
      nullif(itj->>'description', ''),
      nullif(btrim(coalesce(itj->>'serial_number', '')), ''),
      coalesce((itj->>'qty')::int, 1),
      coalesce((itj->>'unit_price')::numeric, 0),
      (itj->>'list_price')::numeric,
      (itj->>'cost')::numeric,
      coalesce((itj->>'discount')::numeric, 0),
      coalesce((itj->>'tax_rate')::numeric, 0),
      coalesce((itj->>'tax_value')::numeric, 0),
      coalesce((itj->>'extended_price')::numeric, 0)
    );
  end loop;

  -- (d) RESTATE the header. Only the figures and the revision stamps move: the transaction number,
  --     the store, the rep who rang it and the original timestamp are the sale's identity and are
  --     deliberately left alone.
  update pos.sales
     set subtotal       = coalesce((p_sale_patch->>'subtotal')::numeric, subtotal),
         discount_total = coalesce((p_sale_patch->>'discount_total')::numeric, discount_total),
         tax_total      = coalesce((p_sale_patch->>'tax_total')::numeric, tax_total),
         total          = coalesce((p_sale_patch->>'total')::numeric, total),
         customer_id    = coalesce(nullif(p_sale_patch->>'customer_id', '')::uuid, customer_id),
         notes          = coalesce(nullif(p_sale_patch->>'notes', ''), notes),
         revision       = v_revision + 1,
         amended_at     = now(),
         amended_by     = nullif(btrim(coalesce(p_employee, '')), ''),
         updated_at     = now()
   where id = p_sale
   returning * into s;

  return s;
end;
$$;
comment on function pos.amend_sale(uuid, uuid, jsonb, jsonb, text, text) is
  'Edit a rung sale in place, keeping its transaction number (index §POS-12). Files the superseded '
  'version into pos.sale_amendments, reverses the old lines exactly as pos.void_sale does, applies '
  'the new ones exactly as pos.checkout does, and restates the header — all in one transaction. The '
  'caller prices the lines and computes the totals through pos/sale_amendment.py, and the cash / '
  'credit mismatch the edit leaves behind through pos/sale_settlement.py, passed in p_sale_patch '
  'under `tender` (mig 1108, index §POS-17).';
