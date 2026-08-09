-- 741_pos_tax_code_market_scope.sql   (platform-core band 700-799; pos schema)
--
-- OWNER 2026-08-09: "option for new tax code should also have market in pos settings to assign the
-- same tax code to that market and option to select multiple stores from the drop down menu".
--
-- MODEL. pos.tax_codes already scopes by store_code, with NULL meaning org-wide. This adds MARKET as
-- the middle rung, giving the platform's standard three-level scope — the same all / market / store
-- vocabulary RBAC and reporting already use — resolved most-specific-first:
--
--     store_code match   ->  beats
--     market match       ->  beats
--     org-wide (both NULL)
--
-- WHY A RESOLVER, NOT JUST A COLUMN. pos.checkout does NOT compute tax: it stores the tax_total it
-- is handed. So the rate is chosen before checkout, and a scope column with no single agreed
-- resolution is how two callers end up disagreeing and a customer is charged the wrong tax. The
-- precedence above is therefore implemented in exactly ONE place
-- (GET /pos/tax-codes/resolve) and this migration only stores the scope.
--
-- Additive + idempotent. Charges nothing on its own: every existing row keeps market NULL and
-- resolves exactly as it does today.

begin;

alter table pos.tax_codes
    add column if not exists market text;

comment on column pos.tax_codes.market is
    'Market this rate applies to (storeops.stores.market). Precedence is store_code > market > '
    'org-wide (both NULL). Set market to give one rate to every store in a market without creating '
    'a row per store; a store_code row still overrides it for that one store.';

-- Partial indexes: each scope level is looked up on its own, and a rate row is small in number but
-- read on every sale, so the lookups stay index-only.
create index if not exists tax_codes_store_scope_idx
    on pos.tax_codes (org_id, store_code) where store_code is not null;
create index if not exists tax_codes_market_scope_idx
    on pos.tax_codes (org_id, market) where market is not null;

-- Guard the one shape that would make precedence meaningless: a row claiming BOTH a store and a
-- market. Such a row is neither scope and would resolve differently depending on which query found
-- it first. Rejected at the database so no code path can create one.
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'tax_codes_one_scope_ck') then
        alter table pos.tax_codes
            add constraint tax_codes_one_scope_ck
            check (store_code is null or market is null);
    end if;
end $$;

commit;
