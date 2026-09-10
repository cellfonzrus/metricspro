-- 1003_daily_closing_workflow_tile.sql
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- OWNER DIRECTIVE 2026-09-10 (verbatim):
--   "Also based ont th flowcharts you create the modules for these should be stacked properly based
--    on thr work flow in one tile so the user does not have to loo for the next module it is user
--    friendly and also the current module should ask the chart what do they want to do next"
--
-- WHAT THIS FIXES. /hub/daily-closing had NO tile layout row of its own, so it fell back to
-- `defaultHubGroups` — the module's pages in NAV order, which is a filing order, not a doing order.
-- A DM finishing DM Verify had to know that "Cash Pickup" was the next thing and go find it, with
-- Management Review, X-Tender Recon and Accessory Recon sitting between them on the screen.
--
-- THE FIRST TILE IS NOW THE WORKFLOW, IN SEQUENCE: close → verify → collect → account. The rest of
-- the module keeps its own tiles below, because they are real work too — they are just not steps in
-- this chain, and pretending everything is a sequence is as unhelpful as pretending nothing is.
--
-- ONE DEFINITION OF THE ORDER. The same sequence is `dailyClosing.stages` in
-- frontend/src/lib/flowcharts.tsx, which also drives the runbook and the "next step" prompt at the
-- foot of each screen (components/WorkflowNext). Those are the SAME seven hrefs in the SAME order —
-- harness_workflow_stacking.py compares this file against that array and fails the build if they
-- ever diverge, because a stacked tile that disagrees with the training material is worse than no
-- tile at all.
--
-- NO NEW MECHANISM: dashboard tiles are D1 CONFIG (mig 068 commcalc.ui_label_override, scope='tiles'),
-- house rows every tenant inherits and may override in the Dashboard Designer — the mig-948 pattern,
-- and mig 1002's precedent. This is an INSERT, not an UPDATE, because unlike management-overview no
-- daily-closing row has ever been seeded.
--
-- ON CONFLICT DO NOTHING: a tenant (or a re-run) that already has a daily-closing layout keeps it.
-- Replacing somebody's designed dashboard because a migration ran twice is not a fix.
--
-- REVERT:
--   DELETE FROM commcalc.ui_label_override
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope = 'tiles' AND key = 'daily-closing';
--   (Removing the row restores the auto-derived default tiles; no page is lost either way.)
-- ─────────────────────────────────────────────────────────────────────────────────────────────────

BEGIN;

INSERT INTO commcalc.ui_label_override (org_id, scope, key, label)
VALUES ('00000000-0000-0000-0000-000000000001', 'tiles', 'daily-closing', '{"version":1,"tiles":[
  {"title":"The closing workflow","icon":"➡️",
   "desc":"Every step in the order it happens - close, verify, collect, account. Start at the top and work down; each screen tells you what comes next.",
   "items":[{"href":"/closing/submit","label":"1 · Submit Closing"},
            {"href":"/closing/verify","label":"2 · DM Verify"},
            {"href":"/closing/pickup","label":"3 · Cash Pickup"},
            {"href":"/closing/billpay-pickup","label":"4 · Bill Payment Pickup"},
            {"href":"/closing/deposit-recon","label":"5 · Cash Deposit Recon"},
            {"href":"/closing/envelope-report","label":"6 · Envelope Report"},
            {"href":"/closing/store-cash-on-hand","label":"7 · Store Cash on Hand"}]},
  {"title":"Dashboard","icon":"🧾",
   "desc":"The closing dashboard - what came in today, what is missing, and who has not closed",
   "items":[{"href":"/closing"}]},
  {"title":"Review & exceptions","icon":"🛡️",
   "desc":"Closings the gate could not settle, and the payouts taken out of an envelope",
   "items":[{"href":"/closing/management"},
            {"href":"/closing/envelope-payout"}]},
  {"title":"Reconciliation","icon":"🔎",
   "desc":"Tie the declared money to the POS, the card processor and the bank",
   "items":[{"href":"/closing/recon"},
            {"href":"/closing/tender-recon"},
            {"href":"/closing/tender-recon-3way"},
            {"href":"/closing/accessory-recon"},
            {"href":"/closing/external-credit-recon"},
            {"href":"/closing/cash-recon-management"}]},
  {"title":"Setup","icon":"⚙️",
   "desc":"Closing deadline and gate, the assigned closer per store, and the tender list",
   "items":[{"href":"/closing/cash-config"},
            {"href":"/closing/tender-config"}]}
]}')
ON CONFLICT (org_id, scope, key) DO NOTHING;

-- Post-flight: the workflow tile must exist and hold its seven steps IN ORDER. A tile that lost a
-- step, or reordered one, would teach the wrong sequence to every DM who reads it — roll back rather
-- than ship that.
DO $$
DECLARE got TEXT[]; want TEXT[] := ARRAY[
  '/closing/submit','/closing/verify','/closing/pickup','/closing/billpay-pickup',
  '/closing/deposit-recon','/closing/envelope-report','/closing/store-cash-on-hand'];
BEGIN
  SELECT array_agg(i ->> 'href' ORDER BY ord)
    INTO got
    FROM commcalc.ui_label_override o,
         LATERAL jsonb_array_elements(o.label::jsonb -> 'tiles') t,
         LATERAL jsonb_array_elements(t -> 'items') WITH ORDINALITY AS x(i, ord)
   WHERE o.org_id = '00000000-0000-0000-0000-000000000001'
     AND o.scope = 'tiles' AND o.key = 'daily-closing'
     AND t ->> 'title' = 'The closing workflow';
  IF got IS DISTINCT FROM want THEN
    RAISE EXCEPTION 'daily-closing workflow tile is not the expected sequence: %', got;
  END IF;
END $$;

COMMIT;
