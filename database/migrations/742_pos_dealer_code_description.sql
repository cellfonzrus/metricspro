-- 742_pos_dealer_code_description.sql   (platform-core band 700-799; pos schema)
--
-- OWNER 2026-08-09: "dealer codes are not being pulled from the reports", answered with a per-carrier
-- source map on commcalc.carrier (migration 293) and POST /pos/dealer-codes/sync-from-reports.
--
-- THE GAP THIS CLOSES. Migration 293 stores THREE things per carrier: the source table, the code
-- column, and `dealer_code_name_column` -- the carrier's own human label for that code (Total's
-- `account_name`, e.g. "Luxelink Wireless LLC" / "Novawave Communications INC"). The sync reads that
-- name, uses it in the preview... and then throws it away, because pos.dealer_codes had nowhere to
-- put it. The result on a live tenant is 20 bare six-digit numbers with no carrier label, no store
-- and no way to tell which entity each one belongs to -- so the person who has to attach each code to
-- a store cannot tell them apart, which is exactly the job the import was supposed to make possible.
--
-- Measured 2026-08-10 on Luxelink: 20 distinct account_ids across TWO legal entities
-- (Novawave 168872-169288, Luxelink 170073-170086). Without the name, those two books are
-- indistinguishable.
--
-- Additive + idempotent. Nullable, no default, no backfill: an existing hand-typed code keeps NULL
-- and every reader treats NULL as "no label", so nothing changes until a sync or an edit fills it.
-- Nothing here touches a rate, a payout or a paid/earned column.

begin;

alter table pos.dealer_codes
    add column if not exists description text;

comment on column pos.dealer_codes.description is
    'Human label for this dealer code as the CARRIER states it -- populated from the carrier''s '
    'commcalc.carrier.dealer_code_name_column when the code is harvested from report data '
    '(POST /pos/dealer-codes/sync-from-reports), or typed by hand. NULL = no label. Never used for '
    'matching: `code` remains the identity.';

commit;
