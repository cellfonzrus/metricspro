-- 1103_pos_sale_amendment.sql
-- A RUNG SALE CAN BE EDITED IN PLACE, KEEPING ITS NUMBER, AND EVERY VERSION IS KEPT.
--
-- Owner ask 2026-10-29 (Sanjot, verbatim): "im not able to edit the receipt ot add more tiems to teh
-- same rec3ipt". Offered three ways — re-ring under a new number, ring a second sale, or edit in
-- place keeping the number — he chose **edit in place, same number**, after being told plainly that
-- it rewrites a tendered record.
--
-- WHAT WAS TRUE BEFORE THIS. There was no way to amend a finished sale at all. `pos.checkout` and
-- `pos.void_sale` (mig 725) were the only two sale functions; the API had only
-- `POST /pos/sales/{id}/void`; the Sale Log's item table was display-only. So nothing here replaces
-- an existing mechanism — the duplicate check found none to extend (index §POS-02/04/07/10/11 all
-- answer receipt questions, none answers "change what was sold").
--
-- WHAT MAKES REWRITING A TENDERED RECORD ANSWERABLE: NOTHING IS OVERWRITTEN. Each edit files the
-- superseded version whole — the sale's own stored figures plus every line as stored — into
-- `pos.sale_amendments`, and bumps `pos.sales.revision`. The version the customer's printed receipt
-- was struck from therefore survives every edit, and any figure that moved can be traced to the
-- edit that moved it. Without that this would be an untraceable rewrite of money, which is the one
-- thing the house rules do not allow a defect to become.
--
-- STOCK FOLLOWS THE LINES, BY CONSTRUCTION. Mig 725 put the inventory movement on a trigger over
-- `pos.sale_items`, and `pos.void_sale` already holds the reversal. `pos.amend_sale` therefore
-- REVERSES every old line exactly as a void does and APPLIES the new ones exactly as a checkout
-- does, inside one transaction. Stock is conserved because the same two code paths that have always
-- moved it are the ones moving it here — not because a delta was computed by hand. An out-of-stock
-- new line raises inside the transaction and the whole edit rolls back, leaving the sale untouched.
--
-- THE FIGURES ARE THE SERVER'S, NOT THE BODY'S. `pos.checkout` stores the totals it is handed, which
-- is tolerable for a new sale the browser just priced. It is NOT tolerable for an edit, whose whole
-- purpose is to move those figures. So the backend re-derives every line's `tax_value` and
-- `extended_price` and the sale's four totals through `pos/sale_amendment.py` — ONE home for that
-- arithmetic, derived from the figures mig 725 already stores and reproducing them to the cent — and
-- this function records what it is given by that one caller. A client-supplied total never survives.
--
-- EACH LINE KEEPS ITS OWN TAX RATE. An edit touches a sale that legally happened, on its date, at
-- the rate in force then. An existing line keeps its stored rate; only a newly added line takes the
-- store's current one. Nothing here restates the tax of a sale whose tax was already struck.
--
-- RULE TWO: no tenant, carrier or product is named. How far back a sale may be edited is a declared
-- per-org setting (`pos.pos_settings` key `sale_reopen_window_days`, house default 1 day), enforced
-- in the backend, and this migration seeds no row — every org keeps the house default until an admin
-- changes it.
--
-- DOCTRINE: additive / idempotent / REVERT-noted. The new columns are NULLABLE with no default, and
-- a NULL `revision` reads as revision 1 (`sale_amendment.current_revision`), so every sale already
-- in the table is untouched and correct. No existing row is updated and no figure moves on apply.
--
-- MONEY-TOUCHING: this adds the ABILITY to change a recorded sale total. It writes no money itself
-- and alters no stored figure on apply. Surfaced for owner approval before applying, per the house
-- rule.
--
-- REVERT:
--   drop function if exists pos.amend_sale(uuid, uuid, jsonb, jsonb, text, text);
--   drop table if exists pos.sale_amendments;
--   alter table pos.sales drop column if exists revision;
--   alter table pos.sales drop column if exists amended_at;
--   alter table pos.sales drop column if exists amended_by;

-- ── 1) the sale carries which revision it is on, and when it last moved ──────────────────────────
alter table pos.sales add column if not exists revision integer;
alter table pos.sales add column if not exists amended_at timestamptz;
alter table pos.sales add column if not exists amended_by text;

comment on column pos.sales.revision is
  'Which version of this sale is current. NULL means never edited and reads as revision 1 '
  '(pos/sale_amendment.current_revision). Bumped by pos.amend_sale, never by hand.';
comment on column pos.sales.amended_at is
  'When this sale was last edited in place. NULL for a sale that has never been edited.';
comment on column pos.sales.amended_by is
  'storeops employee_id of whoever last edited this sale. Never a sentinel — NULL when unresolved.';

-- ── 2) every superseded version, kept whole ──────────────────────────────────────────────────────
create table if not exists pos.sale_amendments (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null,
  sale_id         uuid not null references pos.sales(id) on delete cascade,
  revision        integer not null,          -- the revision being SUPERSEDED by this edit
  transaction_id  bigint,                    -- denormalised so the history reads without a join
  -- The sale AS IT STOOD. Deliberately the stored figures and not re-derived ones: this records
  -- what the customer's receipt said, and re-deriving would record what it should have said.
  before_sale     jsonb not null,
  before_items    jsonb not null,
  -- What the edit did, as the one home computed it (added / removed / changed counts and lines).
  change_summary  text,
  change_detail   jsonb,
  -- The figures either side, so a reader sees the movement without re-adding the lines.
  total_before    numeric(12,2),
  total_after     numeric(12,2),
  reason          text,                      -- what the cashier typed, when they typed one
  amended_by      text,                      -- storeops employee_id; NULL when unresolved
  created_at      timestamptz default now(),
  unique (sale_id, revision)                 -- one record per superseded version, no double-filing
);

comment on table pos.sale_amendments is
  'One row per in-place edit of a rung sale (index §POS-12): the version it superseded, kept whole. '
  'Written only by pos.amend_sale. Never updated or deleted — an edited sale is only answerable '
  'while every version it passed through is still on file.';

create index if not exists sale_amendments_org_sale_idx
  on pos.sale_amendments (org_id, sale_id, revision desc);
create index if not exists sale_amendments_org_created_idx
  on pos.sale_amendments (org_id, created_at desc);

-- A migration grants what it creates (house rule, index §CORE-13). service_role ONLY — the browser
-- never reads this table, so an anon grant would widen exposure rather than fix anything.
grant select, insert, update, delete on pos.sale_amendments to service_role;

-- ── 3) the edit itself, atomic ───────────────────────────────────────────────────────────────────
-- Reverses the old lines (the pos.void_sale loop), applies the new ones (the pos.checkout loop) and
-- restates the header, inside ONE transaction. The caller has already priced the lines and computed
-- the totals through the one home; this function stores them and keeps the history.
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

  insert into pos.sale_amendments (org_id, sale_id, revision, transaction_id, before_sale,
                                   before_items, change_summary, change_detail, total_before,
                                   total_after, reason, amended_by)
  values (p_org, p_sale, v_revision, s.transaction_id, v_before, v_items,
          nullif(btrim(coalesce(p_sale_patch->>'change_summary', '')), ''),
          p_sale_patch->'change_detail',
          v_total_before,
          coalesce((p_sale_patch->>'total')::numeric, 0),
          nullif(btrim(coalesce(p_reason, '')), ''),
          nullif(btrim(coalesce(p_employee, '')), ''));

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
  'caller prices the lines and computes the totals through pos/sale_amendment.py.';
