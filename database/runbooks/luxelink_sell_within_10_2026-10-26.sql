-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- RUN THIS ONCE, AS A SINGLE RUN, IN THE SUPABASE SQL EDITOR
-- Luxelink: sell a phone within 10 days of receiving it in stock (was 12)
-- Prepared 2026-10-26 for Sanjot.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
--
-- WHAT YOU ASKED FOR
--   "inventory aging in luxelink should say sell within 10 days of receiving the phones in stock."
--
-- WHAT THIS CHANGES
--   ONE number, on ONE row, in ONE table: commcalc.payable_source_map.sell_within_days goes from
--   12 to 10 on Luxelink Wireless LLC's Total / MA carrier row.
--
--   That is the whole change. The selling window has been a declared config value since migration
--   1091 — no number is written in the code — so moving this one row moves the Inventory Aging
--   report, the payables out-of-pocket verdict and the new rep dashboard card together. Nothing
--   needs to be redeployed or recalculated: all three read the row live.
--
-- WHAT IT DOES *NOT* TOUCH
--   · net_terms_days, owed_mode, owed_price_table, unsold_source — deliberately untouched. Another
--     piece of work is declaring net_terms_days = 21 on the same row this week, and because each
--     side writes a different column, neither undoes the other.
--   · Cellfonz R Us's Boost row (60 days) and its Total row (12 days) — untouched. This is Luxelink
--     only, which is what you asked for.
--   · No table is created, no row is inserted or deleted, no money moves.
--
-- WHAT YOU WILL SEE AFTERWARDS
--   The "aging" warning edge is derived from the window (three quarters of it, which is why Boost's
--   long-standing 45-of-60 ladder is exactly what it always was). So Luxelink's ladder becomes:
--     under 7 days   = fresh
--     7 to 10 days   = aging
--     over 10 days   = past the selling window
--   Measured against your live inventory snapshot of 2026-10-26, on the 446 phones, tablets,
--   watches and hotspots actually on hand:
--     at the current 12 days  ->  170 past the window, worth $55,482.32
--     at 10 days              ->  247 past the window, worth $69,941.61
--   So 77 units move from "aging" to "past the window" purely because the window tightened, and
--   the 50 that currently read "due today" at 12 days are already past it at 10.
--   (SIM kits and accessories are counted separately and are not in those figures — see the note
--   at the bottom. Counting them in, the same shelf reads 612 past the window at 12 days and 689
--   at 10, which is the measurement that persuaded me to leave them out of the card.)
--
-- HOW TO RUN IT
--   1. Open your Supabase project -> SQL Editor -> New query.
--   2. Paste this WHOLE file in one go and press Run ONCE.
--      It is a single statement plus two read-backs, so there is nothing to split and nothing that
--      can be half-applied. (Do not split it: the SQL Editor ends the transaction at the end of
--      each Run, so a change split across two Runs is silently thrown away.)
--   3. Read the two result tables it prints and check they say what the comments below say they
--      should. If the "BEFORE" table does not show 12, stop and tell me rather than re-running.
--
-- HOW TO UNDO IT
--   Change 10 back to 12 and run it again. Nothing else is affected.
-- ════════════════════════════════════════════════════════════════════════════════════════════════


-- ── 1. BEFORE: what the row says right now ──────────────────────────────────────────────────────
-- Expect exactly ONE row: Luxelink, 'Total / MA', sell_within_days = 12.
-- If you see no rows, or a number other than 12, do not continue.
SELECT 'BEFORE'                       AS stage,
       t.name                         AS tenant,
       m.label                        AS carrier_row,
       m.sell_within_days             AS selling_window_days,
       m.warn_fraction                AS warn_fraction_null_means_three_quarters,
       m.net_terms_days               AS payment_terms_days,
       m.unsold_source                AS which_feed_says_unsold
  FROM commcalc.payable_source_map m
  LEFT JOIN storeops.tenants t ON t.org_id = m.org_id
 WHERE m.org_id     = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'   -- Luxelink Wireless LLC
   AND m.carrier_id = 'e5c6d456-e17e-476e-9b67-9d739beac9bd'   -- Total / MA
   AND m.is_active  IS TRUE;


-- ── 2. THE CHANGE: one column, one row ──────────────────────────────────────────────────────────
-- Scoped by org_id AND carrier_id AND is_active, so it cannot reach another tenant's row or a
-- retired one. Only sell_within_days appears in the SET list; every other column keeps whatever it
-- holds, including anything another change writes to this row this week.
UPDATE commcalc.payable_source_map
   SET sell_within_days = 10            -- owner 2026-10-26: "sell within 10 days of receiving"
 WHERE org_id     = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
   AND carrier_id = 'e5c6d456-e17e-476e-9b67-9d739beac9bd'
   AND is_active  IS TRUE;


-- ── 3. AFTER: prove it took, and prove nothing else moved ───────────────────────────────────────
-- Expect: Luxelink Total / MA = 10, and Cellfonz's two rows exactly as they were (Boost 60,
-- Total 12). If the Cellfonz numbers have changed, something else ran between the two reads — tell
-- me before doing anything further.
SELECT 'AFTER'                        AS stage,
       t.name                         AS tenant,
       m.label                        AS carrier_row,
       m.sell_within_days             AS selling_window_days,
       m.net_terms_days               AS payment_terms_days,
       m.unsold_source                AS which_feed_says_unsold,
       CASE
         WHEN m.org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
              AND m.sell_within_days = 10 THEN 'changed as intended'
         WHEN m.org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
              THEN '*** NOT 10 — STOP AND REPORT THIS ***'
         ELSE 'untouched (another tenant)'
       END                            AS verdict
  FROM commcalc.payable_source_map m
  LEFT JOIN storeops.tenants t ON t.org_id = m.org_id
 WHERE m.is_active IS TRUE
 ORDER BY t.name, m.label;


-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- TWO THINGS WORTH KNOWING, NEITHER OF WHICH THIS FILE CHANGES
--
-- 1. WHAT "RECEIVED IN STOCK" MEANS HERE, EXACTLY.
--    There is no vendor receipt date anywhere in the platform: the column that would hold it
--    (commcalc.inventory_aging_device.received_date) is empty on all 10,525 rows, for both tenants.
--    What your POS export DOES carry is each unit's age in store, which the platform lands as
--    days_in_stock against the date of the file. So the receipt date is worked out as
--    "the file's date minus the unit's age", and the screens label it that way rather than
--    pretending a receipt document exists. That is the honest version of what you asked for, and it
--    is the POS's own count of how long the phone has been on the shelf.
--
--    If your distributor can supply a real per-unit receipt date, the platform will prefer it over
--    the derived one automatically — no code change needed.
--
-- 2. SIM KITS ARE NOT COUNTED AS PHONES, ON PURPOSE.
--    Of the 888 units on hand at the 2026-10-26 snapshot, 442 are SIM kits — 435 "Total by Verizon
--    SIM Kit" and 7 "Total Wireless BYOT SIM Kit". They are all over a hundred days old and worth
--    about $1,300 in total, and when they were counted in, they filled the whole screen and pushed
--    every genuinely overdue handset out of sight. The rep card lists phones, tablets, watches and
--    hotspots, and says underneath how many SIM kits and accessories it held back, so nothing is
--    hidden. If you want them aged too, say so and it is a one-line change.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
