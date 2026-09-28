-- 1029_ready_app_denominator.sql — the Ready App denominator (owner ruling 2026-09-27), the sweep's
-- report set as config, and the Feed-vs-Transactions tile on the Management Overview dashboard.
--
-- Run this in the Supabase SQL editor. Claude does not run SQL and has applied NOTHING of this file.
-- Additive, idempotent, safe to re-run. Numbered, higher number = later ALTER wins.
--
-- OWNER RULING, verbatim, 2026-09-27: *"Denominator should be the total of new activations excluding
-- upgrade and swap as reported in exec mats - data source is the same for all reports"* ("exec mats" =
-- the Executive MTD report). Settles open question (iii) of index §19.28 and is implemented in code as
-- ONE derivation (`line_class.new_activation_units`) that Executive MTD, the pay engine's Ready App
-- rate and the new Feed-vs-Transactions report all dereference. See index §19.31.
--
-- ══ BLOCK 1 — ADDITIVE, MONEY-NEUTRAL ON ITS OWN ════════════════════════════════════════════════
-- Two nullable config columns. Nothing changes behaviour by existing: the code resolves a NULL to the
-- PRE-RULING default in both cases (`kpi_failing.resolve_boostapp_basis` → 'feed_prepaid';
-- `dlar_sweep.resolve_report_set` → the two reports it has always pulled).

BEGIN;

ALTER TABLE commcalc.payout_config
  ADD COLUMN IF NOT EXISTS kpi_boostapp_basis TEXT;

COMMENT ON COLUMN commcalc.payout_config.kpi_boostapp_basis IS
  'Which basis the Carrier-App (Ready App) KPI rate is measured on, per org PER PERIOD. '
  '''exec_new_activations'' = the owner''s 2026-09-27 ruling: boost_ready_bounty over the store''s own '
  'new activations (Executive MTD''s Total Activation less Upgrade less swap — line_class.new_activation_units). '
  '''feed_prepaid'' = the pre-ruling behaviour: the stored raw_dlar_rep.boost_app_pct, which the sweep '
  'derived at INGEST from the feed''s ga_prepaid column. NULL = ''feed_prepaid'', so every period keeps '
  'the score it was paid on until this is set. PER PERIOD deliberately: setting it from one month '
  'forward leaves closed months exactly as paid.';

ALTER TABLE commcalc.dlar_sweep_config
  ADD COLUMN IF NOT EXISTS reports JSONB;

COMMENT ON COLUMN commcalc.dlar_sweep_config.reports IS
  'Which of the portal''s declared reports this org''s sweep pulls, as a JSON array of keys from '
  'dlar_sweep.PORTAL_REPORTS (today: "dlar", "advocate", and the DECLARED-BUT-UNMAPPED '
  '"boost_ready_by_advocate"). NULL = the default pair, byte-identical to every run to date. '
  'Owner: "nothing is hard coded, option is platform wide" — the report SET is config; a report with no '
  'normalizer is refused BY NAME with the reason and never pulled, because a guessed column mapping on a '
  'feed that tiers pay is the index §19.26 defect.';

-- ── The Feed-vs-Transactions tile on the Management Overview dashboard (owner: "show it it in
--    management dashboard"). Tile layout is D1 CONFIG in commcalc.ui_label_override (scope='tiles'),
--    a HOUSE row every tenant inherits and may override in the Dashboard Designer.
--
--    THIS USES THE HOUSE PATTERN (mig 1002 / mig 1016) AND NOT A STRING REPLACE. The first draft of
--    this block did `replace(label, '{"title":"Failing KPIs"', ...)`. MEASURED against the live row
--    2026-09-27: it matches NOTHING, so the guarded UPDATE would have silently not fired — a migration
--    that appears to succeed and adds no tile. Mig 948 INSERTed COMPACT json
--    (`{"title":"Failing KPIs","icon":...`), but the row has since been RE-SERIALISED: spaces after
--    colons and keys reordered (`..."items": [{"href": "/commcalc/kpi-failing"}], "title": "Failing
--    KPIs"}`), because the Dashboard Designer round-trips the layout through a JSON encoder.
--    ** ANY migration that string-matches into a tile layout is broken by construction. ** jsonb append
--    with an `@>` containment guard is order- and whitespace-proof, and is exactly what mig 1016 does.
UPDATE commcalc.ui_label_override
   SET label = jsonb_set(
         label::jsonb,
         '{tiles}',
         (label::jsonb -> 'tiles') || '[
           {"title":"Feed vs Transactions","icon":"\u2696\ufe0f",
            "desc":"What the carrier report claims beside what the store transactions say - and what accounts for every difference: a counting definition, a stale feed slice, or nothing.",
            "items":[{"href":"/commcalc/dlar-vs-platform","label":"Feed vs Transactions"}]}
         ]'::jsonb
       )::text,
       updated_at = now()
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND scope   = 'tiles'
   AND key     = 'management-overview'
   AND jsonb_typeof(label::jsonb -> 'tiles') = 'array'
   AND NOT (label::jsonb -> 'tiles') @> '[{"title":"Feed vs Transactions"}]'::jsonb;

-- Post-flight (mig 1016's own check): the tile must be present EXACTLY once and the layout must still
-- parse as the shape the hub reads. A half-applied dashboard is a blank screen for every manager, so
-- this rolls the whole block back rather than leaving one.
DO $$
DECLARE n INT; v INT;
BEGIN
  SELECT COUNT(*) INTO n
    FROM commcalc.ui_label_override o,
         LATERAL jsonb_array_elements(o.label::jsonb -> 'tiles') t
   WHERE o.org_id = '00000000-0000-0000-0000-000000000001'
     AND o.scope = 'tiles' AND o.key = 'management-overview'
     AND t ->> 'title' = 'Feed vs Transactions';
  IF n <> 1 THEN
    RAISE EXCEPTION 'expected exactly 1 "Feed vs Transactions" tile on the house management-overview layout, found %', n;
  END IF;
  SELECT (label::jsonb ->> 'version')::INT INTO v
    FROM commcalc.ui_label_override
   WHERE org_id = '00000000-0000-0000-0000-000000000001'
     AND scope = 'tiles' AND key = 'management-overview';
  IF v IS NULL THEN
    RAISE EXCEPTION 'house management-overview layout lost its version key';
  END IF;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- REVERT (paste and run to undo BLOCK 1):
--   ALTER TABLE commcalc.payout_config     DROP COLUMN IF EXISTS kpi_boostapp_basis;
--   ALTER TABLE commcalc.dlar_sweep_config DROP COLUMN IF EXISTS reports;
--   UPDATE commcalc.ui_label_override
--      SET label = jsonb_set(label::jsonb, '{tiles}',
--                    (SELECT COALESCE(jsonb_agg(t), '[]'::jsonb)
--                       FROM jsonb_array_elements(label::jsonb -> 'tiles') t
--                      WHERE t ->> 'title' <> 'Feed vs Transactions'))::text,
--          updated_at = now()
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope = 'tiles' AND key = 'management-overview';
--   NOTIFY pgrst, 'reload schema';


-- ══ BLOCK 2 — MONEY. DELIBERATELY COMMENTED OUT. THE OWNER'S CALL, NOT APPLIED. ══════════════════
--
-- This is the one statement that puts the ruling into force. It is held because it MOVES REP PAY the
-- next time the affected period is recalculated — and the DLAR sweep recalculates the CURRENT period on
-- every run (daily, 07:00 America/New_York), so setting it for an open month changes pay without anyone
-- asking again.
--
-- MEASURED, read-only, 2026-09-27, over all 322 HOUSE rep-months (nothing recomputed or written). Only
-- the `boostapp` value changes; every other KPI value, every count and every rate stays as stored. Tier
-- moves are computed as subtotal x (new tier - old tier).
--
--   62 rep-months change their KPI MET-COUNT.  6 change a TIER, and every one of them goes DOWN:
--
--     period        rep                store                  bounty  denom  boostapp        met      tier        delta
--     June 2026     Waleed Asghar      —                      8       23     80.00 -> 34.78  5/7->4/7 0.75->0.50  -153.72
--     April 2026    Radhika Sharma     —                      1       24    100.00 ->  4.17  5/7->4/7 0.75->0.50  -145.65
--     June 2026     Yesica Reyes       —                      5       23     83.33 -> 21.74  5/7->4/7 0.75->0.50  -124.71
--     June 2026     Abdullah Mohammed  —                      2       19    100.00 -> 10.53  5/7->4/7 0.75->0.50  -119.65
--     April 2026    Rana Akhtar        —                      2       10    100.00 -> 20.00  5/7->4/7 0.75->0.50   -71.02
--     March 2026    Angie Ramos        —                      1       13    100.00 ->  7.69  5/7->4/7 0.75->0.50   -43.55
--                                                                                                     TOTAL      -658.30
--
--   WHY IT GOES DOWN, AND WHY THAT IS THE RULING WORKING AS INTENDED: in March-June the feed's own
--   `ga_prepaid` was arriving and was TINY next to the store's transactions (Radhika April: bounty 1
--   over ga_prepaid 1 = "100%", against 24 real new activations = 4.17%). The pre-ruling rate was a
--   bounty divided by a number that was not a month's activations, so it read near-100% for reps who
--   had sold one Ready App. July onward the column stopped arriving entirely and the rate is NULL, so
--   those months move no money at all. NOTHING in July, August or September 2026 changes.
--
--   NOT A RETROACTIVE CLAW-BACK UNLESS HE WANTS ONE: this column is PER PERIOD. Setting it only from
--   September 2026 forward leaves every closed month exactly as paid and -658.30 never happens.
--
-- (a) FROM ONE PERIOD FORWARD — closed months untouched (the recommended form; there is no September
--     2026 payout_config row yet, so it is an INSERT):
--
--   INSERT INTO commcalc.payout_config (org_id, period, kpi_boostapp_basis)
--   VALUES ('00000000-0000-0000-0000-000000000001', 'September 2026', 'exec_new_activations')
--   ON CONFLICT (org_id, period) DO UPDATE SET kpi_boostapp_basis = EXCLUDED.kpi_boostapp_basis;
--
-- (b) EVERY PERIOD, accepting the -658.30 on the six rep-months above the next time each is
--     recalculated:
--
--   UPDATE commcalc.payout_config SET kpi_boostapp_basis = 'exec_new_activations'
--    WHERE org_id = '00000000-0000-0000-0000-000000000001';
--
-- (c) THE BOUNDARY IS CLOSED — the owner ruled on 2026-09-28 and there is NOTHING TO APPLY here.
--
--   (c1) INELIGIBLE PORT-IN — **RULED IN** (owner, 2026-09-28, verbatim: *"port in is activation"*).
--        An ineligible port-in COUNTS in the new-activation denominator. That is already the shipped
--        default (`line_class.HOUSE_NEW_ACTIVATION` excludes only `swap`), so no code and no config
--        change follows from the ruling — which is why the statement that used to sit here, the one
--        that would have added "ineligible" to the exclusion list, has been DELETED rather than left
--        as an option. It is a statement the owner has ruled against; leaving it in a migration file
--        as a copy-pasteable UPDATE is how a ruled-out option gets applied by a later reader.
--
--        THE CONSEQUENCE, STATED RATHER THAN BURIED (index §19.31 (6)): the reference rep (store
--        11636, August 2026) now reads 14 on count_unit='transaction' and 15 on 'event', against
--        ElevateGo's own finalised 13. A RESIDUAL OF 1-2 REMAINS ON THAT STORE and the ruling does
--        not make it go away. What defends the ruling is the population, not that rep: across 296
--        comparable (rep, store, period) cells it is the better fit — 53.0% exact as ruled against
--        52.0% with ineligible port-ins excluded. The count is also no longer identical under both
--        count units (14 vs 15), so §6f's count_unit flip now moves this denominator by one on that
--        cell; that is a known and accepted cost of the ruling, not a defect to chase.
--
--   (c2) PORT-IN and NEW BYOD, both measured and both pointing the SAME way — they ARE new
--        activations, so the ruling as written is right and nothing needs doing. Excluding all
--        port-ins takes the fit from 53.0% exact / +1.16 mean error to 36.8% / -1.66; excluding BYOD
--        takes it to 32.4% / -2.35. Recorded here so the question is closed with evidence rather than
--        left open. The statements that WOULD narrow the count, if he ever wants them:
--
--   -- port-in out:  '{new_activation,classes}' -> '["activation","byod"]'
--   -- new BYOD out: '{new_activation,classes}' -> '["activation","port"]'
--
-- (d) THE THIRD PORTAL REPORT the owner asked for — declared, unmapped, and NOT enabled here. Enabling
--     it needs a normalizer, which needs ONE sample of its /inline JSON; a guessed mapping on a KPI
--     that tiers pay is the §19.26 defect. When that sample exists, this is the whole change:
--
--   UPDATE commcalc.dlar_sweep_config
--      SET reports = '["dlar","advocate","boost_ready_by_advocate"]'::jsonb
--    WHERE org_id = '00000000-0000-0000-0000-000000000001';
