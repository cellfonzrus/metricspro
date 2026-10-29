-- 1102_closing_feed_counterpart.sql
-- A DECLARED CATEGORY CAN NAME WHAT IN THE SALES FEED SHOULD AGREE WITH IT.
--
-- Owner ask 2026-10-29 (Sanjot, verbatim): "Add a field in as no of edge for total and no of
-- financicing in thr daily closing for all employees to declare along with the $amount of edge and
-- acima as they get paid for each transcoding they do, this would then be checked with our system
-- if our system is capturing the sales as declared by the sales rep".
--
-- THE HALF THAT ALREADY EXISTED. The declaration itself needs no schema and no code: since mig 1072
-- a category declares that it is measured in BOTH units and dollars (`value_kind='both'`) and is
-- declared per EMPLOYEE (`declared_by='employee'`), and `ClosingSubmitForm` already renders a `#`
-- box and a `$` box per category per person off `closing/metric_catalog`. "Edge" and "Acima" are
-- therefore CONFIG ROWS (RULE TWO), which is why this migration adds no column for either.
--
-- THE CLASS THIS FIXES. What had no home was the LINK. A declared category could not say WHAT in
-- the sales feed answers the same question, so the only declared-vs-captured check in the platform
-- was `closing/router`'s activation/upgrade count recon — hard-wired to two feed figures, unable to
-- check a new category at all and unable to be reused. One column names the counterpart; ONE
-- reconciler (`closing/declared_feed_recon.py`) serves every category that has one, the
-- activation/upgrade pair included, so there is one path and not two.
--
-- THE COUNTERPART DEREFERENCES, IT DOES NOT RESTATE. `financing:<vendor_key>` points at the
-- tenant's own `commcalc.financing_vendor` row (mig 272), which already answers "is this line
-- financed, and by whom" — keyed on the TENDER, because Edge is how the customer paid and not a
-- Motorola model. `recon_class:<class>` points at the B2B buckets mig 501 already defines. No
-- keyword list is created here, and nothing is per-tenant in code.
--
-- DOCTRINE: additive / idempotent / REVERT-noted. The column is NULLABLE with no default, and NULL
-- means "this category has no counterpart and is not reconciled" — which is every existing row, so
-- no tenant moves and no surface changes until an admin picks one.
--
-- NOT MONEY-TOUCHING AS APPLIED: one nullable config column. It computes nothing, pays nothing and
-- blocks no close; the report it feeds is informational, exactly as mig 501's count recon is.
--
-- REVERT:
--   alter table commcalc.closing_count_field_def drop column if exists feed_counterpart;
--   alter table commcalc.closing_count_field_def drop column if exists feed_units_tolerance;
--   alter table commcalc.closing_count_field_def drop column if exists feed_amount_tolerance;

-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- 1) The link, and the tenant's tolerance on each axis.
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
alter table commcalc.closing_count_field_def
  add column if not exists feed_counterpart      text,
  add column if not exists feed_units_tolerance  integer,
  add column if not exists feed_amount_tolerance numeric;

comment on column commcalc.closing_count_field_def.feed_counterpart is
  'What in the sales feed answers the same question as this declared category, as
   ''<kind>:<ref>''. ''financing:<vendor_key>'' dereferences commcalc.financing_vendor (mig 272);
   ''recon_class:activation''/''recon_class:upgrade'' dereference the B2B count buckets (mig 501).
   NULL = this category has no counterpart and is not reconciled, which is NOT an error: a mailbox
   rental has no answer in a sales feed, and comparing it against zero would invent a finding.
   Parsed in ONE place — closing/declared_feed_recon.parse_counterpart — which REFUSES an unknown
   kind rather than guessing, so a mistyped value reads as "not reconciled" and never as "the rep
   declared sales we did not capture".';
comment on column commcalc.closing_count_field_def.feed_units_tolerance is
  'How many units the declaration may differ from the feed before it is a finding. NULL = exact.';
comment on column commcalc.closing_count_field_def.feed_amount_tolerance is
  'How many dollars the declaration may differ from the feed before it is a finding. NULL = exact.';

-- A value this platform cannot parse must be impossible to STORE, not merely ignored on read: a
-- silently-unreconciled category is a check an owner believes is running and is not.
do $$ begin
  alter table commcalc.closing_count_field_def
    add constraint closing_count_field_def_feed_counterpart_ck
    check (feed_counterpart is null
           or feed_counterpart ~ '^financing:[A-Za-z0-9_.-]+$'
           or feed_counterpart in ('recon_class:activation', 'recon_class:upgrade'));
exception when duplicate_object then null; end $$;

-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- 2) A migration grants what it touches (house rule, index §CORE-13). The table predates mig 501's
--    grant era in some projections, so the grant is restated idempotently. service_role ONLY — the
--    browser never reads this table, and an anon grant would widen exposure rather than fix
--    anything.
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
grant select, insert, update, delete on commcalc.closing_count_field_def to service_role;
