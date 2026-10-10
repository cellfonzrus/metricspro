-- 1068_mdf_entitlement_config.sql
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- AN ENTITLEMENT IS A COUNT OF MONTHS PER STORE — the per-org CONFIG for the completeness check,
-- and its tile on the Management Overview dashboard (index §68, owner instruction 2026-10-10).
--
-- Run this in the Supabase SQL editor. Claude does not run SQL and has applied NOTHING of this file.
-- Additive, idempotent, safe to re-run. Numbered; higher number = later ALTER wins.
--
-- OWNER, verbatim 2026-10-10:
--   "mdf / ramp up subsidy is being finsihed it is inly for 12 months, if we did not get for 12
--    months for each store it should be flagged and the missing month and store to be poimted out ,
--    make it into management overview"
--
-- WHAT THIS IS: ONE additive jsonb column on the per-org config table that already holds 40+ such
-- blobs, plus the dashboard tile. RULE TWO — the entitlement's TERM (a declared number of months),
-- WHICH declared payment category is the entitlement, each store's FIRST entitled month and each
-- feed's column names all live HERE, as data, per org, with the code's house defaults behind them
-- (`backend/app/modules/commcalc/entitlement_coverage.py` CODE_DEFAULT). No carrier, tenant, store,
-- product or month count appears in any branch.
--
-- 💰 MOVES NO MONEY. It adds a column that drives a READ-ONLY completeness report and appends one
-- dashboard tile. No amount, rate, schedule or paid/earned column is touched, no P&L line is
-- re-booked, and no payout is recomputed. The report books nothing and writes no flag.
--
-- WHY `store_windows` IS SEEDED EMPTY, AND WHAT THAT MEANS
--   NOTHING IN ANY FEED SAYS WHEN A STORE'S ENTITLEMENT BEGAN, or that it is twelve months long.
--   That is the owner's knowledge. So the window is declared here, and a store with no declared
--   start month is reported NOT MEASURED with the reason named — never "12 months missing".
--   Inventing a window and then reporting absences against it would be a fabricated accusation
--   against the carrier, which is the worst failure available to a report like this.
--
--   MEASURED LIVE 2026-10-10, read-only, house org, so the declaration can be made with numbers.
--   `commcalc.payment_categories` already declares 'Ramp Up Subsidy' and '4.0 $1K Ramp Up Bonus'
--   into category 'MDF' (0 rows live for the second). The payment is a monthly LUMP SUM, one row per
--   store, on a single day early in the month (03-06, 04-07, 05-06, 06-08, 07-07, 08-06, 09-09,
--   10-07), with `imei` and `mdn` blank on all 44 rows — correctly identifier-less.
--
--     commcalc.raw_payment_detail   44 rows   $287,500.00   March-October 2026
--     commcalc.raw_comp_report      40 rows   $260,500.00   March-September 2026
--
--   The two agree store-for-store and dollar-for-dollar in EVERY month both carry (Mar-Jul: 6 stores
--   / $39,300 · Aug and Sep: 5 / $32,000). The whole difference is October's 4 rows / $27,000.00 not
--   yet in the statement — the known month-end lag, which is why the report reads BOTH feeds and
--   unions them rather than trusting either alone.
--
--   Per store, per month (canonical store keys from the ONE identity home, §64):
--     509 Nostrand Ave        $7,500   March-October     (still being paid)
--     151 E 170th St          $7,500   March-October     (still being paid)
--     1750 5th Ave            $7,000   March-October     (still being paid)
--     11636 Springfield Blvd  $5,000   March-October     (still being paid)
--     103 Fulton Ave          $5,000   March-SEPTEMBER   — nothing in October
--     3565 Broadway           $7,300   March-JULY        — nothing in August, September or October
--
--   Those two stores are what the owner asked to have pointed out. Until their first entitled month
--   is declared the report NAMES all four store-months and reports them NOT MEASURED, because a
--   store that stopped receiving may simply have reached the end of its term. Declaring the window
--   turns them into judged gaps.
--
--   THE DECLARATION, once the owner supplies each store's FIRST ENTITLED MONTH (he has not; the
--   months below are placeholders and this UPDATE is deliberately NOT part of the migration):
--
--     UPDATE commcalc.commission_org_config
--        SET mdf_entitlement_config =
--              COALESCE(mdf_entitlement_config, '{}'::jsonb)
--              || '{"store_windows":{"509 Nostrand Ave":"YYYY-MM",
--                                    "151 E 170th St":"YYYY-MM",
--                                    "1750 5th Ave":"YYYY-MM",
--                                    "11636 Springfield Blvd":"YYYY-MM",
--                                    "103 Fulton Ave":"YYYY-MM",
--                                    "3565 Broadway":"YYYY-MM"}}'::jsonb,
--            updated_at = now()
--      WHERE org_id = '00000000-0000-0000-0000-000000000001';
--
-- ADDITIVE AND IDEMPOTENT: `ADD COLUMN IF NOT EXISTS`, a house seed that only fills a NULL, and a
-- jsonb tile APPEND guarded by `@>` containment. No existing column, constraint, row or value is
-- changed. A database without this migration behaves exactly as it does today — the reader degrades
-- to the code defaults and the report says which migration to apply (contract §5).
--
-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS mdf_entitlement_config;
--   UPDATE commcalc.ui_label_override
--      SET label = jsonb_set(label::jsonb, '{tiles}',
--            (SELECT COALESCE(jsonb_agg(t), '[]'::jsonb)
--               FROM jsonb_array_elements(label::jsonb -> 'tiles') t
--              WHERE t ->> 'title' <> 'Subsidy Months Missing'))::text
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope = 'tiles' AND key = 'management-overview';
-- ─────────────────────────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ══ BLOCK 1 — the per-org config column ═════════════════════════════════════════════════════════

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS mdf_entitlement_config JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.mdf_entitlement_config IS
  'Per-org config for the entitlement-month completeness report (index §68). Keys: enabled; '
  'term_months = how many months the entitlement runs per store (declared, never a literal in '
  'code); entitlement_categories = the org''s OWN payment_categories label(s) whose payments are '
  'the entitlement (empty = nothing declared, every store reports not-measured and no month is '
  'called missing); store_windows = {canonical store key -> ''YYYY-MM'' first entitled month} (a '
  'store absent here has an UNKNOWN window and is NEVER accused of a missing month); '
  'monthly_amounts = optional {store key -> amount} to price a gap, otherwise the store''s own '
  'agreeing payments price it and a gap nothing can price is reported UNPRICED, never $0.00; '
  'columns = the feed column names. House defaults live in '
  'backend/app/modules/commcalc/entitlement_coverage.py CODE_DEFAULT; a tenant row overrides the '
  'house org row key by key (mig 207''s shape).';

-- House row: the TERM and the CATEGORY, which are facts the owner has already stated or declared.
-- `store_windows` stays EMPTY on purpose — see the header. Only fills a NULL, so a row an operator
-- has already edited is never overwritten.
UPDATE commcalc.commission_org_config
   SET mdf_entitlement_config = '{
         "enabled": true,
         "term_months": 12,
         "entitlement_categories": ["MDF"],
         "store_windows": {},
         "monthly_amounts": {}
       }'::jsonb,
       updated_at = now()
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND mdf_entitlement_config IS NULL;

-- ══ BLOCK 2 — the Management Overview tile ("make it into management overview") ═════════════════
-- Tile layout is D1 CONFIG in commcalc.ui_label_override (scope='tiles'), a HOUSE row every tenant
-- inherits and may override in the Dashboard Designer.
--
-- THIS USES THE HOUSE PATTERN (mig 1002 / 1016 / 1029) AND NOT A STRING REPLACE. The Dashboard
-- Designer round-trips the layout through a JSON encoder, so the live row's spacing and key order
-- are not what any migration INSERTed — any migration that string-matches into a tile layout is
-- broken by construction and reports success while adding no tile (§19.31 (8)). A jsonb append with
-- an `@>` containment guard is order- and whitespace-proof.
UPDATE commcalc.ui_label_override
   SET label = jsonb_set(
         label::jsonb,
         '{tiles}',
         (label::jsonb -> 'tiles') || '[
           {"title":"Subsidy Months Missing","icon":"\ud83d\udcc6",
            "desc":"A ramp-up subsidy is a fixed number of months per store. This names any store-month inside a store''s own entitlement that never arrived - store and month - and keeps a month no payment reached us for at all in its own bucket, because that is our feed and not the carrier withholding it.",
            "items":[{"href":"/commcalc/mdf-entitlement-coverage","label":"Subsidy Months Missing"}]}
         ]'::jsonb
       )::text,
       updated_at = now()
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND scope   = 'tiles'
   AND key     = 'management-overview'
   AND jsonb_typeof(label::jsonb -> 'tiles') = 'array'
   AND NOT (label::jsonb -> 'tiles') @> '[{"title":"Subsidy Months Missing"}]'::jsonb;

-- Post-flight (mig 1016's own check): the tile must be present EXACTLY once and the layout must
-- still parse as the shape the hub reads. A half-applied dashboard is a blank screen for every
-- manager, so this rolls the whole block back rather than leaving one.
DO $$
DECLARE n INT;
BEGIN
  SELECT COUNT(*) INTO n
    FROM commcalc.ui_label_override o,
         LATERAL jsonb_array_elements(o.label::jsonb -> 'tiles') t
   WHERE o.org_id = '00000000-0000-0000-0000-000000000001'
     AND o.scope = 'tiles' AND o.key = 'management-overview'
     AND t ->> 'title' = 'Subsidy Months Missing';
  IF n <> 1 THEN
    RAISE EXCEPTION 'expected exactly 1 "Subsidy Months Missing" tile on the house management-overview layout, found %', n;
  END IF;
END $$;

COMMIT;
