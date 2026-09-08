-- 993_commission_backoffice_recon.sql
-- Owner directive 2026-09-08 (verbatim): "this is the p&l calculated as per the back office,
-- disregard the expenses but check the commission received as per our system and the back office,
-- seems like a big difference, all items should match and there should be nothing in unsplit,
-- everything has a reason and everything is assigned to the code, so anything which is assigned to
-- the company level should be split among the store — they are also pulling the data from the same
-- source as us. dont count any rebate received in the commission — it reflects in the balance sheet
-- towards gross sales but not in gross profit. also the residual seems a lot off, check and fix".
--
-- MONEY-TOUCHING. Section A (columns) is inert on its own. Section B (the per-org seeds) MOVES
-- REPORTED DOLLARS and is COMMENTED OUT behind the owner gate — apply only on an explicit go.
-- Numbered, idempotent, additive. Nothing here is applied by an agent.
--
-- WHAT WAS MEASURED (org 854f6d7b-6590-4e4d-88ab-646f560d4f4c, period 'August 2026', live read
-- 2026-09-08; the org holds BOTH back-office books — company 'Luxlink Wireless' = the 13 Chicago
-- stores, company 'Nova Wave Communications' = the 7 NY/NJ stores; they are NOT two org_ids):
--
--   line (back office)      back office     ours (booked)     variance   cause
--   Postpaid Residual         35,490.67       35,490.67           0.00   exact, 20/20 stores
--   Postpaid Spiff            97,465.36       94,861.81      -2,603.55   'Retroactive Postpaid
--                                                                        Spiff' books nowhere
--                                                                        (feed has 3,794.56 of it;
--                                                                        the back office itself is
--                                                                        1,191.01 short of its feed)
--   Premium Store Spiff       15,000.00       16,000.00      +1,000.00   the feed carries a 16th
--                                                                        $1,000 row (3560 Nostrand)
--                                                                        the back office dropped
--   rebate (no such line)          0.00      251,946.31    +251,946.31   pl_rebate_presentation
--                                                                        = 'income' puts device
--                                                                        rebates on a REVENUE line
--
-- ── SECTION A — CONFIG COLUMNS (inert; no dollar moves) ─────────────────────────────────────────
-- RULE TWO: both are per-org config with house defaults. No carrier, tenant, product name, month
-- count or rate appears in any code branch.

ALTER TABLE commcalc.commission_org_config
  -- WHICH COMPONENTS THE RESIDUAL-PER-SUBSCRIBER REPORT SUMS INTO "residual", per SOURCE (a
  -- feed-shape key, never a carrier name): {"boost_mi_atu": ["mi","atu"], "vidapay_ma": ["mi"]}.
  -- NULL = the house defaults in account/residual_subs.RESIDUAL_COMPONENTS_DEFAULT, which are the
  -- fix: the Boost source keeps MI+ATU (two halves of one booked residual line), the MA/VidaPay
  -- source counts the booked residual line ONLY. Setting {"vidapay_ma": ["mi","atu"]} restores the
  -- pre-fix fold for an org that wants it.
  ADD COLUMN IF NOT EXISTS residual_report_components JSONB,
  -- WHY a raw_ma_daily_tx order-type family books to no P&L line: {order_type: reason}. Read by
  -- account/ma_store_pnl.ma_tx_coverage. A family with no entry is reported with the literal
  -- 'no business rule configured' (commcalc.ma_recon.NO_RULE_REASON, mig 312) — absence of a rule
  -- is reported, never guessed at. NULL = {} = every unbooked family is unexplained.
  ADD COLUMN IF NOT EXISTS pl_ma_unbooked_reasons JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.residual_report_components IS
  'Per-source component list summed into the residual-per-subscriber report''s "residual" '
  '(owner 2026-09-08). NULL = house defaults: boost_mi_atu = mi+atu, vidapay_ma = mi only '
  '(airtime margin has its own P&L line since mig 309 and does not recur per subscriber).';
COMMENT ON COLUMN commcalc.commission_org_config.pl_ma_unbooked_reasons IS
  'Per-org {raw_ma_daily_tx.order_type: stated reason} for families that book to no P&L line. '
  'Unlisted families report the literal ''no business rule configured'' (mig 312 marker).';

-- ── SECTION B — PER-ORG SEEDS (MOVES MONEY — OWNER APPROVAL REQUIRED) ───────────────────────────
-- Each statement below is a separate decision. Uncomment only the ones approved.
--
-- B1. REBATES ARE NOT COMMISSION (owner: "dont count any rebate received in the commission").
--     Effect, August 2026: revenue line "Rebates (device purchase)" −$251,946.31 (Luxlink
--     −188,343.28 / Nova Wave −63,603.03); the same dollars land back on the `device_rebate`
--     contra-COGS line against the handset purchases they belong to. GROSS PROFIT AND NET INCOME
--     ARE UNCHANGED (revenue and COGS move together — mig 934 header). What changes is that a
--     rebate stops presenting as income anyone can mistake for commission received.
--     NOTE FOR THE OWNER, stated plainly: this takes the rebate out of REVENUE. It does not take
--     the device leg out of GROSS PROFIT, because the handset PURCHASE is still booked to COGS
--     (`device_cost`, Aug $260,206.80). The back office's own P&L carries neither the purchase nor
--     the rebate — it treats the whole device leg as inventory/balance sheet. Making ours match
--     that is a FINANCE-side change to the device COGS line, not a commission-line change; today
--     the device leg (device_rev 80.81 − device_cost 260,206.80 + rebate 251,946.31) contributes
--     −$8,179.68 to gross profit.
-- UPDATE commcalc.commission_org_config
--    SET pl_rebate_presentation = 'contra_cogs', updated_at = now()
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';
--
-- B2. THE SPIFF FAMILY THE CONFIG NEVER NAMED. 'Retroactive Postpaid Spiff' is carrier spiff cash
--     paid to the dealer (Aug 2026: 230 rows, +$3,794.56, every dollar attributable to a store
--     through the mig-314 account→store index) and it books to NO P&L line today, because
--     pl_ma_spiff_order_types lists only 'PostPaid Additional Spiff'. Adding it moves our
--     "Carrier commissions & incentives" from 94,861.81 to 98,656.37; the back office's own
--     Postpaid Spiff is 97,465.36, i.e. 1,191.01 SHORT of what the feed both books hold (its
--     shortfall is per-store and reported in the recon — 4 of 20 stores tie exactly).
-- UPDATE commcalc.commission_org_config
--    SET pl_ma_spiff_order_types =
--          '["PostPaid Additional Spiff", "Retroactive Postpaid Spiff"]'::jsonb,
--        updated_at = now()
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';
--
-- B3. WHY THE REST OF THE DAILY-TX FEED BOOKS NOWHERE — stated, not silent. These families are the
--     DEVICE leg (purchases, promo subsidies, voids, SIM kits, invoice fees). They are already in
--     the books through the device-COGS path (raw_ma_commission / raw_ma_fulfillment, mig 314
--     device_cogs) — booking them again from daily_tx would double-count. 'Activation Order' is the
--     plan purchase; its dealer margin is the row's merchant_discount, which DOES book, to
--     "Merchant discount". Reasons are config so the coverage read-out can say this out loud.
-- UPDATE commcalc.commission_org_config
--    SET pl_ma_unbooked_reasons = '{
--          "Postpaid Branded MarketPlace": "device purchase — booked as device COGS from the MA Commission Details / fulfillment path (mig 314 device_cogs); booking it again here would double-count",
--          "Postpaid Branded Void": "reversal of a device purchase — nets inside the same device COGS path",
--          "Postpaid Promo Order": "carrier device subsidy against a purchase — nets inside the device COGS path, it is not commission earned",
--          "Sales Order": "airtime/refill wallet purchase — the dealer margin on it is the row merchant_discount, which books to Merchant discount",
--          "Activation Order": "plan purchase at activation — the dealer margin on it is the row merchant_discount, which books to Merchant discount",
--          "Postpaid MarketPlace": "SIM kit purchase — distributor fee/COGS, not commission",
--          "Postpaid MarketPlace RMA": "SIM kit return — reversal of the purchase above",
--          "SIM Assigment": "identifier assignment rows, always zero money",
--          "Fee": "processor invoice fee — a distributor fee, not commission"
--        }'::jsonb,
--        updated_at = now()
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';
--
-- B4. RESIDUAL BASIS. No seed is required: the house default now excludes airtime margin from the
--     MA/VidaPay residual figure, which is what makes the report agree with the books
--     ($35,490.67, not $54,972.03). A row here is only needed to OPT BACK IN:
-- UPDATE commcalc.commission_org_config
--    SET residual_report_components = '{"vidapay_ma": ["mi", "atu"]}'::jsonb, updated_at = now()
--  WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

-- REVERT:
--   ALTER TABLE commcalc.commission_org_config
--     DROP COLUMN IF EXISTS residual_report_components,
--     DROP COLUMN IF EXISTS pl_ma_unbooked_reasons;
--   -- B1: UPDATE commcalc.commission_org_config SET pl_rebate_presentation = 'income'
--   --       WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';
--   -- B2: UPDATE commcalc.commission_org_config
--   --       SET pl_ma_spiff_order_types = '["PostPaid Additional Spiff"]'::jsonb
--   --       WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';
--   -- B3: UPDATE commcalc.commission_org_config SET pl_ma_unbooked_reasons = NULL
--   --       WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';
--   -- Dropping the columns alone restores every house default (both readers are ADAPTIVE and
--   -- degrade to the code defaults when the column is absent).
