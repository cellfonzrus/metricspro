"""THE MODULE GRAPH — one home for "what else answers this question, and who reads it".

Owner directive 2026-10-04: *"one hand does not talk to the other and we need to build a check
mechanism via the index and registry we created that everytime an update or extension of a module is
done all connected pieces get updated automatically, the tree should be interlinked properly."*

THE CLASS, NOT THE INSTANCE. The instance was an alert's send record implemented four times
(index 15.1). The class is: **a shared fact acquires a second caller, or a second derivation, and
nothing tells the author that other pieces answer the same question.** Fifteen `harness_*_lock.py`
files already each held their own private `HOME = ...` plus a hand-typed list of callers — which is
the duplicate defect wearing the shape of its own cure: fifteen private copies of "what depends on
what", none of them interlinked, none of them queryable, every one of them hand-maintained.

So the graph has ONE home, this file, and it is DERIVED where it can be:

  · `FACTS[key]["homes"]`      — the file(s) allowed to answer this question. Hand-declared; this is
                                 the ruling, and a ruling cannot be inferred.
  · `FACTS[key]["callers"]`    — a SNAPSHOT of every file that imports a home, and the name each one
                                 binds it to. NOT hand-maintained: `harness_module_graph_guard.py`
                                 recomputes it from the import graph and fails the build when it
                                 differs, so a new connected piece cannot land silently.
  · `FACTS[key]["index"]`      — the `docs/SYSTEM_DATA_FLOW_INDEX.md` section(s) that document it.
                                 The guard checks each one resolves to a real heading.
  · `FACTS[key]["locks"]`      — the harness(es) that enforce it. The forbidden-pattern rules stay
                                 in those locks; copying them here would be the very defect this
                                 file exists to stop. The guard checks each lock exists and is run
                                 by a workflow.

WHAT THIS BUYS, concretely: `connected(path)` answers "I am about to change this file — what else
answers the same question?" in one call, and the guard puts that answer in front of the author on
the pull request instead of relying on them to remember to look.

WHAT IT DELIBERATELY DOES NOT DO: it does not edit the connected pieces for you. Nothing can safely
rewrite a caller; what breaks the "one hand does not talk to the other" loop is that the other hand
is now NAMED, in CI, before the merge.

Pure data and pure functions — no DB, no network, no imports beyond the stdlib, so a lock may
dereference it without importing the application.
"""
from __future__ import annotations

# Bump when the SHAPE of a fact entry changes, so a stale consumer fails loudly rather than quietly.
SCHEMA = 1

FACTS: dict[str, dict] = {
    # ── §CORE-17 (owner ask 2026-10-24) — a reported figure NAMES the rows behind it ────────────
    'report_drilldown': {
        "question": 'WHERE IS THIS NUMBER COMING FROM? A report that summarises — a tile, a rollup '
                    'row, a per-person or per-store total — must open that figure to the rows it '
                    'was built from, and every row must name the report, feed or upload it came '
                    'out of plus the keys that address it there. Before this there were as many '
                    'answers as reports: the P&L accumulates `detail` labels in `account/coa.py` '
                    'and ties them out in TypeScript (§4e), the commission explain re-computes '
                    'from the plan engine, `/ma-overview-recon/drill` scans one table per tile, '
                    'and `"source"` means a table name, a screen name, an upload route or a free '
                    'sentence on ~30 other payloads. 93 of 136 report routes answered nothing at '
                    'all (audit 2026-10-24). The SHAPE of the answer lives here — `origin`, '
                    '`drill_row`, `tie_out`, `panel` — and the tie-out STATES a disagreement '
                    'between a figure and its own evidence rather than rounding one away. Absence '
                    'is never zero and never a tie: `ties` is None, not False, when a counted row '
                    'has no number. Which reports still OWE a drill-down is part of the same fact '
                    'and is written down (`harness_drilldown_pending.txt`), because "by default" '
                    'is a ratchet, not a sentence in a document.',
        # The CLIENT side of the same fact is a second home on purpose (§CORE-17.3d): a report whose
        # tiles are folded over the rows the reader filtered down to needs the panel narrowed to that
        # same selection, and `drill-narrow.ts` is the only place that happens. It is pinned to the
        # server's fold by object identity on the unfiltered case (frontend/prove_drill_narrow.mjs §A8)
        # over fixtures regenerated from the real declarations (harness_drill_narrow_fixtures.py), so
        # the two cannot become two arithmetics hoping to agree.
        "homes": ('app/modules/core/report_drilldown.py',
                  'frontend/src/lib/drill-narrow.ts'),
        "index": ('CORE-17',),
        "locks": ('harness_report_drilldown.py', 'harness_drilldown_coverage.py',
                  'harness_cash_recon_drilldown.py', 'harness_tender_recon_drilldown.py',
                  'harness_day_recon_drilldown.py', 'harness_drill_narrow_fixtures.py',
                  'harness_batch5_drilldown.py', 'harness_batch6_drilldown.py',
                  'harness_batch7_drilldown.py', 'harness_batch8_drilldown.py',
                  'harness_batch9_drilldown.py', 'harness_batch10_drilldown.py',
                  'harness_batch11_drilldown.py', 'harness_batch12_drilldown.py',
                  'harness_batch13_drilldown.py', 'harness_batch14_drilldown.py',
                  'harness_batch15_drilldown.py', 'harness_batch16_drilldown.py',
                  'harness_batch17_drilldown.py',
                  'harness_batch18_drilldown.py',
                  'harness_batch19_drilldown.py',
                  'harness_batch20_drilldown.py',
                  'harness_batch21_drilldown.py',
                  'harness_batch22_drilldown.py',
                  'harness_batch25_drilldown.py'),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            # day_drill_row / group_drilldown / drilldowns — the cash-variance producer, the first
            # report on the shared mechanism; the endpoint reaches it through `drilldowns`.
            'app/modules/closing/cash_variance.py': ('report_drilldown',),
            # tile_panel / tiles — the TILE shape (§CORE-17.3a). This is the fold most of the
            # remaining reports owe, so it lives in the one home and a report declares only its
            # own catalogue. First declaration: the eleven cash-recon tiles.
            'app/modules/closing/cash_recon_drilldown.py': ('report_drilldown',),
            # The X-report-vs-closing tiles (§CORE-17.3b). Declared and tying on the unfiltered
            # range; the route still reads `owes` because its tiles are recomputed on the client,
            # so the drill is offered only where the two provably agree.
            'app/modules/commcalc/tender_recon_drilldown.py': ('report_drilldown',),
            # The two closing DAY recons — ePay bank-deposit and accessory (§CORE-17.3c). One grain
            # (store-day), one row-fields function, two catalogues: the reports differ in their
            # figures, not in what identifies a row.
            'app/modules/closing/day_recon_drilldown.py': ('report_drilldown',),
            # The card-settlement tally (§CORE-17.3c). Its gap statuses are dereferenced from
            # `external_credit_recon.GAP_STATUSES`, so "we do not know" cannot become a measured $0.
            'app/modules/closing/external_credit_drilldown.py': ('report_drilldown',),
            # The 3-way recon's selection strip (§CORE-17.3e). ALSO the one home for the flattening
            # the strip is folded over (`flat_rows`) and the strip itself (`totals`), because the
            # screen flattens the same payload for its cards and two flattenings are two populations.
            'app/modules/closing/three_way_drilldown.py': ('report_drilldown',),
            # The cash-pickup footers (§CORE-17.3e). The PLAIN tile shape deliberately: every filter
            # on that screen is a query parameter, so the server's fold already covers the envelopes
            # on screen and `drill-narrow` is NOT wired in (locked, harness §F5).
            'app/modules/closing/pickup_drilldown.py': ('report_drilldown',),
            # The close recon's four counts (§CORE-17.3f). Its rows are ACCUSATIONS, so the panel is
            # the accusations themselves and the rows a count excludes stay in it as context. The
            # rule that made a row a block is NOT here: `router._money_issues` keeps that.
            'app/modules/closing/recon_drilldown.py': ('report_drilldown',),
            # The DM-verify chargeback strip (§CORE-17.3f) — money out of a named person's pay,
            # opening to the missed verifications it charges for.
            'app/modules/closing/ops_chargeback_drilldown.py': ('report_drilldown',),
            # §CLOSING-02 — Declared vs Captured. TWO panels, because its two figures are Σ over the
            # SAME row out of DIFFERENT feeds (the closing sheets and the sales pair), which is
            # §CORE-17.3m's grain rule and this report's whole subject. The three verdict COUNTS get
            # no panel: the table below them IS that list. The absence reason on a captured row is
            # DEREFERENCED from `declared_feed_recon`, which is the one home for telling the four
            # absences apart — re-deciding it here could blame a rep for a feed nobody loaded.
            'app/modules/closing/declared_feed_drilldown.py': ('report_drilldown',),
            # The Management Envelope Receipt (§CORE-17.3g). THREE PEOPLE COUNT ONE ENVELOPE, in
            # three tables, so each figure names only the ones it was read out of — and it serves a
            # SECOND panel family, one per By-employee line, the owner's employee-level cash
            # variance. An uncounted envelope is shown and never folded in at $0.00.
            'app/modules/closing/envelope_drilldown.py': ('report_drilldown',),
            # The Daily Closing dashboard's tile strip (§CORE-17.3g). The grain is the STORE-DAY and
            # that is a fact, not a preference: `verified_overlay` makes a DM's correction
            # authoritative for the store-day, so a rep-day fold would not add up to the tile on any
            # corrected day (locked, harness §D3). The two cash tiles are declared in
            # `recon_drilldown.py` instead, beside the counts they share `_money_issues` with.
            'app/modules/closing/rollup_drilldown.py': ('report_drilldown',),
            # The CRM's three report tabs (§CORE-17.3h). The file where the house rule for a
            # QUOTIENT is written down: `win_rate` and `close_rate` get no panel at all, because no
            # set of leads adds up to a percentage, so they offer the counts they divide. Also the
            # one place the funnel's silent drop of an unknown-stage lead is made visible.
            'app/modules/crm/reports_drilldown.py': ('report_drilldown',),
            # The Helpdesk dashboard's tiles (§CORE-17.3h). Same ruling for an AVERAGE: the mean
            # resolution time opens to `measured`, its own denominator, which this declaration folds
            # and the endpoint now also shows on the tile.
            'app/modules/helpdesk/dashboard_drilldown.py': ('report_drilldown',),
            # The Closing & Cash Pickup Status tiles (§CORE-17.3i). The first declaration whose most
            # important rows name NO table: a store-day nobody filed anything for was read out of
            # nothing and is on the report because the roster expected it, which is served as a
            # stated untraceable origin. Both state vocabularies are dereferenced, never re-spelled.
            'app/modules/closing/chain_drilldown.py': ('report_drilldown',),
            # Management Review's attempt groups (§CORE-17.3i). A turned-away submit is shown and
            # never counted — `submit_refusal.is_real_try` stays the one rule for what is a try.
            'app/modules/closing/attempts_drilldown.py': ('report_drilldown',),
            # The Balance Sheet and the Cash Flow (§CORE-17.3j) — ONE declaration, because they
            # share a grain: both read one stored `account_statements` row and render its
            # sections/lines, and the cash flow's lines ARE balance-sheet line keys. The first
            # declaration to say where provenance STOPS: a stored statement does not record which
            # feed each line was read out of, so a feed is named only where the booking code reads
            # it unconditionally and a config-resolved line names none.
            'app/modules/account/statement_drilldown.py': ('report_drilldown',),
            # The Current Monetary Liabilities tiles (§CORE-17.3j). Six figures composed from four
            # other derivations, each now openable: the feed the RESOLVED payable basis read, one
            # row per person with the hours table that answered for them, and an unset rent shown
            # without a figure and never counted.
            'app/modules/account/liabilities_drilldown.py': ('report_drilldown',),
            # The PAYROLL-AND-WORKFORCE family (§CORE-17.3x) — the Payroll Report, StoreOps Reports
            # — Hours & Payroll, and the Hours Budget. TWO modules, not one, and the reason is the
            # grain rule: a PERSON over a PAY PERIOD whose hours may come out of the clock, the plan
            # or nothing, against a STORE over a WORK-WEEK whose hours come out of the plan alone.
            # The two payroll screens share one grain exactly, so they share ONE catalogue; a second
            # over it would be the sibling derivation the index rules forbid. Also where the shared
            # fold learned that the COLUMN a figure was read out of can differ per row, additively.
            'app/modules/storeops/payroll_hours_drilldown.py': ('report_drilldown',),
            'app/modules/storeops/scheduled_hours_drilldown.py': ('report_drilldown',),
            # The three commission DISAGREEMENT reports (§CORE-17.3k) — Pay Discrepancy,
            # Carrier Earned vs Employee Paid and Commission received over M1–M12. ONE module, THREE
            # catalogues, because they do NOT share a row grain (a commission line, a rep, a money
            # stream in a month) but they do share the provenance rule, the "one side was never
            # reported" vocabulary and the snapshot ruling — three files would be three answers to
            # one question. Also where a DIFFERENCE is shown to be drillable when it is folded per
            # row rather than subtracted from two other totals.
            'app/modules/commcalc/carrier_pay_drilldown.py': ('report_drilldown',),
            # The seven DEVICE-ASSET reports (§CORE-17.3l) — the Asset Ledger, the Charges
            # Dashboard, On-Inventory by Store, Weekly Owed to Distributor, the On-Inventory ↔ POS
            # recon, Inter-store Borrowed Money and Asset Lending. ONE module, SEVEN catalogues,
            # because the family has SIX row grains (measured, not assumed: four of them are keyed
            # by a store and are still four different populations in three different units) and
            # exactly THREE provenance answers — the distributor's unit ledger, the POS unit
            # snapshot named PER ROW, and the financing records the platform writes itself. Also
            # the one home for the per-row RMA net-loss rule the charges endpoint used to fold
            # inline, and for `GET /asset/summary`'s own On-Inventory predicate, so a tile and its
            # evidence cannot come to mean different rows.
            'app/modules/asset/device_asset_drilldown.py': ('report_drilldown',),
            # The five TARGETS reports (§CORE-17.3m) — Targets, Accessory Targets, the Action
            # Plan, My Targets and the Manager Report Cards. ONE module, SIX catalogues,
            # because the family has SIX measured grains (measured, not assumed: a scope-DAY,
            # a store-MONTH in three different provenance answers, a rep row and a
            # district-manager row) and FOUR provenance answers — the sales feeds, the
            # declared closing counts, the target settings rows and the shift schedule. A
            # TARGET is a declared number, never a measurement, so an unset one is reported as
            # unset rather than folded in as zero.
            'app/modules/commcalc/targets_drilldown.py': ('report_drilldown',),
            # The three BILL-PAYMENT / PROCESSOR MONEY reports (§CORE-17.3n) — Bill Payments, the
            # fee reconciliation and the processor's daily debits & credits. ONE module, THREE
            # catalogues, because a catalogue is per ROW LIST and these are three different
            # populations; SEVEN measured grains, of which SIX are keyed by a store-day and are
            # separated only by PROVENANCE — what the register rang, what the processor's own
            # report says, and a third naming both. A side that reported nothing is shown without
            # a figure, never netted at $0.00.
            'app/modules/commcalc/processor_money_drilldown.py': ('report_drilldown',),
            # The three ACCESSORY MONEY reports (§CORE-17.3o) — the Accessory Target Allocation,
            # the Accessory Cost Audit and the Accessory Flags. ONE module, THREE catalogues,
            # because a catalogue is per ROW LIST and these are three different populations (a
            # store, a rep, a flagged sale line); EIGHT measured grains over those three rows and
            # FIVE provenance answers — the sales display union, the monthly upload on its own, the
            # product catalogue, the target settings row and the org's flag-rules row. Where the
            # ruling that a PANEL EXPLAINS THE FIGURE, NOT THE TABLE is written down: two of these
            # screens filter in the browser WITHOUT re-folding their tiles, so narrowing their
            # panels would move a figure and `drill-narrow` is deliberately not wired to any of
            # them. Also the first declaration whose most important figure was read out of NOTHING:
            # a store's share of a company goal is derived on the load and stored nowhere.
            'app/modules/commcalc/accessory_money_drilldown.py': ('report_drilldown',),
            # The four DEVICE-FINANCING reports (§CORE-17.3p). SIX measured grains over FOUR row
            # shapes, two of which are the SAME device row in the SAME unit separated only by which
            # relation answered — the distributor's CLAIM against the carrier's PAYMENT, which is the
            # dispute the report exists to settle. Where the §CORE-17.3o call is made FOUR WAYS in
            # one batch: three of these screens print the server's own fold and their panels are
            # deliberately not narrowed, while the financing report folds its two tiles in the
            # browser and therefore is. Also the batch whose duplicate check found the grain ALREADY
            # DECLARED: the weekly lending batches are the device-asset family's catalogue, bound by
            # identity rather than re-written, and the defect was that the PayGo page threw away a
            # panel its own endpoint had been serving since batch 12.
            'app/modules/commcalc/device_financing_drilldown.py': ('report_drilldown',),
            # The four EXECUTIVE-MONTH reports (§CORE-17.3q) — the Owner Overview, Executive MTD,
            # This Month's Focus and the Total Compensation trend, which are the surfaces the owner
            # looks at most. ELEVEN measured grains over FIVE row shapes and SIX provenance answers
            # (the calculated rep-commission snapshot, the recorded chargeback, the sales display
            # union resolved off the read's own meta, the target settings, the store roster — and
            # one relation this platform CANNOT NAME, because no migration here creates it, which is
            # reported as a stated countable untraceable origin on every row rather than guessed).
            # Two of its grains are the SAME roster store row in the SAME unit separated only by
            # which relation answered: how many stores exist against how many carry a goal somebody
            # saved. Where the §CORE-17.3o call is made BOTH ways: three of these pages print the
            # server's own fold and their panels are deliberately not narrowed, while the
            # compensation trend's by-rep view folds its two tiles in the browser and therefore is.
            # Also the batch that declined a whole VIEW: every figure on the by-account compensation
            # trend is refused a panel, with four distinct reasons.
            'app/modules/commcalc/exec_month_drilldown.py': ('report_drilldown',),
            # The four SALES-AND-MARGIN reports (§CORE-17.3r) — the Gross Profit Report, the
            # Retention Analysis, the Sales Comparison and Zero Sales, which together answer *what
            # did we sell, and at what margin*. FOURTEEN measured grains over SIX row shapes and
            # SEVEN provenance answers — the sales display union resolved off each read's OWN meta,
            # the month-end upload on its own, the carrier payment feed (or the MA statement pair,
            # whichever the producer's own gate says filled the column), the subscriber residual
            # report, the calculated rep-commission snapshot, the entered expense sheet and the
            # shift schedule. Where NET PROFIT is shown to be drillable because the producer does
            # the subtraction INSIDE its per-store loop and the header is Σ of those rows, while
            # every Δ on the Sales Comparison is refused because that one is taken at the TOTAL from
            # two independent window folds and BOTH its sides drill. Also where the two comparison
            # WINDOWS name their own provenance separately, because a different period may have been
            # answered out of different tables — the sharpest §CORE-17.3n case in the programme.
            # Two of the Zero Sales tiles name NO SOURCE AT ALL and say so countably: 'nothing landed
            # for this store-day' is an absence, not a row in a relation. The §CORE-17.3o call is
            # made BOTH ways: the GP page folds its seven tiles in the browser and is therefore
            # narrowed, while the other three print the server's own fold and are deliberately not —
            # and one of those three contains `reduce(` twice, inside its dry-run digest preview, so
            # the lock asserts the narrowed page's fold by its ACTUAL TEXT rather than by the word.
            'app/modules/commcalc/sales_margin_drilldown.py': ('report_drilldown',),
            # WHAT THE CARRIER OWES vs WHAT IT PAID (§CORE-17.3s) — one declaration for the four
            # reports that answer that from four angles: the multi-month expected/earned ladder, the
            # processor roll-up, the denied-appeal claw-back, and the cell COUNT the other three's
            # money is computed on. Nine grains in four row shapes. All four pages print the
            # SERVER's own fold and none is narrowed, which `harness_batch19_drilldown.py` §F locks
            # in both directions off each page's own text.
            'app/modules/commcalc/carrier_owed_drilldown.py': ('report_drilldown',),
            # HOW ARE REPS AND STORES PERFORMING, AND WHO NEEDS COACHING (§CORE-17.3t) — one
            # declaration for the four reports that answer that from four angles: Productivity,
            # Rep Coaching, Autopay Opportunity and Failing KPIs. TWELVE measured grains in four
            # row shapes, and the programme's FIRST caller for `UNIT_HOURS`. The §CORE-17.3o call
            # is made BOTH ways inside one family — Coaching and Failing KPIs fold their own tiles
            # in the browser and are narrowed, Productivity and Autopay put every filter in the
            # query string and are deliberately not — and `harness_batch20_drilldown.py` §F locks
            # each decision in both directions off each page's own text. This family is also full
            # of RATES, so it refuses a lot (§CORE-17.3h) and every refusal names the two figures
            # it offers instead.
            'app/modules/commcalc/performance_coaching_drilldown.py': ('report_drilldown',),
            # §CORE-17.3u — the four SALES-OUTCOME reports (peer comparison, product mix, spiff
            # impact, tax collected): one question worked through in sequence over ONE month of the
            # SAME retail sales lines. ELEVEN grains, three of them on a single spiff store row
            # separated by PROVENANCE ALONE (§CORE-17.3n), and two row shapes inside the tax report
            # because a store figure folds store rows while a store's OWN figure folds its days.
            # It extended the one home ADDITIVELY in that PR with `served_tiles` — 'which figures
            # can be explained at all' — which batch 20's declaration had been holding a private
            # copy of and now dereferences by identity. `harness_batch21_drilldown.py` §A3 fails
            # the build if either stops dereferencing it.
            'app/modules/commcalc/sales_outcome_drilldown.py': ('report_drilldown',),
            # The last four COMMCALC reports (§CORE-17.3v) — the commission dashboard, the Rep
            # Breakdown tab, the subsidy-coverage report and the activation counts. THREE
            # declarations, not one, because they share no row grain: a rep-per-store row of
            # payout, a store-month of entitlement and a store row of counted devices are three
            # different subjects. The dashboard and the breakdown DO share one, so they share one
            # catalogue — a second one over the same grain is the sibling derivation the index
            # rules forbid. Where the §CORE-17.3o call is made BOTH ways inside one catalogue: the
            # dashboard prints the server's fold and is not narrowed, the breakdown folds the
            # reader's own rep+store filter in the browser and is. SIX grains over three row
            # shapes, two of them separated by PROVENANCE ALONE (§CORE-17.3n) — what an absent
            # entitlement month was worth, priced from a declared rate or observed from the
            # store's own payments. Also the batch that REPORTED three live defects rather than
            # coding around them: a footer that sums a never-recorded slice as zero, two tabs
            # meaning different things by `installment`, and a per-market fold whose own assembly
            # destroys the market label before it prints — which is why no per-market panel is
            # served at all.
            'app/modules/commcalc/rep_incentive_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/entitlement_coverage_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/activation_count_drilldown.py': ('report_drilldown',),
            # BATCH 23 (§CORE-17.3w) — the FINANCE set. THREE declarations over three questions a
            # reader asks separately: what the distributor BILLED us for devices and what of it is
            # still owed (ONE family over two screens, because both fold the units the distributor
            # billed at the same store row, out of the same two relations, and the payable report
            # already imports the voided-invoice rule from the purchases one); what the stock on the
            # shelf is WORTH and whether the two sources that claim to know agree; and whether the
            # money the distributor CREDITED matches what the residual report says was EARNED. The
            # inventory family is the sharpest §CORE-17.3n case in finance so far — one store row,
            # one unit, and the relation that answered decided PER ROW by a precedence rule, read
            # off the producer's own basis word and never guessed. It is also the batch that
            # REPORTED eight live defects rather than coding around them, among them a page that
            # prints two columns under one header computed by two different rules, and a silent
            # fifteen-row cap on the evidence behind an accusation.
            'app/modules/account/device_billing_drilldown.py': ('report_drilldown',),
            'app/modules/account/inventory_value_drilldown.py': ('report_drilldown',),
            'app/modules/account/memo_recon_drilldown.py': ('report_drilldown',),
            # §COMMCALC-43 — the brought-device-then-upgrade flag, TWO grains in one catalogue
            # because its two money figures are Σ over the same row out of DIFFERENT feeds: the
            # sales feed for what the store rang, the carrier's payment feed for the chargeback
            # amount. One panel for both would attribute a carrier payment to the sales feed.
            'app/modules/commcalc/byod_upgrade_drilldown.py': ('report_drilldown',),
            # §COMMCALC-49 — the Accessory Order Plan. FOUR COUNTS, not money, folded over the
            # report's own model rows. It is the first declaration whose central figure is a
            # PROJECTION, so the honesty rule it needs from the shared mechanism is the one about a
            # row with no number: a model nobody has ruled on carries no projected units and is
            # served with its reason, because a projected zero would read as a decision to stop
            # ordering for it. It refuses the two rates and every accessory quantity — the latter
            # being the device total times a declared per-device rate, which has no rows of its own.
            'app/modules/commcalc/accessory_order_drilldown.py': ('_dd',),
        },
    },
    # ── §CORE-14 (owner report 2026-10-24) — what an INTERNAL SELF-CALL must prove ───────────────
    'internal_self_call': {
        "question": 'When a handler reaches a sibling module through the platform\'s OWN HTTP '
                    'surface (`INTERNAL_API_BASE_URL`, default 127.0.0.1:8000), what identity must '
                    'that call carry? TWO facts, not one: WHO is calling (the caller\'s bearer '
                    'token) and WHICH TENANT they are acting as (`x-active-org`, as '
                    '`tenant_middleware` already validated it). The call re-enters the same gate '
                    'from the outside, so anything it drops is a fact the gate must guess — and '
                    'the gate correctly refuses to. Dropping the token read as **$0.00 owed** on '
                    'every envelope (2026-08-04); dropping the tenant read as a **409 '
                    '`tenant_choice_required`** on the envelope payout screen for the four logins '
                    'that belong to more than one company and have no `is_default_org` row (owner '
                    'report 2026-10-24: `core.access_log` shows both 409s with `actor_auth_id` '
                    'NULL between the outer 200s of `ab08bb15…` acting as Luxelink). The NOTE a '
                    'refused self-call turns into is part of the fact: 401/403/404/409 each read '
                    'as a cause, never as a number. Stamping the already-validated tenant is not a '
                    'widening — `x-active-org` is honoured only when it names one of the '
                    'caller\'s own memberships.',
        "homes": ('app/core/internal_call.py',),
        "index": ('CORE-16',),
        "locks": ('harness_internal_self_call.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            # mod-retail-ops' envelope payout + the closing-expense P&L push (5 self-calls).
            'app/modules/closing/router.py': ('internal_api_base', 'self_call_headers',
                                              'self_call_note'),
            # mod-people's PTO / payroll-expense / gross / additional-payroll P&L pushes and the
            # bulk targets read (5 self-calls).
            'app/modules/storeops/router.py': ('internal_api_base', 'self_call_headers'),
        },
    },
    # ── §CORE-07 (owner report 2026-10-14) — which PERSON a login belongs to ─────────────────────
    'self_lockout': {
        "question": 'Would THIS administrative write take away the authority that just made it? '
                    'Every role / membership / role-permission write in `core/router.py` gates on '
                    '`can_edit_setting(caller, \'security\')` and may then write the very row or '
                    'role that answer was computed from — a one-way door, because no screen is left '
                    'that could undo it (the `ss@1313global.us` Vzone membership, owner '
                    '2026-10-16). The AUTHORITY ANSWER ITSELF is part of this fact and lives here '
                    'too: `can_edit_setting` moved out of `core/router.py` and the router '
                    'dereferences it, because a guard asking a different question than the gate '
                    'protects a different door. Which writers are EXCUSED is part of the fact and '
                    'is written down — provisioning, elevation, reinstatement and the deliberate '
                    'self hand-over can only add authority or ARE the hand-over.',
        "homes": ('app/modules/core/self_lockout.py',),
        "index": ('CORE-12',),
        "locks": ('harness_self_lockout.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            # assign_role, bulk_assign, create_role, update_role, deactivate_user,
            # revoke_super_admin and purge_app_user — every writer of authority in the module.
            'app/modules/core/router.py': ('_self_lockout',),
        },
    },
    'app_user_employee_link': {
        "question": 'Which EMPLOYEE does a login belong to, and in what order is that decided when '
                    'an administrator names one, the row already carries one, and the roster '
                    'resolves one by email? `storeops.app_users.employee_id` is the single column '
                    'that ties a login to its person, and with it NULL the login is NOBODY: '
                    '`core/scope.py::visible_people_keyset` narrows a people_visibility-\'self\' '
                    'role to the empty set, so the rep opens Schedule to a blank week, and '
                    '`closing/router.py::_closer_gate` refuses their closing `closer_no_name`. '
                    'Three paths wrote such a row and each decided for itself: the Roles grid sent '
                    'the id, the add-a-person form and the spreadsheet upload did not. Which '
                    'writers are EXCUSED is part of the same fact and is written down — tenant '
                    'creation and super-admin elevation mint an \'all\'-scope administrator, who '
                    'is on no store\'s roster. Sibling of §19.45 THE ONE MINT, which gives the '
                    'PERSON a business id; this one makes the LOGIN point at it.',
        "homes": ('app/modules/core/app_user_identity.py',),
        "index": ('CORE-07',),
        "locks": ('harness_app_user_employee_link.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        # The lock verifies it against the real import graph.
        "callers": {
            # POST /users/assign and POST /users/bulk-assign — the two person-scoped login writers.
            # `_ensure_employee` in the same module supplies the roster half of the answer.
            'app/modules/core/router.py': ('_app_user_identity',),
        },
    },
    'stored_credential_column': {
        "question": 'Which stored COLUMNS hold a credential — a portal password, an authenticator '
                    'secret, a live browser session, a vendor API key — and therefore must be '
                    'ENCRYPTED AT REST? Which primitive opens one (text vs the JSONB envelope), '
                    'which schema the table is in and which columns address one of its rows are the '
                    'same fact, read again by the backfill, and live here too (`json_columns`, '
                    '`table_schema`, `table_key`). A credential-looking column that is deliberately '
                    'NOT encrypted is part of the same fact and carries its reason in writing '
                    '(`NOT_A_SECRET`), because an excuse nobody wrote down is indistinguishable '
                    'from an oversight.',
        "homes": ('app/modules/core/portal_secret.py',),
        "index": ('CORE-03',),
        "locks": ('harness_portal_secret_lock.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            # the six sweep config readers, the status/session write chokepoint, every config upsert,
            # `_ds_rows` (THE data_source read) and `save_data_source`
            'app/modules/commcalc/router.py': ('_psec',),
            # the credit-memo sweep's own portal_pass read — the one credential read that was never
            # in commcalc/router.py
            'app/modules/account/router.py': ('_psec',),
            # the Google Places API key: `get_config` is its ONE reader, the config PUT its writer
            'app/modules/storeops/google_reviews.py': ('_psec',),
            'app/modules/storeops/router.py': ('_psec',),
            # the platform's own vendor API tokens (mig 090) — the sibling §D's migration scan found
            'app/modules/billing/router.py': ('_psec',),
            # a minted OAuth token, cached on the login row it came from
            'app/modules/supply/api_credential.py': ('_psec',),
            # seals what was already stored, iterating the registry so it owns no table list
            'app/core/encryption_backfill.py': ('portal_secret',),
        },
    },
    # ── §CORE-04 (measured 2026-10-12) — and the rows ALREADY stored ─────────────────────────────
    'encryption_backfill_coverage': {
        "question": 'Which encrypted-at-rest column family has a sweep that seals the rows written '
                    'BEFORE the column became encrypted? "Encrypted on write" and "sealed at rest" '
                    'used to be two unrelated facts, so every new encrypted column started out half '
                    'protected and the application could not tell: two employees\' bank routing '
                    'numbers were readable in production on 2026-10-12 while run_all() claimed to '
                    'run every sweep. A column whose writes were ALWAYS encrypted owes no sweep and '
                    'says so in writing (`BORN_SEALED`), proved rather than trusted. And — the '
                    'second half, measured 2026-10-13 — WHAT sealing a family means is the '
                    'WRITER\'s to say, declared per family as `sealer` and APPLIED by the sweep: '
                    'every sweep read its writer\'s list of WHICH columns and then inlined its own '
                    '`crypto.encrypt`, which agreed by luck for a text column and did NOT for a '
                    'JSONB one, so the credential registry\'s JSONB half was skipped on every boot '
                    'and three live portal sessions stayed readable.',
        "homes": ('app/core/encryption_backfill.py',),
        "index": ('CORE-04',),
        "locks": ('harness_encryption_backfill_coverage.py',),
        # SNAPSHOT — written BY HAND (§50). These are the modules that import the home, DERIVED from
        # the import graph. The modules the registry HOLDS TO A SWEEP are a different set: the home
        # now imports THEM (each family's `sealer`), which is the opposite direction and does not
        # make them callers. `vision/router.py` is held to no sweep at all — it claims BORN_SEALED.
        # That set is enforced by the lock's §C coverage scan and §I sealer wiring, not by an import
        # edge — which is the whole point of the lock: a module can start encrypting without ever
        # referencing this one.
        "callers": {
            # the no-shell route: ENCRYPTION_BACKFILL_ON_BOOT runs run_all once at startup
            'app/main.py': ('run_all',),
            # the HR button — the family whose absence WAS the defect; it now calls the shared sweep
            # instead of holding its own encrypt loop
            'app/modules/hr/router.py': ('encryption_backfill',),
            # the shell route for the PIN sweep
            'app/scripts/backfill_customer_pin_encryption.py': ('backfill_customer_pins',),
        },
    },
    # ── §COMMCALC-07 (owner directive 2026-10-12) — one pull loop, many feeds ───────────────────
    'portal_report_pull': {
        "question": 'Which code reaches a report on a portal we are logged into, presses its export '
                    'control and lands the file? It used to be whichever sweep needed it: the loop '
                    'and the feed were one lump inside merchant_portal_sweep.py, bound to card '
                    'settlement by three hard-coded calls, so a second feed on the same machinery '
                    'could only be built by copying the calibration ladder, the export words, the '
                    'honest-failure wording and the cold-session restore. A feed is now an ADAPTER '
                    '(four functions) and the loop is written once; three older sweeps still drive '
                    'their own and are PINNED with reasons in the lock, a count that may only shrink. '
                    'Second axis (§COMMCALC-37): WITH WHICH PARAMETERS was it run? A report is '
                    'identified by its page AND its filters, so the dates, dropdowns and detail '
                    'toggles are DECLARED per report in portal_calibration and set by '
                    'apply_report_params before the Run press and the export — and a report whose '
                    'declared parameters could not all be set is NOT exported, because the page '
                    'default under the asked-for date is the wrong figures wearing the right date. '
                    'Whether a report runs once per day is DERIVED from that declaration ({date} '
                    'appearing in a value), never a second switch that could disagree with it. '
                    'Third axis (COMMCALC-39): WHERE DOES THE DECLARATION COME FROM? Off the page, '
                    'not a guess — read_page_controls / control_selector / control_purpose / '
                    'suggest_params read a report page through the authenticated session and SUGGEST '
                    'its declaration, writing nothing, because a framework-generated control id '
                    'cannot be guessed and a wrong selector is now a report that never exports. '
                    'specs_for is the one resolver of a source\'s declared reports, so the capture '
                    'reads exactly the set the pull will run.',
        "homes": ('app/modules/commcalc/portal_report_pull.py',),
        "index": ('COMMCALC-07', 'COMMCALC-37', 'COMMCALC-39'),
        "locks": ('harness_portal_report_pull.py',),
        # SNAPSHOT — written BY HAND (§50): the modules that IMPORT the home, derived from the import
        # graph. The adapters import it for its helpers and are resolved BY it through the registry,
        # which is a runtime edge the import graph cannot see and the lock's §A proves instead.
        "callers": {
            # the card feed imports the moved names under their old spellings, which is what keeps
            # every one of its callers reading the same code it always did
            'app/modules/commcalc/merchant_portal_sweep.py': (
                'DEFAULT_WINDOW_DAYS', 'MAX_WINDOW_DAYS', 'date_range', 'make_pull_fn', 'next_expiry',
                'prp', 'pull_reports_on_page', 'session_ttl_hint', 'window_days'),
            'app/modules/commcalc/pos_report_sweep.py': ('prp',),
            # the THIRD adapter (§COMMCALC-38) — it reaches the loop for the same four helpers and
            # the same cold restore, which is the whole point of the extraction: a new portal feed is
            # four functions, not a copied sweep
            'app/modules/commcalc/supplier_invoice_sweep.py': ('prp',),
            # the login step (§COMMCALC-46) reuses this loop's PAGE READER and SELECTOR RULE whole —
            # "what controls does this page offer" is one question whether the page is a report page
            # or a sign-in page, and a second reader in the login path is the duplicate defect
            'app/modules/commcalc/login_progressive.py': ('prp',),
            # the editable report declaration (§COMMCALC-48) offers a tenant only the filter KINDS and
            # date TOKENS this loop can actually apply — an editor with its own list would offer a
            # filter the pull silently ignores, which looks like a declared filter and is not one
            'app/modules/commcalc/report_declaration.py': ('_prp',),
            # the live session's dispatch and the scheduled cron both ask the registry
            'app/modules/commcalc/router.py': ('_prp',),
        },
    },
    # ── §COMMCALC-38 (owner ask 2026-10-27) — reading an exported report's columns ───────────────
    'report_column_vocabulary': {
        "question": 'Which column of a downloaded report is which fact? Every portal feed faces it '
                    'before reading a figure: the export says "Trans Date", "Ext Price", "Center '
                    'Number". `pos_report_feed` answered it with a normalizer, a '
                    'longest-synonym-first header matcher and a header-row scorer, welded to its own '
                    'vocabulary — so a second feed needing the same three things against its own '
                    'vocabulary would have copied them. That copy fails expensively: `norm` is what '
                    'makes \'B-1115\' and \'b 1115\' one store key, and this platform has already '
                    'lost a phantom store holding $289 to a code compared under one rule here and '
                    'another rule there. So the MECHANISM is here and the VOCABULARY stays with each '
                    'feed, which also keeps every vendor and tenant spelling out of this module. '
                    '`money_or_none` DEREFERENCES merchant_portals.money and adds exactly one rule — '
                    'a blank cell is None, never 0.0 — because 0.0 asserts a supplier billed nothing '
                    'and a blank asserts nothing at all.',
        "homes": ('app/modules/commcalc/report_columns.py',),
        "index": ('COMMCALC-38',),
        "locks": ('harness_supplier_invoice_feed.py',),
        # SNAPSHOT — written BY HAND (§50). Both feeds keep their old public spellings
        # (`norm`/`map_headers`/`header_score`/`_cell`) as dereferences of this home, so every
        # existing caller of theirs reads the same code it always did.
        "callers": {
            'app/modules/commcalc/pos_report_feed.py': ('rc',),
            'app/modules/commcalc/supplier_invoice_feed.py': ('rc',),
            # the calibration capture (COMMCALC-39) compares a control's printed LABEL against the
            # words that say what it is for, and a label compared under one rule here while a header
            # is compared under another there is the drift this home exists to prevent
            'app/modules/commcalc/portal_report_pull.py': ('norm',),
            # a login page's controls are compared against the words that say what they take
            # (COMMCALC-46) — the same rule that compares a report header, for the same reason
            'app/modules/commcalc/login_progressive.py': ('rc',),
            # the report editor (§COMMCALC-48) folds a capture's suggested filter in only where the row
            # has no filter meaning the same thing, and "the same thing" is this rule, not a second one
            'app/modules/commcalc/report_declaration.py': ('_rc',),
        },
    },
    # ── §COMMCALC-47 — where a click on the live screen lands ────────────────────────────────────
    'live_frame_click_target': {
        "question": 'The live login streams a real browser and lets the operator click the picture; the '
                    'UI sends the click as a FRACTION of the image, so where on the live page is that? '
                    'It was answered inside the click handler by multiplying the fraction by the '
                    'configured viewport — but the frames come from Chrome\'s screencast, and every '
                    'frame arrives with metadata saying what it actually shows (deviceWidth / '
                    'deviceHeight, pageScaleFactor, offsetTop). The frame handler kept the JPEG and '
                    'the ack and DROPPED the metadata, so on any page whose visible size is not the '
                    'window every click landed off target by that ratio, worse the further from the '
                    'top-left the operator aimed (the owner\'s billing portal, 2026-10-29: "it shows '
                    'the live scren but cannot click on the box"). A frame is not a guess about a '
                    'viewport — it is an image that knows what it shows, so the geometry travels WITH '
                    'the frame and the click dereferences it. The arithmetic reduces exactly to the '
                    'old formula at scale 1 / offset 0 / device size = window, which is every portal '
                    'that works today.',
        "homes": ('app/modules/commcalc/live_frame.py',),
        "index": ('COMMCALC-47',),
        "locks": ('harness_live_frame.py',),
        # SNAPSHOT — written BY HAND (§50). Both live screens (the portal login and supply's vendor
        # window) drive the SAME live_login session, so this one translation serves both and there was
        # no sibling to fix.
        "callers": {
            'app/modules/commcalc/live_login.py': ('lf',),
        },
    },
    # ── §COMMCALC-46 — what a login page wants before it will show the password ──────────────────
    'login_first_step': {
        "question": 'Is the password on this login page, and if not, what does the page want first? '
                    'Every login driver must answer it before typing anything, and three of them '
                    'answered it differently: live_login._companyid_step and '
                    'vidapay_sweep.begin_login_b2bsoft each spelled ONE vendor\'s element ids '
                    '(#companyId / #btnSubmit / #username), and the GENERIC begin_login — the path '
                    'every other portal signs in through — did not answer it at all, reporting a '
                    'two-page sign-in as a form that renders unexpectedly (the owner\'s UPS billing '
                    'login, 2026-10-27). "The password is on a later page" is a SHAPE of login form, '
                    'not a fact about a vendor, so the decision is read off the page\'s own controls '
                    'and own words here, and a portal whose markup explains nothing declares its '
                    'fields in portal_calibration["login"] (RULE TWO: config, never code).',
        "homes": ('app/modules/commcalc/login_progressive.py',),
        "index": ('COMMCALC-46',),
        "locks": ('harness_login_progressive.py',),
        # SNAPSHOT — written BY HAND (§50). The planner is PURE and takes read_page_controls' output,
        # so the lock can prove every page shape without a browser; the driver is the only caller of
        # a page object, and both login paths plus the live session reach it rather than copying it.
        "callers": {
            'app/modules/commcalc/live_login.py': ('lp',),
            'app/modules/commcalc/vidapay_sweep.py': ('_lp',),
        },
    },
    # ── §COMMCALC-48 — a tenant's declared reports, as one editable thing ───────────────────────
    'report_declaration': {
        "question": 'Which reports does this login pull, and what must be set on each report\'s page '
                    'before the export is pressed? It was already config and not code \u2014 '
                    'pos_report_sweep.report_specs reads WHICH (portal_reports) and '
                    'portal_report_pull.report_params reads HOW (portal_calibration) \u2014 but '
                    'nothing could WRITE either column. No screen and no endpoint, so a tenant\'s '
                    'reports could be declared only by somebody with SQL access against the live '
                    'database, which from the owner\'s side is the same thing as hardcoded '
                    '(2026-10-29: "start definig the reports fromt eh back end again not hardpcded '
                    'but consumer defined editablke"). An editor wants ONE row per report with its '
                    'filters attached, and that is a THIRD shape of the same fact \u2014 so it is '
                    'DERIVED both ways and never stored, with columns(rows(src)) pinned as the '
                    'identity: opening the editor and saving changes nothing. per_day and problems '
                    'are derived for the same reason a stored copy could disagree with the filters '
                    'it describes. A capture SUGGESTS and never decides.',
        "homes": ('app/modules/commcalc/report_declaration.py',),
        "index": ('COMMCALC-48',),
        "locks": ('harness_report_declaration.py',),
        # SNAPSHOT — written BY HAND (§50). The home is PURE: the router is its only caller, and it
        # dereferences spec_key (§COMMCALC-39) and the filter vocabulary (§COMMCALC-37) rather than
        # restating either, so there is no second rule for "which report is this".
        "callers": {
            'app/modules/commcalc/router.py': ('rd',),
        },
    },
    # ── §COMMCALC-50 — what a page is telling us instead of showing a form ──────────────────────
    'portal_page_verdict': {
        "question": 'Is this page REFUSING us, CHALLENGING us, or THROTTLING us, rather than showing '
                    'us a form \u2014 and what should the operator be told? Four vocabularies '
                    'answered it: portal_block_marker rows (configurable, throttles only), '
                    'vidapay_sweep._BOT_PHRASES (a tuple in code), _looks_like_cloudflare\'s inline '
                    'markers, and a fourth tuple inside begin_login_b2bsoft that was OR\'d with the '
                    'first predicate, so a phrase a tenant configured was honoured by one half of '
                    'that condition and not the other. None held the owner\'s UPS billing wording '
                    '("you are trying to perform an action that is not allowed", 2026-10-30), so the '
                    'driver reported "Could not find the login form \u2014 it may render differently '
                    'than expected" about a page with no form on it. A page that refuses us is not a '
                    'login form that renders unexpectedly, so the words live in ONE table with a '
                    'kind column and a tenant adds a ROW, never a branch. The three kinds stay '
                    'three answers because the remedies differ \u2014 an address, a proxy, or time '
                    '\u2014 and only a throttle sets a cooldown. The rows are read WHOLE (the '
                    '\u00a74b.1 reading rule): the columns arrive one hand-applied schema change at '
                    'a time, so a '
                    'select naming them and a shorter one behind it is the column-block ladder that '
                    'rule forbids, and a row predating the kind column simply arrives without the '
                    'key and reads as the throttle row it always was. '
                    'THE SECOND HALF OF THE QUESTION (2026-10-31): "instead of showing a form" was '
                    'never asked. These markers match the page\'s MARKUP as well as its words '
                    '(_page_text reads content()) and "captcha" is a seeded challenge marker, so '
                    'every portal whose sign-in page ships a reCAPTCHA read as a bot wall \u2014 the '
                    'owner\'s UPS Store reporting portal says the word nine times, none of it '
                    'visible, on a working Username + Password form, and his live login spun 60s in '
                    '_wait_settle and then accused the egress IP while both boxes sat empty. '
                    'page_verdict now takes form_present and returns NOTHING when the form is '
                    'there; vidapay_sweep.login_form_present is the one home for that question and '
                    'is deliberately the same password-frame answer _wait_for_password already acts '
                    'on. Whether a human check GATES the submit stays a separate DOM question with '
                    'its own home (human_check_on_sign_in) \u2014 this marker list must not answer '
                    'it a second time by text search over markup.',
        "homes": ('app/modules/commcalc/portal_backoff.py',),
        "index": ('COMMCALC-50',),
        "locks": ('harness_portal_page_verdict.py',),
        # SNAPSHOT \u2014 written BY HAND (\u00a750). The driver keeps the predicates every caller already
        # asks (_looks_like_bot_wall / _looks_like_cloudflare) and dereferences the vocabulary; the
        # live login asks the driver's one reader. _CAPTCHA_TEXT is the EXCUSED sibling (\u00a7COMMCALC-11):
        # "must a human tick a box on this form" is a different question about a form that IS there.
        "callers": {
            # the driver: both predicates every caller already asks, plus the throttle exception it
            # has always raised from here
            'app/modules/commcalc/vidapay_sweep.py': ('PortalRateLimited', '_pb'),
            # the live login reaches the VOCABULARY through the driver's one reader (vp.page_refusal)
            # and the cooldown gate through the home directly
            'app/modules/commcalc/live_login.py': ('_pb',),
            # the cooldown gate the scheduler and the operator's own click both pass
            'app/modules/commcalc/router.py': ('_m',),
            # a landed upload says whether the source was in a cooldown when it was due
            'app/modules/commcalc/import_audit.py': ('_pb',),
            # the two sweeps that ask the vocabulary when a LOGIN failed, so a page refusing us at
            # the edge is named a refusal rather than a rejected credential (§COMMCALC-52). Both ask
            # it against the page's VISIBLE text, never its markup — that is the point of §51.
            'app/modules/commcalc/epay_sweep.py': ('_pb',),
            'app/modules/commcalc/dlar_sweep.py': ('_pb',),
        },
    },
    # ── §COMMCALC-52 — what happened to the login we just submitted ─────────────────────────────
    'login_outcome': {
        "question": 'We submitted a portal login. WHAT HAPPENED — and what is the ONE thing the '
                    'operator should do about it? Three portal drivers answered this three ways and '
                    'the worst of them was load-bearing. epay_sweep decided from page.content() — '
                    'the MARKUP — with `any(w in body for w in ("invalid","incorrect","failed")) '
                    'and "passwordinput" in body`, so a single-page application that merely BUNDLES '
                    '`var M={invalid:"Invalid user name or password"}` read as having SAID it. Its '
                    'only route to success was the vendor\'s own words in the document title, which '
                    'is a carrier name in code (RULE TWO) and a false negative for every other '
                    'tenant\'s portal. Measured 2026-10-31: 1,665 scheduled runs, every 30 minutes, '
                    'no gap; the last successful login 2026-10-09T19:30:43Z; then 1,056 CONSECUTIVE '
                    'runs reporting "credentials rejected", and three carrier feeds dark for 22 days '
                    'because nobody could tell whether that sentence was true. dlar_sweep\'s probe '
                    'was sound (ask for a page that needs a session) but named every failure a '
                    'rejected credential; vidapay_sweep\'s was sound too (DOM, never markup) but '
                    'asserted "Login rejected" for every still-on-the-form case and asked the '
                    'operator to work the rest out from a diagnostic dict. SO: six named outcomes, '
                    'each carrying the one remedy that applies to it — signed_in, '
                    'credentials_rejected, human_check, portal_refused, login_page_changed, unknown '
                    '— decided from STRUCTURE (is the form still on screen) and VISIBLE text '
                    '(document.body.innerText, or visible_from_html for a driver with no browser), '
                    'never markup. The evidence arguments are keyword-only with NO default, so a '
                    'caller that has not looked at the page cannot get an answer; '
                    'is_credential_problem is an ALLOWLIST, so an outcome nobody has ruled on stays '
                    'quiet about the password rather than blaming it. A challenge or refusal is '
                    'believed only when the form is ABSENT — §COMMCALC-50.8\'s rule applied '
                    'here, not copied here. EXCUSED SIBLING, pinned shrink-only: router.py\'s '
                    'MAILBOX sweep says "login rejected" for an IMAP [AUTHENTICATIONFAILED], which '
                    'is a protocol answer with no page to misread.',
        "homes": ('app/modules/commcalc/login_outcome.py',),
        "index": ('COMMCALC-52',),
        "locks": ('harness_login_outcome.py',),
        # SNAPSHOT — written BY HAND (§50). All three portal drivers dereference it for the NAME and
        # for the operator sentence; each still reads its own page its own way, because what the
        # facts are read OFF differs per driver (a browser frame, an HTTP document, an error region)
        # and only the decision is shared.
        "callers": {
            # the browser driver whose markup predicate caused the outage
            'app/modules/commcalc/epay_sweep.py': ('_lo',),
            # the browserless driver: same question, visible_from_html / form_in_html
            'app/modules/commcalc/dlar_sweep.py': ('_lo',),
            # the live login: names which of the two a still-on-the-form submit was
            'app/modules/commcalc/vidapay_sweep.py': ('_lo',),
        },
    },
    # ── §COMMCALC-53 — what day is it where this portal lives ────────────────────────────────────
    'tenant_today': {
        "question": 'A feed window, an "as of" stamp, a staleness age — WHAT DAY IS IT, for this '
                    'tenant? Three portal drivers resolved it against the CONTAINER\'s clock '
                    '(datetime.now(timezone.utc).date()), which is not any tenant\'s calendar. '
                    'Measured 2026-10-31: the comprehensive comp report has never once arrived '
                    'automatically since the leg was built — not one `epay auto-sweep` row in '
                    'commcalc.upload_log, for any org. Its slot is 23:30 in the tenant\'s zone, '
                    'deliberately, because the portal posts a day\'s compensation at about 23:30 '
                    'local; 23:30 America/New_York is 03:30 UTC THE NEXT DAY, so on eight '
                    'consecutive recorded runs the window asked for a day one ahead of the tenant\'s '
                    'and the portal answered zero rows — read as "no compensation posted", for four '
                    'months. The rule already had a home, written 2026-07-30 for a scheduled send, '
                    'whose docstring states this exact defect ("resolving a relative date off the '
                    'server\'s UTC day would put an 8pm-Eastern Friday run on Saturday\'s date"); '
                    'nothing in commcalc read it. §19.18\'s shape for the fifth time. Each driver '
                    'now BINDS that function (feed_today IS business_today — the same object, so the '
                    'lock asserts identity and no second zoneinfo ladder can appear), and `tz` is '
                    'keyword-only with NO default at every window-building entry point, because the '
                    'defect was a caller that had never asked the org what day it was and still got '
                    'an answer. The zone comes from the connector\'s own `*_sweep_config.timezone` '
                    'row, a column that already exists NOT NULL on every sweep config table, so '
                    'nothing had to be added to the schema. '
                    'EXCUSED SIBLING, pinned shrink-only: feed_period.month_state defaults `today` '
                    'to the container\'s day, excused because it is MONTH grain (the two clocks can '
                    'disagree only either side of midnight on the 1st) and `today` is already '
                    'injectable there, so a caller that cares passes the tenant\'s day.',
        "homes": ('app/modules/notify/report_filters.py',),
        "index": ('COMMCALC-53',),
        "locks": ('harness_feed_window_tenant_day.py',),
        # SNAPSHOT — written BY HAND (§50). Each driver binds the home's function object rather than
        # calling through a wrapper, so the dereference is provable by identity, not by grep.
        "callers": {
            # ── the three portal drivers, which never asked it until 2026-10-31. Each imports the
            # MODULE and binds its function object (`feed_today = _report_filters.business_today`),
            # so the dereference is provable by identity rather than by grep.
            # the window that cost the comp report every automatic run it ever made
            'app/modules/commcalc/epay_sweep.py': ('_report_filters',),
            # the invoice window and the PayGo cutoff
            'app/modules/commcalc/vip_sweep.py': ('_report_filters',),
            # the day an inventory snapshot is filed under
            'app/modules/commcalc/b2b_sweep.py': ('_report_filters',),
            # ── the notify side, which has read this home correctly since it was written
            'app/modules/notify/attention.py': ('report_filters',),
            'app/modules/notify/finance_reports.py': ('ReportConfigError',),
            'app/modules/notify/report_registry.py': ('ReportConfigError', '_business_today',
                                                      '_resolve_billing_friday', '_validate_filters'),
            'app/modules/notify/workforce_reports.py': ('_business_today',),
        },
    },
    # ── §COMMCALC-38 — what a supplier billed us ─────────────────────────────────────────────────
    'supplier_invoice_line': {
        "question": 'What did a supplier bill us, and which shipment was it for? Asked so every '
                    'charge can be checked against the sale that should have collected for it '
                    '(owner 2026-10-27). The platform already held DISTRIBUTOR invoices in '
                    'commcalc.vip_invoices, but those tables are keyed on one portal\'s internal '
                    'invoice id, carry no supplier and no source column, and are read by the P&L, '
                    'the balance sheet, device COGS and the Distributor Invoices report — so a '
                    'second supplier landed in them would be indistinguishable from the first and '
                    'would make every one of those readers wrong, silently, on money. This home '
                    'names no supplier. vip_invoices is the EXCUSED SIBLING that should fold into '
                    'it later, with the reason recorded in index COMMCALC-38 rather than left for '
                    'a reader to notice. The two recon references are kept verbatim AND normalized '
                    'by the one rule, because the supplier and the POS print a tracking number '
                    'differently and two rules would make one shipment read as unbilled on one side '
                    'and uncollected on the other.',
        "homes": ('app/modules/commcalc/supplier_invoice_feed.py',
                  'app/modules/commcalc/supplier_invoice_sweep.py'),
        "index": ('COMMCALC-38',),
        "locks": ('harness_supplier_invoice_feed.py',),
        "callers": {
            # the registry row dereferences the module's CONNECTOR constant rather than re-spelling
            # it — a literal there would be the second copy, and the two disagreeing is exactly how a
            # feed ends up dispatchable with no door (§12a.2a)
            'app/modules/commcalc/connector_registry.py': ('SUPPLIER_INVOICE_CONNECTOR',),
            # the dispatcher; the reconciliation that reads these rows is not built yet
            # (§COMMCALC-38.8 states that plainly rather than implying it shipped)
            'app/modules/commcalc/router.py': ('_SUPPLIER_INVOICE_CONNECTOR', 'sis'),
        },
    },
    'pos_report_declared_metric': {
        "question": 'What does an external POS report line MEAN in this tenant\'s own declared '
                    'vocabulary, and may that meaning be read as the tenant\'s word? The owner asked '
                    'for the portal\'s daily report to be checked against what the reps declare, and '
                    'the two name the same things differently. Spelling the categories in code would '
                    'break for the next tenant and is what he explicitly forbade; resolving a line to '
                    'the closest-looking category would produce a comparison that reads as evidence '
                    'and is not. So the match is a stated ladder and every row carries its BASIS: '
                    'only a declared mapping or the category\'s own label counts as the tenant\'s '
                    'word, and a line two categories could claim resolves to neither.',
        "homes": ('app/modules/commcalc/pos_report_feed.py',),
        "index": ('COMMCALC-07',),
        "locks": ('harness_pos_report_feed.py',),
        "callers": {
            'app/modules/commcalc/pos_report_sweep.py': ('feed',),
        },
    },
    # ── §4a.4 (owner decision 2026-10-19) — which POS departments are DEVICE departments ────────
    'device_department': {
        "question": 'Is this POS department a DEVICE department? Asked by the Gross Profit report '
                    '(both classification branches), by the P&L\'s sales classifier, and by the GP '
                    'Category Map screen that tells the owner what each department IS. The answer is '
                    'the org\'s OWN declared box-department set, with an explicit GP category '
                    'override allowed to beat it and the house labels as the default for an org that '
                    'declared nothing. It had TWO homes: the GP report read the declaration and the '
                    'P&L read the category overrides ALONE, so a department the owner declared but '
                    'never overrode was device revenue on one report and unbooked sales on the '
                    'other ($111,464.09 across Mar-Oct 2026). Where each side is configured: '
                    'index 4a.4.',
        "homes": ('app/modules/commcalc/gp_report.py',),
        "index": ('4a.4',),
        "locks": ('harness_gp_pnl_commission_parity.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04).
        #
        # READ THIS LIST CORRECTLY. The home is the GP ENGINE module, which also owns the VOID-TOKEN
        # vocabulary (`VOID_TOKENS` / `is_voided` / `countable_sale_skip_reason`) that half the
        # commcalc package dereferences. The derivation is per-MODULE, so every importer of that
        # engine lands here whatever it reads. The bindings that read THIS fact are
        # `device_department_test` and `_dept_classifier`; a caller binding only a void-token name
        # is listed because it imports the home, not because it classifies a department. What the
        # A-check buys even so: a NEW importer cannot land silently, and the next author is told
        # that this file answers the device-department question before they touch it.
        "callers": {
            # ── THE READERS OF THIS FACT ────────────────────────────────────────────────────────
            # The P&L's sales classifier (`_sales_classifier.is_device`) — the reader that had its
            # own answer until 2026-10-19 and now dereferences this one.
            'app/modules/account/coa.py': ('device_department_test',),
            # The GP report's one caller (`_compute_gp`, which hands over the org's declaration in
            # EVERY mode) and the GP Category Map screens, which must tell the owner the same thing
            # the books do about each department.
            'app/modules/commcalc/router.py': ('DEVICE_DEPTS', 'GP_CATEGORIES', 'ONDIGO_DEPT',
                                               '_GP_CATEGORIES_SET', '_GP_CATS_DEFAULT',
                                               '_GP_VOID_TOKENS', '_dept_classifier', '_gp',
                                               '_gp_is_voided', '_gp_skip_reason', '_ik',
                                               'bucket_map', 'calc_gp_report',
                                               'device_department_test'),
            # ── IMPORTERS OF THE VOID-TOKEN VOCABULARY IN THE SAME MODULE (not of this fact) ────
            'app/modules/commcalc/calculator.py': ('_VOID_TOKENS', '_is_voided'),
            'app/modules/commcalc/commission_drilldown.py': ('_is_voided',),
            'app/modules/commcalc/commission_engine.py': ('_VOID_TOKENS', '_is_voided'),
            'app/modules/commcalc/flags.py': ('_is_voided',),
            'app/modules/commcalc/ma_recon.py': ('_is_voided',),
            'app/modules/commcalc/plan_impact.py': ('_is_voided',),
            'app/modules/commcalc/sale_installment_engine.py': ('_VOID_TOKENS', '_is_voided'),
            'app/modules/commcalc/tax_collected.py': ('VOID_TOKENS', 'is_voided', 'safe_float'),
            'app/modules/commcalc/void_watchdog.py': ('_skip',),
            'app/modules/commcalc/watchdog_router.py': ('is_voided',),
            'app/modules/marketing/event_sales.py': ('is_voided',),
        },
    },
    # ── §37.11 (owner ruling 2026-10-20) — which FIGURE does a mapped sale line book ─────────────
    'sale_line_booking_basis': {
        "question": 'A POS sale line no P&L classifier claims is routed to a revenue line by the '
                    'org own rule map. WHICH FIGURE does that rule book, and does the line carry '
                    'money at all? Both answers used to be one word, ext_price, written straight '
                    'into the P&L sales scan: so a wallet refill would have booked the customer '
                    'pass-through money as income, a plan activation (which rings no gross at all, '
                    'because the customer pays the carrier) could not be booked or even reported, '
                    'and the blank department the unbooked report prints as (blank) was the one '
                    'department a rule could not name. The owner ruling of 2026-10-20 is that '
                    'commission and margin coming in ARE income, and only money reimbursed toward '
                    'phone cost is a subsidy, so the figure is a per-rule BASIS: ext (the gross) '
                    'or gp (the store margin). Where it is configured: index 37.11.',
        "homes": ('app/modules/account/sales_line_map.py',),
        "index": ('37.11',),
        "locks": ('harness_royalty_lock.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04).
        "callers": {
            # The P&L sales scan — the one booking path, and the only reader of the money gate.
            'app/modules/account/coa.py': ('_slm',),
            # The rule editor endpoint: it offers the vocabulary and validates against it rather
            # than spelling the fields, the bases or the blank label itself.
            'app/modules/account/router.py': ('sales_line_map',),
        },
    },
    # ── §COMMCALC-03 (owner directive 2026-10-12) — evaluate a store we do not own yet ───────────
    'multi_handset_one_buyer': {
        "question": 'Which buyers took more handsets in one day at one store than a family plausibly '
                    'would — counted ACROSS the receipts the sale was split over, from the LIVE '
                    'daily sales feed rather than anything uploaded for an evaluation?',
        "homes": ('app/modules/commcalc/multi_handset_day.py',),
        "index": ('COMMCALC-05',),
        "locks": ('harness_multi_handset_day.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50).
        #
        # THE SIBLING, NAMED SO NOBODY MERGES THEM. `commcalc/prospect_eval.py` (COMMCALC-03) asks
        # the SAME question of an UPLOADED carrier statement for a store we do not own. The two are
        # deliberately separate: that one reads `prospect_feed_row` and the carrier's own account
        # column weeks after the fact, this one reads the live POS feed the morning after, and
        # neither feed can answer for the other. The owner drew the line himself ("this would check
        # the flags for ongoing activation not the test data uploaded for evaluation").
        #
        # WHAT IS NOT HERE: no severity, area or label of its own (`flag_registry` owns all three),
        # no threshold (a `watchdog_rule` config row), and no idea of its own about which line is a
        # handset — the per-org `box_departments` set is INJECTED by the caller, the same membership
        # test `router._sales_cell_agg` counts boxes with. §G of the lock fails the build if this
        # module reads that config itself or defaults it to the house labels.
        "callers": {
            # The ONE sweep every watchdog runs on (one cron line, §53). It reads the sales lines
            # through `_read_sales` — which dereferences `router._open_month_source`, the one home
            # for "which sales table answers for this period" — injects this org's
            # `box_departments` and the shared void-token filter, and writes under the detector's
            # OWN `source` so the commission recalculation's flag pass cannot retire its findings.
            'app/modules/commcalc/watchdog_router.py': ('_handsets',),
        },
    },
    'store_takeover_evaluation': {
        "question": 'What would a store we are considering taking over be worth, from its own MA '
                    'commission / daily-TX / handset-order uploads against OUR average store '
                    'expenses with ITS actual rent — and which of its subscriber accounts carry '
                    'more lines than one month should?',
        "homes": ('app/modules/commcalc/prospect_eval.py',),
        "index": ('COMMCALC-03', 'COMMCALC-03.26', 'COMMCALC-03.27', 'COMMCALC-03.28'),
        "locks": ('harness_prospect_eval.py', 'harness_prospect_pl_trend.py',
                  'harness_upload_trace_coverage.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        #
        # NOTE WHAT IS NOT HERE. This home re-derives no booking rule: the evaluation books through
        # `account/ma_store_pnl` (`ma_commission_bookings` / `ma_tx_bookings` / `rebate_route`),
        # `account/device_cogs` (`ma_unit_price_from_rows` / `ma_sold_cost_from_rows`),
        # `account/coa` (`PL_SPEC` / `PL_SECTION` / `route_expense_line`), `account/engine._assemble`,
        # `account/analysis.pl_totals`, `commcalc/expenses_effective` and `commcalc/column_mapping` —
        # injected by `router._prospect_homes()` so the prospect P&L and the company P&L cannot
        # disagree. §D of the lock fails the build if this module grows a rule of its own.
        #
        # §COMMCALC-03.26 / .27 ADDED TWO MORE DEREFERENCES, NOT TWO MORE RULES:
        #   · the seller's declared accessory sale books its COST at `account/coa`'s own
        #     `account_config.accessory_cogs_pct` (mig 611, the rate the owner set to 20% on
        #     2026-06-20), injected — §X2 of the lock fails the build on a literal rate here;
        #   · the M1 / M2-M12 split reads `account/ma_store_pnl.ma_earned_month_ladder` and
        #     `ma_received_month_ladder`, with `commcalc/commission_legs` saying which rung is
        #     trailing and `commcalc/commission_ledger.LEG_LABELS` saying what the buckets are
        #     called. §X15 fails the build if this module parses a month out of a label itself.
        # The per-store rent is the SAME `apply_rent_override` §COMMCALC-03.6 shipped, now applied
        # once per column instead of once per prospect; §X6 holds that exactly one call site exists.
        #
        # §COMMCALC-03.28 ADDED THREE ONE-HOMES AND NO SECOND DERIVATION. The month-over-month P&L is
        # `evaluate` + `router._prospect_assemble` called once per month — the SAME booking path and
        # the SAME assembler the month report uses, which §Z of the lock proves by requiring a
        # column and that month's own report to agree on every line. `feeds_for_months` (which rows
        # belong to a month, off the registry's `period_scope`), `month_has_ended` (which
        # `default_period` now dereferences too) and `expense_window` (which of OUR months the
        # benchmark is taken over, nearest-first in either direction) are the three, and §Y/§Z fail
        # the build if a caller stops dereferencing any of them.
        "callers": {
            'app/modules/commcalc/router.py': ('_pev',),
        },
    },
    'leg_store_trend_shape': {
        "question": 'For each STORE, what did every month-of-life rung (M1..M12) of received '
                    'commission pay in each month of the window — and which rung is running down?',
        "homes": ('app/modules/commcalc/commission_leg_store_trend.py',),
        "index": ('COMMCALC-12',),
        "locks": ('harness_commission_leg_store_trend.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        #
        # NOTE WHAT IS NOT HERE. This home computes NO money. It is a PIVOT of the money
        # `router._commission_leg_trend_compute` already produced — the same read, the same carrier
        # classification (§58/§66), the same store identity (§64) — reshaped store-major with the
        # months side by side. §A of the lock fails the build if Σ(per-store) ever stops equalling the
        # company figure to the cent, which is the whole claim that this is a shape and not a sibling
        # derivation of the leg trend.
        #
        # THE RUNG CONVENTION IS `commcalc/commission_legs`'s, dereferenced: `ladder_key`,
        # `ladder_sort_key`, `months_present`, `ladder_column`, `ladder_label` and the Unlabelled
        # bucket. §F of the lock fails the build on a `range(` or a month COUNT here, so a rung the
        # feed carries can never be invisible — an M13 is a column the day it arrives.
        #
        # TWO CALLERS, ONE PIVOT (added 2026-10-16, §COMMCALC-15). The owner asked for the same
        # question of a store we do NOT own yet — "a seaprate block with m2-m12 for each store with
        # filters for all month and all stores" — and the live endpoint cannot answer it, because it
        # reads our own carrier feed and may never read a prospect's uploads (§COMMCALC-03.2's AST
        # lock). So `prospect_eval.commission_leg_store_months` builds the per-(month, store) cells
        # out of the two ladder one-homes and hands them to THIS pivot: the same rows, cells, deltas,
        # worst-rung read-out, rung columns and partial-month rule. §W9 of
        # `harness_prospect_eval.py` fails the build if that caller starts deriving any of them
        # itself, which is what a second block would have been.
        "callers": {
            'app/modules/commcalc/prospect_eval.py': ('_lst',),
            'app/modules/commcalc/router.py': ('_leg_store_pivot',),
        },
    },
    'prospect_feed_tally': {
        "question": 'How many rows has each store being evaluated got, per uploaded feed, per '
                    'month — and has that even been counted yet?',
        "homes": ('app/modules/commcalc/prospect_feed_tally.py',),
        "index": ('COMMCALC-14',),
        "locks": ('harness_prospect_feed_tally.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        #
        # TWO READINGS, ONE SHAPE, ONE ACCUMULATOR. `from_grouped` takes counts the DATABASE grouped
        # (migration 1085) and `from_rows` counts the rows here; both reach the shape through `_add`,
        # and §A of the lock fails the build if they ever disagree on equivalent input. The fast
        # reading is preferred and the slow one SAYS it was used, so a slow answer is never mistaken
        # for a fast one.
        #
        # WHY IT EXISTS AT ALL. The evaluations list used to carry this tally inline and compute it by
        # shipping every feed row here — 202,865 rows, 203 round trips, ~59 s on the acting org — so
        # the two-row answer the owner asked for was withheld for a minute and the list read as empty
        # (owner report 2026-10-16). The tally is a DECORATION and now loads beside that answer.
        #
        # UNKNOWN IS NOT ZERO. `missing_tally` is the one place "not counted yet" is distinguished
        # from "counted, and empty"; §C of the lock holds both the payload and the screen to it,
        # because printing 0 over 137,374 uploaded rows is the same wrongness in a cell.
        # The binding is the import alias, DERIVED from the AST import graph like every other entry
        # here (§50) — `router._prospect_tally_read` is the one function that uses it.
        "callers": {
            'app/modules/commcalc/router.py': ('_pft',),
        },
    },
    'peer_traffic_band': {
        "question": 'Which stores see comparable FOOT TRAFFIC (bill-payment visits), and how far is '
                    'each one behind the best and the median of its own band?',
        "homes": ('app/modules/commcalc/peer_comparison.py',),
        "index": ('59',),
        "locks": ('harness_peer_comparison.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_peercmp',),
            # §60 — the spiff-impact report ranks its own metric THROUGH this home
            # (`with_extra_metric` / `lagging` / `prompt_sentence`) so "behind" keeps one definition.
            'app/modules/commcalc/spiff_impact.py': ('_pc',),
            # §62 — the product-mix report dereferences the ratio, the median and the severity cut, and
            # its endpoint borrows `with_extra_metric` / `peer_items_by_store` for the store plan, so a
            # rep and their store are judged behind by one rule.
            'app/modules/commcalc/product_mix.py': ('_pc',),
            # §CORE-17.3u — the sales-outcome drill-down reads this home's OWN threshold and its own
            # default ranked metric (`BAND_MIN_PEERS`, `DEFAULT_GAP_METRIC`) so a panel's NOTE can
            # say what the report refused and why in the report's own numbers. It compares nothing
            # against either and re-bands nothing; `harness_batch21_drilldown.py` §A7 fails the
            # build if it stops dereferencing them.
            'app/modules/commcalc/sales_outcome_drilldown.py': ('_peer',),
        },
    },
    'device_price_band': {
        "question": 'What price band did the CUSTOMER pay for this device, which model was it, and is '
                    'this rep low on accessory $ per box AND on port-ins at the same time?',
        "homes": ('app/modules/commcalc/product_mix.py',),
        "index": ('62', 'COMMCALC-32'),
        "locks": ('harness_product_mix.py', 'harness_payables_device_rows.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            # `_sales_cell_agg` asks price_cfg for the band and the model rather than deciding either,
            # and the endpoint wires the report. §62.
            'app/modules/commcalc/router.py': ('_pmix',),
            # The takeover evaluation's "what was sold" breakup reads the SAME home for what a product
            # description means — `product_class_of` / `product_breakup` beside `model_of`, so a
            # tablet is one thing on the platform. §COMMCALC-03.21.
            'app/modules/commcalc/prospect_eval.py': ('_pm',),
            # §CORE-17.3u — the sales-outcome drill-down reads this home's own BOX FLOOR and its own
            # VERDICT WORDS rather than re-testing a rep against two medians: 'low on both signals'
            # is `VERDICTS[0][0]`, read off the row the producer already judged. The declaration
            # spells no verdict and holds no floor; `harness_batch21_drilldown.py` §A7/§A8c fail the
            # build if either becomes a literal there again.
            'app/modules/commcalc/sales_outcome_drilldown.py': ('_pmix',),
            # §COMMCALC-32 — the payables ledger asks this home whether the line sold a DEVICE AT ALL
            # (`no_device_declared`, the declared-basis half of `product_class_of`) instead of
            # inferring it from a missing product name. A line the feed declares as the customer's own
            # phone is not a device payable, and `harness_payables_device_rows.py` §A9/§D2 fail the
            # build if that question is answered here again by a literal token or a blank-name guess.
            'app/modules/payables/engine.py': ('_pm',),
        },
    },
    'device_recovery_amount': {
        "question": 'How much has the carrier ALREADY recovered against this device - deducted on '
                    'its own statement or reimbursed - and does anything actually MEASURE that, or '
                    'is the answer unknown?',
        "homes": ('app/modules/payables/recovered_amount.py',),
        "index": ('COMMCALC-33',),
        "locks": ('harness_payables_recovered_amount.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04, and rewrote 51 of them on 2026-10-26). The lock verifies it against the
        # real import graph.
        "callers": {
            # The ledger WRITER. All three recovery sources (the device's own declared column, the
            # Boost/VIP classification, the ePay feed) fold through `resolve` here, so the amount
            # and its basis cannot disagree, and the `rebate_amount = 0.0` placeholder that billed
            # $544,447.78 of netted devices to the owner cannot return — §F of the lock fails the
            # build if it does. §COMMCALC-33.
            'app/modules/payables/engine.py': ('_rec',),
            # The EXPOSURE figure every surface headlines. `stamp_aging` nets through this home
            # rather than re-deriving the subtraction, and reports what was NOT netted beside the
            # total instead of dropping it — which an earlier draft did, zeroing Boost's real
            # exposure. §COMMCALC-33.4.
            'app/modules/payables/unsold_inventory.py': ('_rec',),
        },
    },
    'payable_billing_period': {
        "question": 'Which BILLING PERIOD does this dated payable money fall in, and WHAT SAID SO - '
                    'a period the carrier stamped, a declared weekly rhythm, or no rhythm at all?',
        "homes": ('app/modules/payables/billing_period.py',),
        "index": ('COMMCALC-34',),
        "locks": ('harness_billing_period.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04, and rewrote 51 of them on 2026-10-26). The lock verifies it against the
        # real import graph.
        "callers": {
            # `Weekly Owed to Distributor` (§COMMCALC-34). The report used to BE the answer: it read
            # one carrier's weekly consignment column and nothing else, so a tenant whose carrier
            # raises an invoice per order saw a sum over an empty set. It now dereferences the
            # cadence — `resolve_cadence` / `week_window` / `roll_up` — and derives no rhythm of its
            # own. A carrier's stamped period still WINS through this home, never recomputed.
            'app/modules/asset/router.py': ('_bp',),
        },
    },
    'payable_dated_owed': {
        "question": 'What is OWED, on which date, at which grain, and which source said so - the '
                    'per-device payable ledger or the processor\'s own dated feed?',
        "homes": ('app/modules/payables/owed_read.py',),
        "index": ('COMMCALC-34',),
        "locks": ('harness_billing_period.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04, and rewrote 51 of them on 2026-10-26). The lock verifies it against the
        # real import graph.
        "callers": {
            # The DAILY surface, `/payables/owed-by-date`. Its own ledger query and its own vendor
            # fallback are DELETED, not left beside this one — a dead second reader is a future
            # divergence that compiles (§19.18). All that remains of it here is the grouping by date.
            'app/modules/payables/router.py': ('_owed',),
            # The WEEKLY roll-up, which groups the SAME rows by billing period instead. Giving the
            # weekly report its own read was the obvious repair and would have been the duplicate
            # defect the house rules gate on: two paths answering "what do I owe", free to disagree
            # the moment either learned something the other did not. §F13 of the lock fails the
            # build if a second definition of the read appears anywhere in the triangle.
            'app/modules/asset/router.py': ('_owed_r',),
        },
    },
    'kpi_column_meaning': {
        "question": 'Which column carries which KPI actual at each grain, and what does the carrier\'s '
                    'port-in rate MEAN \u2014 which direction is good and what scale is it on?',
        "homes": ('app/modules/commcalc/kpi_failing.py',),
        "index": ('19.28', '62'),
        "locks": ('harness_kpi_registry_lock.py', 'harness_product_mix.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        #
        # The port-in half was added in §62 after the only reader of `raw_dlar_store.port_pct` on the
        # platform was found to have BOTH its direction and its scale wrong — it called the carrier's
        # port-INS a port-out rate and multiplied an already-percent value by 100, so the flag fired on
        # every store with a single port-in. `harness_product_mix.py` §J1 fails the build if any module
        # but this home reads that column again.
        "callers": {
            'app/modules/commcalc/calculator.py': ('_kpi_failing',),
            'app/modules/commcalc/dlar_vs_platform.py': ('_kf',),
            'app/modules/commcalc/flags.py': ('_kpi',),
            'app/modules/commcalc/payout_structure.py': ('_kpi',),
            'app/modules/commcalc/router.py': ('_kpi_failing', '_kpif'),
            # §CORE-17.3t. The Failing KPIs drill-down has to say WHICH GRAIN each panel is over —
            # a door's actual comes out of the carrier's own per-door report and an employee's out
            # of what the last calculation wrote down, and the two are never pooled. So it asks this
            # home for the two grain words rather than spelling them, bound by object identity and
            # locked by `harness_batch20_drilldown.py` §A7. It reads NO KPI column itself and holds
            # no port-in reading of its own: every per-metric verdict is the classifier's own
            # `failing` / `met` / `no_data` list, read off the row.
            'app/modules/commcalc/performance_coaching_drilldown.py': ('_kpif',),
        },
    },
    'spiff_store_impact': {
        "question": 'What is ONE carrier pay type worth to a store\'s commission payout revenue and '
                    'to its net profit, and which stores are not earning it on the sales they make?',
        "homes": ('app/modules/commcalc/spiff_impact.py',),
        "index": ('60',),
        "locks": ('harness_spiff_impact.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_spiffimp',),
            # §CORE-17.3u — the sales-outcome drill-down reads this home's own METRIC KEY so the
            # per-100-box rate it refuses a panel to is named in the producer's own word. THREE
            # provenances sit on one store row of this report, and the declaration says which of
            # them answered for each figure rather than pooling them.
            'app/modules/commcalc/sales_outcome_drilldown.py': ('_spiff',),
        },
    },
    'accessory_target_allocation': {
        "question": 'Given ONE company accessory sales goal, what target should each store carry, '
                    'derived from its own accessories-per-box history against the boxes it sells?',
        "homes": ('app/modules/commcalc/accessory_target_plan.py',),
        "index": ('61',),
        "locks": ('harness_accessory_target_plan.py', 'harness_batch15_drilldown.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_accplan',),
        },
    },
    'manager_report_card': {
        "question": 'What was assigned to this district manager for each of their stores, did the '
                    'system record it as met, and who is accountable one level above them?',
        "homes": ('app/modules/commcalc/manager_report_card.py',),
        "index": ('59.9',),
        "locks": ('harness_manager_report_card.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_mrcard',),
        },
    },
    'device_reimbursement_paid_vs_claimed': {
        "question": 'What did the carrier actually PAY for device financing, what does the '
                    'distributor CLAIM it reimbursed, and may the two be compared at all? At '
                    'STORE-MONTH grain from the statement, and at DEVICE grain from the carrier\'s '
                    'own per-line feed — including WHICH STORE was paid for a device, because the '
                    'distributor books the claim to the store it was stocked to and the carrier '
                    'pays the store it was activated at. The COST side dereferences that same '
                    'device verdict (fact `device_cost_store`, SS69) rather than deriving its own.',
        "homes": ('app/modules/commcalc/device_reimb_recon.py',),
        "index": ('19.53', '65'),
        "locks": ('harness_device_reimb_recon.py',
                  'harness_device_line_reimbursement.py',
                  'harness_batch16_drilldown.py'),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            'app/modules/commcalc/router.py': ('_drr',),
            # The COST side (§69) reads this fact's own verdict reader to place a handset's cost on
            # the activating store. One reconciliation, two readers, never two derivations.
            'app/modules/account/device_cogs.py': ('_drr',),
            # The DRILL-DOWN (§CORE-17.3p) reads this fact's own VERDICTS and REASON KEYS so its two
            # unpaid buckets count exactly the devices the recon reached those verdicts about, and a
            # device shown without a figure names the absence in the producer's own vocabulary. It
            # forms no opinion of its own about any device: it declares which column each figure was
            # read out of and which relation held it.
            'app/modules/commcalc/device_financing_drilldown.py': ('_drr',),
            # §COMMCALC-44 — the one home for the DOLLARS against a device binds this fact's pure
            # fold to the org's config and the paged feed read. Both reports that want the figure
            # go through it, so neither re-derives this fact's verdict vocabulary.
            'app/modules/commcalc/carrier_device_money.py': ('_drr',),
        },
    },
    # ══════════════════════════════════════════════════════════════════════════════════════════
    # Factored out of `router._device_line_reimb_run` on 2026-10-28 when §COMMCALC-43's chargeback
    # amount needed the same figure. It is NOT a sibling of the fact above: that one owns the
    # VERDICT (paid here, paid elsewhere, never paid, not measured) and this one owns only the
    # DOLLARS against one device and the I/O that binds the §58/§66/§27 questions to them. Two
    # reports read this; both read the verdict fact through it.
    'carrier_device_money': {
        "question": 'How much device-financing money has the carrier already paid against THIS '
                    'device? It binds the org\'s own declarations to §58\'s classification, asks '
                    '§66 whether a classification is evidence at all, asks §27 how long either '
                    'side of the event the money may arrive, reads the per-line feed paged, and '
                    'folds it per device through §65\'s pure `carrier_line_side`. It answers None '
                    'and never 0.00 for a device the feed carries no row for, because a rebate '
                    'lands months after the sale and a confident zero there is the fake-zero '
                    'class.',
        "homes": ('app/modules/commcalc/carrier_device_money.py',),
        "index": ('COMMCALC-44',),
        "locks": ('harness_carrier_device_money.py',
                  'harness_device_line_reimbursement.py'),
        # SNAPSHOT — written BY HAND, multi-line, not blessed (see the note on the fact above).
        "callers": {
            # The P&L's equipment-reimbursement side: one row per device, the figure the statement
            # is booked on.
            'app/modules/commcalc/router.py': ('_cdm',),
            # §COMMCALC-43's CHARGEBACK AMOUNT — the carrier money standing against the handset on
            # a flagged brought-device-then-upgrade pair. It reads the SAME verdict the P&L books
            # on, which is the point: a watchdog screen and the statement must not quote two
            # different rebates for one phone.
            'app/modules/commcalc/watchdog_router.py': ('_cdm',),
            # §COMMCALC-43's drill-down catalogue names the relation this home reads the chargeback
            # amount out of, by dereferencing `FEED_TABLE` rather than spelling it — so a panel
            # cannot attribute the figure to the feed the ROW was found in (§CORE-17.3j).
            'app/modules/commcalc/byod_upgrade_drilldown.py': ('_cdm',),
        },
    },
    # ── §COMMCALC-49 (owner ask 2026-10-29) — HOW MANY OF THIS WILL SELL NEXT ───────────────────
    'demand_projection': {
        "question": 'Given the units of something sold inside a window, how many will sell over the '
                    'next horizon — and which sale line sold a DEVICE at all? Both facts were '
                    'written TWICE before this home existed, and the second copy of each shipped '
                    'with a docstring saying it was a copy (the import-cycle excuse). The home '
                    'imports NOTHING, so that excuse cannot be revived. Velocity is per window-UNIT '
                    'and the horizon is in the same unit, so a caller counting days and a caller '
                    'counting months share one arithmetic without a hidden second divisor; the '
                    'banker\'s rounding both live callers were already ordering phones on is '
                    'preserved to the digit. A window of no days is reported as unmeasurable, never '
                    'as a projection of zero. SECOND HOME, added 2026-10-30 after the owner '
                    'found all three callers blind to a whole tenant: which DEPARTMENTS an org '
                    'calls a device is the org\'s own declaration '
                    '(`accessory_config.box_departments` — the list the box count, the '
                    'product mix and the GP report already read), asked through '
                    '`device_vocabulary.declared_device_departments`. It had been an OPTIONAL '
                    'default holding one tenant\'s POS labels, so a month of another tenant\'s '
                    'handsets read as zero. The vocabulary is now a REQUIRED argument: a caller '
                    'that has not asked the org cannot answer at all.',
        "homes": ('app/modules/commcalc/demand_projection.py',
                  'app/modules/commcalc/device_vocabulary.py'),
        "index": ('COMMCALC-49',),
        "locks": ('harness_demand_projection.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04, and it strips exactly these reasons).
        "callers": {
            # The device forecast (§15, module 095). It kept its own four lines of velocity and its
            # own device-line test; both are gone, and the device DEPARTMENTS it used to spell as a
            # literal are now the ORG'S declaration, asked through `device_vocabulary`.
            'app/modules/payables/router.py': ('_devvocab', 'demand_projection'),
            # The proposed-PO recommendations (§36). The other copy, excused in its docstring by the
            # module-load cycle — answered rather than argued with, since the home imports nothing.
            'app/modules/asset/purchase_orders.py': ('_devvocab', 'demand_projection'),
            # The Accessory Order Plan (§COMMCALC-48) — the THIRD caller, and the one whose arrival
            # is why the fact was factored instead of copied a third time. It counts MONTHS where
            # the other two count days, which is exactly what the window-unit rule allows.
            'app/modules/commcalc/accessory_order_plan.py': ('_dp',),

            # The report's endpoint reads the device-line test directly, so the look-back counts the
            # same lines the two ordering screens do.
            'app/modules/commcalc/router.py': ('_devvocab', '_dp'),
        },
    },
    'device_model_family': {
        "question": 'WHICH SOLD LINES ARE THE SAME PHONE, for the purpose of ordering accessories? A '
                    'case fits a phone, not a colour of it, so five colours of one handset are one '
                    'order row — while a Pro is a different phone and must stay one. The capacity is '
                    'dropped by PATTERN (a number followed by a storage unit is a format marker, like '
                    'the CellPhone category); the COLOUR is dropped only if the ORG HAS DECLARED THAT '
                    'WORD, because a colour word is a tenant\'s own spelling and CLAUDE.md bans a '
                    'house default that is really one tenant\'s vocabulary. So `colour_tokens` is a '
                    'REQUIRED keyword argument with nothing behind it, an org that has declared none '
                    'gets no grouping and the surface SAYS so, and a curated '
                    '`device_model_alias.canonical_model` outranks the derivation. The folded row '
                    'carries the spellings that went into it, so the drill-down costs no second '
                    'query. A DEVICE CONDITION is read the same way and applied AFTER the alias '
                    '(owner 2026-10-31: a pre-owned handset is a different device), which is the '
                    'only order that cannot lose the marker — the router used to canonicalise '
                    'first and silently merged the two.',
        "homes": ('app/modules/commcalc/device_model_family.py',),
        "index": ('COMMCALC-49.11', 'POS-18'),
        "locks": ('harness_accessory_order_grid.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50).
        "callers": {
            # The Accessory Order Plan's endpoint: it asks the org for its colour words and folds the
            # raw model counts into phones before the plan is built.
            'app/modules/commcalc/router.py': ('_dmf',),
        },
    },
    'device_condition_vocabulary': {
        "question": 'WHICH DEVICE CONDITIONS EXIST, and what does THIS feed call one? Two different '
                    'questions, split on CLAUDE.md\'s line: the four conditions are the '
                    'platform\'s POLICY (the same for every tenant, and the same four the schema '
                    'has always allowed on `pos.inventory_serial`, pinned key by key), while '
                    'the WORDS a product description marks one with are the TENANT\'s vocabulary '
                    '(`CPO` in one feed, `CERTIFIED_PREOWNED` in another) and may only be declared '
                    '— a REQUIRED argument with no house fallback, so an org that has declared none '
                    'identifies no condition and the surface says so rather than reading a '
                    'refurbished handset as new. An UNMARKED line is the house default with '
                    '`basis=unmarked`, which keeps an inference apart from a reading. Both lists '
                    'were written twice before this (a schema constraint and a literal array on the '
                    'inventory screen) and the accessory grid kept a pre-owned handset off the new '
                    'one\'s row only because the word happened to survive a parse.',
        "homes": ('app/modules/pos/device_condition.py',),
        "index": ('POS-18',),
        "locks": ('harness_accessory_order_grid.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50).
        "callers": {
            # The Accessory Order Plan's endpoint: it asks the org for its condition words so a
            # pre-owned handset is ordered as its own device.
            'app/modules/commcalc/router.py': ('_cond',),
            # The one home for the family grain reads the condition off the raw spelling.
            'app/modules/commcalc/device_model_family.py': ('_cond',),
            # The POS inventory screen's two dropdowns are served from here.
            'app/modules/pos/router.py': ('_devcond',),
        },
    },
    'accessory_order_catalog': {
        "question": 'WHAT KINDS OF ACCESSORY CAN BE BOUGHT, and how is each one chosen? The '
                    'load-bearing distinction is SPLIT against ASSIGNED: a case and a protector are '
                    'bought FOR a phone and divide across styles, so they are columns per phone; a '
                    'cable, a charging cube and earphones are bought BY CONNECTOR and consolidate '
                    'across every phone that takes the same one, because the cables are common to a '
                    'lot of phones. The option lists are extensible per org, with a picture per '
                    'option; a house default is correct here because these are the generic names of '
                    'a kind of product, read against no tenant\'s feed. NOT `accessory_catalog`, '
                    'which classifies what was SOLD from an uploaded product catalog '
                    '— one word apart in the filename, two different questions, and writing this '
                    'feature overwrote that module once before the name was fixed. Also the ONE home '
                    'for the share-reading rule (percentages or fractions, decided by the total), '
                    'which the colour mix now dereferences instead of keeping its own copy.',
        "homes": ('app/modules/commcalc/accessory_order_catalog.py',),
        "index": ('COMMCALC-49.12',),
        "locks": ('harness_accessory_order_grid.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50).
        "callers": {
            # The plan builds the grid and the consolidated sheet off the resolved catalog, and reads
            # the share rule for the colour mix through it.
            'app/modules/commcalc/accessory_order_plan.py': ('_cat',),
            # The endpoint resolves the catalog to validate a per-phone assignment before storing it,
            # and reads which product classes take an accessory at all.
            'app/modules/commcalc/router.py': ('_aocat',),
        },
    },
    'device_cost_store': {
        "question": 'WHICH STORE carries a handset\'s cost? The distributor ledger books it to the '
                    'store the unit was STOCKED to; the carrier pays the store it was ACTIVATED at. '
                    'Declared per org, and when the activating basis is declared the cost follows '
                    'the per-device verdict that already reconciles those two authorities — never a '
                    'second opinion formed on the cost side.',
        "homes": ('app/modules/account/device_cogs.py',),
        "index": ('70', '42.5'),
        "locks": ('harness_device_inventory_cogs.py',
                  'harness_device_line_reimbursement.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04).
        "callers": {
            # The P&L input builder: it hands the PERIOD down so the activation index is reachable
            # at all. Without that argument the switch could be declared and still never fire —
            # the §19.18 un-wiring failure mode, pinned by §H5b.
            'app/modules/account/coa.py': ('_device_cogs',),
            # The finance-config screen: it READS the resolved basis and writes the declaration,
            # and has no opinion of its own.
            'app/modules/account/router.py': ('_dc', '_dccfg', '_dcscfg'),
            # §COMMCALC-03 — the takeover evaluation prices a PROSPECT's handsets through this home's
            # factored-out pure cores (`ma_unit_price_from_rows` / `ma_sold_cost_from_rows`), so a
            # prospect's device COGS is the same arithmetic as the company's.
            'app/modules/commcalc/router.py': ('_dcg',),
            # The BALANCE SHEET and the statement engine import this module for the mig-1041 basis
            # (§42.5), not for the placement — but the import graph connects them, so they are named
            # here rather than left to look like an unexplained edge.
            'app/modules/account/balance_sheet.py': ('FEE_CATEGORIES',),
            'app/modules/account/statement_engine.py': ('device_cogs',),
            # COMMCALC-27 (2026-10-22) — THE PAYABLE LEDGER prices a handset the carrier's own row
            # carries no amount for (`owed_mode='unit_price_map'`, mig 1091) through this home's pure
            # core, so a Total device's cost on the payables screen is the SAME arithmetic the P&L
            # costs it with. Measured that day: the two easier joins both fail — the activation order
            # is absent from the fulfillment sheet (0 of 4,322) and the processor feed's price is an
            # activation charge, not the device ($529.00 vs $195.00 on one iPhone 13).
            'app/modules/payables/engine.py': ('_cogs',),
        },
    },
    'entitlement_month_coverage': {
        "question": 'An entitlement is a COUNT OF MONTHS per store — which store-month inside a '
                    'store\'s own entitlement window never arrived? And which of those absences is '
                    'a fact about the CARRIER (the month\'s payment run arrived for other stores) '
                    'rather than about OUR INGEST (no payment of this kind reached us at all that '
                    'month) or simply undecidable (the store\'s first entitled month is not '
                    'declared, so a gap cannot be told from the entitlement running out)?',
        "homes": ('app/modules/commcalc/entitlement_coverage.py',),
        "index": ('68',),
        "locks": ('harness_mdf_entitlement_coverage.py',),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            'app/modules/commcalc/router.py': ('_ec',),
            # §CORE-17.3v. The coverage drill-down reads the producer's OWN verdict words, config
            # column and migration name rather than restating any of them, so a verdict renamed
            # here reaches the panel with no second edit.
            'app/modules/commcalc/entitlement_coverage_drilldown.py': ('_ec',),
        },
    },
    'declared_metric_catalog': {
        "question": 'What does this business DECLARE at the end of a day, what is a unit worth, and '
                    'who declared it? Asked by the closing form, the Count Fields editor and the '
                    'daily-incentive rules. ONE registry; the two physical stores '
                    '(daily_closing.counts and closing_metric_line) are folded by one reader, and a '
                    'store-level figure is never attributed to a person.',
        "homes": ('app/modules/closing/metric_catalog.py',),
        "index": ('76',),
        "locks": ('harness_metric_catalog_lock.py', 'harness_daily_incentive.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04, and on 2026-10-10 it stripped the hand-written caller comments
        # off five facts; that run was reverted).
        "callers": {
            # The ONE construction site (`_metric_catalog`), the store-day fold and the metric-line
            # writer. Every closing endpoint that offers a category list routes through it.
            'app/modules/closing/router.py': ('_metrics',),
            # The incentive engine measures THROUGH the catalog — `gross_revenue`, `units_of`,
            # `amount_of` — so a payout rule and a target cannot disagree about what a day's sales
            # were. It holds no category list and no second definition of revenue.
            'app/modules/commcalc/daily_incentive.py': ('mc',),
            # The rule editor and the day calculation reach the catalog through the closing home's
            # `_metric_catalog`, never by building one.
            'app/modules/commcalc/router.py': ('_mc',),
            # §COMMCALC-02 — the targets engine dereferences `figure_unit` for a declared category's
            # UNIT, while closing/router dereferences `figure_of` for its ACHIEVED figure. Two copies
            # of that one rule would let a target be SET in units and MEASURED in dollars, so it
            # belongs to this question rather than being a fact of its own.
            'app/modules/commcalc/targets_engine.py': ('_figure_unit',),
            # §CLOSING-02 (mig 1102) — the declared-vs-captured reconciler asks the catalog WHO
            # declares a category (`is_employee_declared`), because the grain of an accusation is
            # the category's and not the feed's: folding a per-rep capture against a store
            # declaration accused a store of not declaring a sale it had declared.
            'app/modules/closing/declared_feed_recon.py': ('_metrics',),
        },
    },
    'daily_incentive_shapes': {
        "question": 'What does a day\'s declared categories PAY each employee? Three SHAPES only; '
                    'every rule, category, rate, threshold and split is a config row. A gate that '
                    'cannot be verified fails closed, and a pool whose split cannot be measured is '
                    'returned unallocated rather than split evenly.',
        "homes": ('app/modules/commcalc/daily_incentive.py',),
        "index": ('76',),
        "locks": ('harness_daily_incentive.py', 'harness_metric_catalog_lock.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (see the note above).
        "callers": {
            # The rule editor and the day calculation. Scope precedence is NOT here — it is
            # `management_incentive.resolve_plan`, so there is no second ladder.
            'app/modules/commcalc/router.py': ('_daily_incentive',),
        },
    },
    # ── §CORE-08 (owner report 2026-10-14) — which PERSON is this name ──────────────────────────
    'person_identity': {
        "question": 'Is this the same human? Asked wherever two systems\' people are joined: a '
                    'punch against a sale, the schedule against the POS, a closing against the '
                    'roster, a commission plan against a rep. The owner reported a store-day '
                    'listing "Simarjyot Singh, Singh, Simarjyot" — ONE man, because the who-worked '
                    'signal unioned NAME STRINGS and a union of strings has no notion of a person. '
                    'FOUR questions, named apart so they cannot be mistaken for one another: '
                    '`person_key` (comma-order-insensitive, what ~30 commcalc money call sites '
                    'already key on, deliberately unwidened), `token_key` (word order thrown away '
                    'too, the 2026-09-02 schedule-vs-POS join), `folded_token_key` (THE STRONGEST '
                    'and the one for new code: order-free AND accent-folded, so Martínez matches '
                    'Martinez) and `loosely_same_person` (a generous SCREENING rule, proven '
                    'non-transitive, so never a grouping key). Two modules each declared '
                    'themselves the home — storeops/payroll_identity for the clock feed and '
                    'commcalc/commission_engine for pay — and `closing` could reach neither '
                    'without a cycle, which is exactly how it grew a seventh rule of its own.',
        "homes": ('app/modules/core/person_identity.py',),
        "index": ('CORE-08',),
        "locks": ('harness_person_identity.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04).
        "callers": {
            # The commission PLAN engine's `_canon_person`, read ~30 times inside commcalc and the
            # key a payout is matched on. Byte-identical to what it ran before, on purpose.
            'app/modules/commcalc/commission_engine.py': ('_person_identity',),
            # A NEAR-copy that said it mirrored the engine and did not: it reordered a name with ANY
            # number of commas, so 'Khan, Ariful, Jr' keyed one way for a commission and another for
            # a management plan.
            'app/modules/commcalc/management_incentive.py': ('_person_identity',),
            # `name_key` — the word-order-free key the per-employee daily target joins on (a plain
            # compare matched ZERO of LuxeLink's reps, 2026-09-02).
            'app/modules/commcalc/targets_engine.py': ('_person_identity',),
            # The drill-down's word-set, for its "did you mean" ranking.
            'app/modules/commcalc/commission_drilldown.py': ('_person_identity',),
            # The closing module: `_name_match` (the screening rule) AND the who-worked signal,
            # which is the surface the owner reported.
            'app/modules/closing/router.py': ('_person_identity',),
            # §CLOSING-02 — the declared-vs-captured reconciler joins the name a rep typed at
            # closing to the salesperson on the sales feed, which is exactly the punch-against-sale
            # join this key exists for. It dereferences `folded_token_key` and folds nothing itself.
            'app/modules/closing/declared_feed_recon.py': ('_person',),
            # A byte copy of the router's screening rule, kept local to dodge an import cycle the
            # pure home does not have.
            'app/modules/closing/ops_chargebacks.py': ('_person_identity',),
            # The clock feed's employee matcher. THIS MODULE USED TO BE THE HOME (clock_feed.py said
            # so), and the accent fold that makes its key the strongest was found here.
            'app/modules/storeops/payroll_identity.py': ('_person_identity',),
            # THE HOURS APPROVAL BOARD'S PERSON FILTER. `GET /core/filter-options` keys its `reps`
            # options by NAME, and this board alone sent that value as `employee_id` — compared
            # against 'E043', so the filter matched NOBODY, ever (owner report 2026-10-26: *"i
            # selected mohammed sami but nothing showed up"*). The token is now resolved here
            # instead of by a seventh local name rule.
            'app/modules/storeops/payroll_approval.py': ('_pkey',),
        },
    },
    'store_identity': {
        "question": 'Which STORE is this string? Asked by the GP report, the commission-leg '
                    'trend/breakout, the residual report, flags 7/8, the closing store-day match, '
                    'the P&L store grain and the MA account index. The answer is the org\'s own '
                    'declared spellings; AMBIGUITY RESOLVES TO NOTHING, and money nothing can '
                    'place is reported on its own row rather than dropped.',
        "homes": ('app/modules/account/store_identity.py',),
        "index": ('64',),
        "locks": ('harness_store_identity_lock.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04).
        "callers": {
            # The I/O wrapper: reads store_mapping + store_aliases and hands them to the chain.
            'app/modules/account/coa.py': ('_sid',),
            # The GROSS PROFIT engine. It carried a private `street_num()` leading-token join for
            # every money source, which DROPPED a store's whole carrier income whenever the carrier
            # and the roster spelled the street number differently (measured $106,400.37 of house
            # revenue over Jul-Oct 2026) and was last-wins where two codes share one address.
            'app/modules/commcalc/gp_report.py': ('_sid',),
            # Flags 7/8 compared the TOKEN sets of the sales side and the payment side, so a store
            # the two feeds spell differently raised both "no payment" and "no sales" every month.
            'app/modules/commcalc/flags.py': ('_sid',),
            # The residual report's rep-pay join key.
            'app/modules/account/residual_subs.py': ('_sid',),
            # A closing store-day's match key (§29.12) — a named alias, no second copy.
            'app/modules/closing/unfinished_day.py': ('_sid',),
            # `_leg_store_index` + the two commission-leg endpoints' row keys, and (mig 1073) the
            # B2B clock report's `raw_store_string -> store CODE` resolver, which places a punch's
            # store through THIS chain rather than matching an address of its own.
            # `_sid_pairs` since §68: the entitlement-month report's router glue reads this home's
            # own PAIRWISE predicate (§70 `may_be_one_store`) for "may these two keys be one store".
            'app/modules/commcalc/router.py': ('_sid_pairs', '_store_identity'),
            # ── THE ONE ACCENT FOLD (`fold_accents`, 2026-10-10) ────────────────────────────────
            # `str.isalnum()` is TRUE for 'i-acute', so every alnum-only identity fold in this
            # codebase kept the accent and compared an accented spelling unequal to its own plain
            # twin (measured: the clock report's `Martinez, LESLIEE` matched no roster row). The
            # fold now lives here once and these four siblings DEREFERENCE it — they are named so
            # the next change cannot quietly re-spell one of them back into a private fold.
            'app/core/scope.py': ('_fold_accents',),
            'app/modules/account/balance_sheet.py': ('_sid_fold',),
            'app/modules/account/device_payable.py': ('_sid_fold',),
            'app/modules/account/store_identity_audit.py': ('_sid',),
            # The B2B clock feed's own folds (`_fold`, login + punch-type keys) and the employee
            # matcher's person key ride the same accent fold, so a person and a store are folded by
            # one rule (mig 1073, §14z).
            'app/modules/storeops/clock_feed.py': ('_sid',),
            # THE ONE PERSON KEY (§CORE-08) folds a person's spelling through this fold, for the
            # clock feed's matcher and the closing module's who-worked signal alike —
            # `payroll_identity` dereferences it rather than importing the fold itself.
            'app/modules/core/person_identity.py': ('fold_accents',),
            # The remaining five identity folds, found by the lock's second axis and wired in the
            # same PR rather than left as "the ones we did not get to". Each folds a KEY nothing
            # ever shows a human — a store code, a carrier label, a clock-in evidence key, a role
            # name — so routing it through the one fold gains matches and moves none.
            # `_store_identity` since §CORE-08: the who-worked signal buckets a PUNCH's store_code
            # through this chain, so a punch stamped 'b-418' cannot split from the same store's
            # 'B-418' sales row.
            'app/modules/closing/router.py': ('_sid_fold', '_store_identity'),
            'app/modules/commcalc/b2b_sweep.py': ('_sid_fold',),
            'app/modules/commcalc/vidapay_sweep.py': ('_sid_fold',),
            'app/modules/storeops/clockin_evidence.py': ('_sid_fold',),
            'app/modules/storeops/clock_variance.py': ('_sid_fold',),
            # §15 (2026-10-22) — THE STORE FILTER on Device Forecasting & Vendor Payables. Its store
            # picker compared the roster's spelling against each row's own, so every store the feeds
            # spell differently returned an empty report while the MARKET filter (which was already
            # resolved) worked — measured live: 13 of 33 asset_ledger spellings and 15 of 37 ledger
            # spellings matched no option at all. Rows and options are now both STAMPED with this
            # home's key, and the comparison is made on that.
            'app/modules/payables/router.py': ('_sid',),
        },
    },
    'carrier_dollar_component': {
        "question": 'Is this carrier dollar a commission, a spiff, a residual or a reimbursement — '
                    'and is that the ORG\'S OWN declaration or a guess the platform made? Which '
                    'P&L LINE and which GROSS-PROFIT COLUMN that component lands on is the same '
                    'fact, read twice, and lives here too (`component_line` / `gp_column`). '
                    'WHETHER THE COMPONENT IS EVIDENCE AT ALL is part of the same fact and is '
                    'answered from `basis` alone (`component_is_evidence`): a component reached on '
                    'a fallback basis is a placement of last resort, so a keyword guess never '
                    'overrides the org\'s own declaration, and what a caller DOES about an '
                    'unreliable component is the caller\'s policy, never this home\'s. AND A '
                    'DECLARATION MUST BE HONOURABLE: the component vocabulary '
                    '(`carrier_map.COMPONENTS`, dereferenced) covers every category the platform '
                    'can place, because a category it cannot express leaves a keyword guess '
                    'standing where the org\'s own word already exists — which booked $260,500.00 '
                    'of declared market-development money on the device-financing reimbursement '
                    'line. Which components ARE the device-financing side is the same fact too '
                    '(`device_reimb_line` / `device_reimb_components`), and so is what a component '
                    'is CALLED for a human (`component_label`).',
        "homes": ('app/modules/commcalc/carrier_dollar_class.py',),
        "index": ('58', '66', '69'),
        "locks": ('harness_carrier_dollar_class.py',
                  'harness_gp_carrier_class_dereference.py',
                  'harness_component_basis_evidence.py',
                  'harness_declared_category_honourable.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            'app/modules/account/coa.py': ('_cdc',),
            # The GROSS PROFIT engine (owner report 2026-10-08: *"gross profit is still showing the
            # old data m teh source of information should be the same"*). It carried TWO private
            # classifications — a keyword guess on the compensation type and four exact compares on
            # the pay category — which disagreed with the P&L by $418,922.21 on the same 11,114
            # August 2026 rows. It now dereferences this home and states no ruling of its own.
            'app/modules/commcalc/gp_report.py': ('_cdc',),
            # The SALES-AND-MARGIN drill-down (§CORE-17.3r). A panel over the GP report's two
            # commission legs has to say whether this period's carrier dollars were PLACED by the
            # company's own declaration or by a last-resort keyword rule, because a figure resting
            # on a guess is not the same claim as one resting on a fact. It asks
            # `component_is_evidence(basis)` — the ALLOWLIST that is the one home for that question
            # (§66) — and states no ruling of its own: a basis nobody has ruled on reads as NOT
            # evidence, so a last-resort basis added later cannot start presenting itself as a fact.
            'app/modules/commcalc/sales_margin_drilldown.py': ('_cdc',),
            'app/modules/commcalc/router.py': ('_cdc', '_cdc_gp', '_cdc_leg'),
            # §COMMCALC-44 — the device-money home asks this fact THREE of its questions at once:
            # which components are the device-financing side (`device_reimb_components`, §69),
            # whether a classification is evidence at all (`component_is_evidence`, §66) and the
            # classification itself (`classify`). It carries no payment-type vocabulary and no basis
            # allowlist of its own; the POLICY of declining a non-evidence component belongs to its
            # per-device grain and lives there.
            'app/modules/commcalc/carrier_device_money.py': ('_cdc',),
        },
    },
    'installment_month_of_life': {
        "question": 'Which instalment month is this subscriber or sale in, and is that month PROVEN '
                    'by the row or assumed from the window the reader pulled?',
        "homes": ('app/modules/commcalc/installment_month.py',),
        "index": ('19.51',),
        "locks": ('harness_installment_month_anchor.py',),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` has silently deleted facts
        # before, so this entry is maintained here and the lock verifies it against the real import
        # graph rather than regenerating it.
        "callers": {
            'app/modules/commcalc/installment_engine.py': ('_im',),
            'app/modules/commcalc/sale_installment_engine.py': ('_im',),
        },
    },
    'finding_kind_and_severity': {
        "question": 'What kind of finding is this, how bad is it, and who reviews it?',
        "homes": ('app/modules/commcalc/flag_registry.py',),
        "index": ('52.1', '52.2'),
        "locks": ('harness_flag_registry_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/recon.py': ('_reg',),
            'app/modules/asset/invoice_due.py': ('_reg',),
            'app/modules/asset/router.py': ('_reg',),
            'app/modules/closing/cash_watchdog.py': ('_reg',),
            'app/modules/closing/ops_chargebacks.py': ('_reg',),
            # §COMMCALC-43. The byod-and-upgrade detector spells no severity, area or label of its
            # own — `harness_byod_upgrade_day` §G1 fails the build if it declares one — so the
            # Management Watchdog board gains a page for it by this registration alone.
            'app/modules/commcalc/byod_upgrade_day.py': ('_reg',),
            'app/modules/commcalc/flags.py': ('_reg',),
            'app/modules/commcalc/multi_handset_day.py': ('_reg',),
            'app/modules/commcalc/portout_flags.py': ('_reg',),
            'app/modules/commcalc/router.py': ('flag_registry',),
            'app/modules/commcalc/sale_installment_engine.py': ('_reg',),
            'app/modules/commcalc/sales_recon.py': ('_reg',),
            'app/modules/commcalc/void_watchdog.py': ('_reg',),
            'app/modules/commcalc/watchdog_router.py': ('_reg',),
            'app/modules/payables/engine.py': ('_reg',),
        },
    },
    'cash_count_comparability': {
        "question": 'May a count of the whole envelope be scored against this expected figure?',
        "homes": ('app/modules/closing/billpay_netting.py',),
        "index": ('74.2', '74.3'),
        "locks": ('harness_envelope_count_basis.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/pickup_actual.py': ('_bn',),
            'app/modules/closing/router.py': ('billpay_netting',),
        },
    },
    'cash_count_day_verdict': {
        "question": 'What did this store-day\'s counted envelopes come to, short or over?',
        "homes": ('app/modules/closing/pickup_actual.py',),
        "index": ('CLOSING-01.1', 'CLOSING-01.4'),
        "locks": ('harness_management_recon_expected.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`, but CHECK THE
        # DIFF: the rewriter strips hand-written comments out of these `callers` dicts (it dropped
        # 119 lines of them on 2026-10-12), so this entry was written by hand.
        "callers": {
            # The accountability board shows a day's row counts BESIDE its comparable subtotal, and
            # takes the day's VERDICT from this home rather than re-deriving one — so the board and
            # the management recon screen cannot disagree about a store-day. §CLOSING-01.4.
            'app/modules/closing/deposit_accountability.py': ('_day_variance', '_envelope_opened',
                                                              '_row_variance'),
            # `GET /cash-recon-management`. It used to subtract the whole declared drawer here; it
            # now computes no variance at all. §CLOSING-01.1.
            'app/modules/closing/router.py': ('_pickup_actual',),
            # NOT a sibling derivation, named so the edge is not an unexplained one: the import graph
            # connects this through `pickup_actual`, but `pickup_totals_by_store_day` answers HOW
            # MUCH MONEY MOVED on a store-day, not whether its envelopes were short — a different
            # question with a different home.
            'app/modules/closing/billpay_pickup.py': ('_outflow',),
        },
    },
    'cash_count_correction_trail': {
        "question": 'Was this management cash figure changed, from what, and by whom?',
        "homes": ('app/modules/closing/count_revision.py',),
        "index": ('73.2', '73.3'),
        "locks": ('harness_cash_count_revision.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/router.py': ('_count_revision',),
        },
    },
    'cash_figure_basis': {
        "question": 'Which cash does this figure count, and may it be compared with that one?',
        "homes": ('app/modules/closing/cash_variance.py',),
        "index": ('72.2', '72.3'),
        "locks": ('harness_cash_variance.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/router.py': ('_cash_variance',),
        },
    },
    'cash_drawer_variance_owner': {
        "question": 'Which store-day carries a drawer variance nobody has ruled on?',
        "homes": ('app/modules/closing/cash_watchdog.py',),
        "index": ('52.3',),
        "locks": ('harness_cash_watchdog.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/watchdog_router.py': ('_cash',),
        },
    },
    'void_and_return_visibility': {
        "question": 'What did we void or return, and who rang it?',
        "homes": ('app/modules/commcalc/void_watchdog.py',),
        "index": ('52.4',),
        "locks": ('harness_void_watchdog.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/watchdog_router.py': ('_void',),
        },
    },
    'vendor_order_route': {
        "question": 'How does an order actually reach this vendor, and may a sweep send it?',
        "homes": ('app/modules/supply/order_transport.py',),
        "index": ('51.1',),
        "locks": ('harness_order_transport.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/storevisit/router.py': ('_ot',),
            'app/modules/supply/router.py': ('_tr',),
        },
    },
    'vendor_order_dialect': {
        "question": 'What do I say to this vendor API, and what comes back?',
        "homes": ('app/modules/supply/shopify_draft_order.py',),
        "index": ('51.2', '51.7'),
        "locks": ('harness_order_transport.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/storevisit/router.py': ('_shopify',),
            'app/modules/supply/router.py': ('_shopify',),
        },
    },
    'vendor_api_credential': {
        "question": 'What bearer token do I use for this vendor right now?',
        "homes": ('app/modules/supply/api_credential.py',),
        "index": ('51.6',),
        "locks": ('harness_order_transport.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/storevisit/router.py': ('_cred',),
            'app/modules/supply/router.py': ('_cred',),
        },
    },
    'vendor_customer_identity': {
        "question": "Who is this store on the vendor's side?",
        "homes": ('app/modules/supply/vendor_customer.py',),
        "index": ('51.8',),
        "locks": ('harness_order_transport.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/storevisit/router.py': ('_vc',),
            'app/modules/supply/order_transport.py': ('_customer',),
            'app/modules/supply/router.py': ('_vc',),
        },
    },
    'alert_send_record': {
        "question": 'Has this alert finding been carried to this address, over this channel?',
        "homes": ('app/modules/storeops/alert_log.py',),
        "index": ('15.1',),
        "locks": ('harness_alert_channel_record.py', 'harness_alert_autofix.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/router.py': ('_alert_log',),
            'app/modules/notify/digest_delivery.py': ('_log',),
            'app/modules/storeops/router.py': ('_alert_log',),
        },
    },
    'send_report_identity': {
        "question": 'Which report does this send record name?',
        "homes": ('app/modules/notify/send_identity.py',),
        "index": ('15.3',),
        "locks": ('harness_notify_send_identity.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/notify/router.py': ('send_identity',),
        },
    },
    'send_failure_reason': {
        "question": 'Why did this send fail for this recipient, and what does a human do about it?',
        "homes": ('app/modules/notify/send_outcome.py',),
        "index": ('NOTIFY-01',),
        "locks": ('harness_notify_send_outcome.py',),
        # HAND-WRITTEN, per the precedent above: `--bless` strips the per-caller notes. The digest leg
        # imports it INSIDE the except branch, so the alias it binds is `_why`, not the module name.
        "callers": {
            'app/modules/notify/router.py': ('send_outcome',),
            'app/modules/notify/digest_delivery.py': ('_why',),
        },
    },
    'alert_channel_ladder': {
        "question": 'Which channels does this recipient still owe, and what did each one do?',
        "homes": ('app/modules/notify/digest_delivery.py',),
        "index": ('15.2',),
        "locks": ('harness_alert_channel_record.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/router.py': ('_delivery',),
            'app/modules/commcalc/router.py': ('_delivery',),
            # §14z — the weekly clock-variance ask carries itself on this ladder and holds no mail
            # call, no channel list and no send record of its own. That is the point of routing it
            # here: the ask was the sixth place that would have grown its own "try email, try
            # WhatsApp, write one dedup row", which is the drift this fact exists to stop.
            'app/modules/storeops/clock_variance_api.py': ('_delivery',),
            'app/modules/storeops/router.py': ('_delivery',),
            'app/modules/storevisit/router.py': ('_delivery',),
        },
    },
    'month_archive_due': {
        # §19.54 widened this ONE question rather than standing up a second home for the same
        # subject: "which days and months is this feed's window actually about" covers both when a
        # month has CLOSED (so its archive is due and may be compared) and which days that closed
        # month is OWED — plus the window a pull must ASK for so its last day arrives at all
        # (`request_window`, `month_days`). A second module for the boundary would have been a
        # second place to answer the same thing, which is the defect the graph exists to stop.
        "question": "Which days and months is this feed's window about — has the month closed so its "
                    "month-end archive is due and comparable, which days is a closed month owed, and "
                    "what window must a pull ASK for so its last day actually arrives?",
        "homes": ('app/modules/commcalc/feed_period.py', 'app/modules/commcalc/sales_recon.py'),
        "index": ('19.52', '19.54'),
        "locks": ('harness_sales_recon_basis.py', 'harness_feed_day_grain.py',
                  'harness_statement_month_coverage.py'),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            # §68 — the entitlement-month report asks the calendar whether a window month has
            # CLOSED (so an absence in it is a fact) and parses every month spelling through the same
            # home; it re-implements neither, and holds no clock of its own.
            'app/modules/commcalc/entitlement_coverage.py': ('_fp',),
            'app/modules/commcalc/epay_sweep.py': ('_feed_period',),
            'app/modules/commcalc/import_audit.py': ('_recon',),
            # §19.54 — the statement-coverage verdict dereferences `month_state` for "is this month
            # closed" and `month_days` for the calendar it is judged against; it owns neither.
            'app/modules/commcalc/pay_data_quality.py': ('_fp',),
            'app/modules/commcalc/router.py': ('_feed_period', 'sales_recon'),
            'app/modules/notify/report_registry.py': ('SR',),
        },
    },
    'sweep_due': {
        "question": ("Should this sweep run NOW, and why — or is the only answer anybody has a stored "
                     "clock time?"),
        "homes": ('app/modules/commcalc/sweep_due.py',),
        "index": ('COMMCALC-45',),
        "locks": ('harness_sweep_due.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            # The defect was HERE and nowhere else: eight `run-due` endpoints each comparing a stored
            # clock, four of them in SQL where no other reason can be expressed. §F of the lock reads
            # this file as source and pins the one excused filter that remains.
            'app/modules/commcalc/router.py': ('_sweep_due',),
        },
    },
    'sweep_verdict': {
        "question": ("Did this sweep run leave the feed whole — and does the stored status WORD say "
                     "the same thing as the status sentence beside it?"),
        "homes": ('app/modules/commcalc/dlar_sweep.py',),
        "index": ('COMMCALC-40',),
        "locks": ('harness_portal_cell.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            # The defect was HERE, not in the home: the router passed the literal 'ok' beside a
            # detail line that said "⚠ PARTIAL". §H of the lock reads this file as source and keeps
            # the literal as an armed negative control.
            'app/modules/commcalc/router.py': ('dlar_sweep',),
        },
    },
    'portal_cell': {
        "question": ("Did the source actually SEND this figure, or is this a zero we wrote down? And "
                     "which source field feeds which column?"),
        "homes": ('app/modules/commcalc/portal_cell.py',),
        "index": ('COMMCALC-16',),
        "locks": ('harness_portal_cell.py', 'harness_kpi_registry_lock.py', 'harness_kpi_vintage.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/dlar_sweep.py': ('_cell',),
        },
    },
    'payroll_hours_basis': {
        "question": 'Which hours is this person paid for on this day, and on what BASIS — a closed '
                    'punch, a human correction, a measured zero, the schedule they are ticked to be '
                    'paid from, or nothing at all? It was `actual_hours or scheduled_hours` in six '
                    'places over a column effectively nothing writes (7 of 7,740 shift rows), so the '
                    '`or` fell through to the schedule on effectively every row and no reader was '
                    'ever told. The hours and the REASON they are those hours are ONE fact: a caller '
                    'that wants the number takes the basis with it (§14y). §CORE-17.3x made that '
                    'basis a drill-down\'s provenance — which relation answered for a figure — so a '
                    'second copy of the vocabulary would be a panel naming the wrong table.',
        "homes": ('app/modules/storeops/payroll_hours_basis.py',),
        "index": ('14y',),
        "locks": ('harness_payroll_hours_basis.py', 'harness_payroll_hours_basis_lock.py'),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        # The eleven pre-existing callers are named here for the FIRST time, beside the drill-down
        # that needed the fact registered: batch 22's posture for `rep_pay_snapshot`, taken again.
        "callers": {
            'app/modules/account/coa.py': ('_hours_basis',),
            'app/modules/commcalc/labour_coverage.py': ('_hours_basis',),
            'app/modules/hr/router.py': ('_hours_basis',),
            'app/modules/storeops/clock_feed.py': ('_basis',),
            'app/modules/storeops/clock_variance.py': ('_basis',),
            'app/modules/storeops/clock_variance_api.py': ('_hb',),
            'app/modules/storeops/payroll_evidence.py': ('_hb',),
            # §CORE-17.3x — the payroll drill-down reads the basis VOCABULARY (the bases, the paying
            # allowlist and the measured set) to answer which relation a person's hours came out of,
            # and keys its basis→relation map on those constants rather than on words of its own.
            'app/modules/storeops/payroll_hours_drilldown.py': ('_hb',),
            'app/modules/storeops/pto_accrual.py': ('_hours_basis',),
            'app/modules/storeops/router.py': ('_hours_basis',),
            'app/modules/storeops/salary_expense.py': ('_hb',),
            'app/modules/storeops/salary_owed.py': ('_hours_basis',),
        },
    },
    'status_detail': {
        "question": ("This status must fit a column — which part of it survives, and does the reader "
                     "know something was dropped?"),
        "homes": ('app/core/status_detail.py',),
        "index": ('CORE-18',),
        "locks": ('harness_status_detail.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/auto_calc.py': ('_status_detail',),
            'app/modules/commcalc/router.py': ('_status_detail',),
            'app/modules/commcalc/vidapay_sweep.py': ('_status_detail',),
        },
    },
    'feed_lineage': {
        "question": "Which table is the LIVE feed for this item, and which column means 'arrived'?",
        "homes": ('app/modules/commcalc/data_lineage_registry.py',),
        "index": ('19.43',),
        "locks": ('harness_data_lineage_guard.py', 'harness_feed_watchdog.py', 'harness_ingest_freshness.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/auto_calc.py': ('_lineage',),
            # §COMMCALC-41 — the registry of DERIVED BUILDS declares which tables make each stored
            # snapshot stale, so the post-landing hook can rebuild it. Every one of those table names
            # is dereferenced from here (`ASSET_LEDGER`, `PAYMENT_DETAIL`, `DISTRIBUTOR_INVOICES`,
            # `MI_RESIDUAL`, `COMMISSION_CALC_FEEDS`) rather than spelled beside the build: a second
            # copy of "which table is this feed" is exactly the divergence this registry exists to
            # stop, and `harness_derived_build_lock.py` §F fails the build on a literal.
            'app/modules/commcalc/derived_builds.py': ('_lineage',),
            # A DRILL-DOWN HAS TO NAME A TABLE, so the cash-recon tiles' provenance asks the
            # registry which feed the resolved bill-payment processor lands in, and which table is
            # the live sales feed (§CORE-17.3a). Naming either at the call site would be the copied
            # fact this registry exists to prevent — and a provenance claim nobody checked.
            'app/modules/closing/router.py': ('_lineage',),
            # THE SAME REASON, one batch on (§CORE-17.3k): the three commission disagreement reports
            # name a feed per ROW, so the declaration asks the registry which table each money
            # stream was read out of, which feeds the writer of a stored discrepancy row reads, and
            # which tables the sold union is. It also added the five commission feed CONSTANTS the
            # registry had only ever held as literals inside `COMMISSION_CALC_FEEDS`.
            'app/modules/commcalc/carrier_pay_drilldown.py': ('_lineage',),
            # THE SAME REASON again (§CORE-17.3l): the seven device-asset reports name a table per
            # figure — and the POS recon names one per ROW, because a store the POS file never
            # mentioned must name only the ledger. It also added the five device-asset table
            # constants and re-keyed every map that had spelled `asset_ledger` or
            # `vip_paygo_payments` as a literal, because naming a fact is only half the fix.
            'app/modules/asset/device_asset_drilldown.py': ('_lineage',),
            # §COMMCALC-34 — the ONE dated-owed read names the processor's feed through the
            # registry rather than spelling it: the read it absorbed carried that table name as a
            # LITERAL, and naming a fact is only half the fix (§19.18), so the copy went in the same
            # commit. `harness_billing_period.py` §H2 fails the build if the literal reappears.
            'app/modules/payables/owed_read.py': ('_lineage',),
            # The targets drill-down asks the registry WHICH TABLES the sales feeds and the
            # daily closing are, so a target-vs-actual panel never spells a relation name of
            # its own (§CORE-17.3m). The four relations it names that are NOT registered
            # ingest feeds — the two target settings tables, the store roster and the shift
            # schedule — are config rows, deliberately kept out of the ingest registry so the
            # feed watchdog's population stays the feeds.
            'app/modules/commcalc/targets_drilldown.py': ('_lineage',),
            # The bill-payment / processor-money drill-down asks the registry which tables the
            # sales display union, the bill-pay settlement feed and the dealer-account payment
            # feeds are, so no panel on these three reports spells a relation name of its own
            # (§CORE-17.3n). `billpay_processor_feed` is dereferenced rather than re-mapped.
            'app/modules/commcalc/processor_money_drilldown.py': ('_lineage',),
            # §CORE-17.3x — the PAYROLL and WORKFORCE declarations ask the registry which relation
            # records a worked hour (the closed punch), which one records a PLANNED one (the
            # schedule), and which one holds a person's pay rate. The last of those, and
            # hand-entered hours beside it, became registry NAME constants in this batch with their
            # one pre-existing literal copy (`account/liabilities_drilldown`) re-keyed in the SAME
            # commit — naming a fact is half a design fix (§19.18).
            'app/modules/storeops/payroll_hours_drilldown.py': ('_lineage',),
            'app/modules/storeops/scheduled_hours_drilldown.py': ('_lineage',),
            # THE SAME FACT, ONE LAYER DOWN. `processor_ledger.FEED_SHAPES` spelled the payment
            # and daily-TX table names as LITERALS, which were a second copy of two constants the
            # registry already held — and the drill-down above reads that map for a cell's
            # provenance, which would have made it a third. The shapes dereference the registry now
            # (string-identical values, no read changed).
            # The accessory-money drill-down asks the registry which tables the sales display
            # union is, which two relations the PAY engine's own legacy read walks (in its own
            # order — the monthly upload leads, which is the reverse of the display union's lead)
            # and which table the product catalogue is (§CORE-17.3o). The target settings row and
            # the plan-rule / flag-rules config rows are NOT registered feeds: the first is
            # dereferenced from the targets family, the other two are named once here, the same
            # posture `targets_drilldown` takes.
            'app/modules/commcalc/accessory_money_drilldown.py': ('_lineage',),
            # The Accessory Order Plan's declaration (§COMMCALC-49) asks the registry for ONE fact —
            # which relations the sales DISPLAY union reads (`SALES_DISPLAY_SOURCES`) — and names no
            # table of its own. Its figures are device-unit counts off those relations, so a
            # restated table name there would be a provenance claim nothing checks.
            'app/modules/commcalc/accessory_order_drilldown.py': ('_lin',),
            # The device-financing drill-down asks the registry which relation the distributor's
            # INVOICE feed is (`DISTRIBUTOR_INVOICES`, added by batch 16 — this file held no literal
            # copy of it, so there was nothing to re-key), which one its UNIT LEDGER is, and which
            # one the carrier's PER-LINE feed is. Those last two are what separate its two device
            # grains, so a copied name there would be a provenance claim nothing checks. The weekly
            # lending batches are dereferenced from the device-asset family, which named them once
            # already, and the sales provenance is not a constant at all: it is resolved off the
            # read's own meta through `processor_money_drilldown.sales_union_tables` (§CORE-17.3p).
            'app/modules/commcalc/device_financing_drilldown.py': ('_lineage',),
            # The executive-month drill-down asks the registry which relation the RECORDED CHARGEBACK
            # is and which one the STORE ROSTER is (§CORE-17.3q). Both are NAME constants added by
            # batch 17 — and `ops_chargeback` was already spelled as a LITERAL in TWO drill-down
            # declarations (`closing/ops_chargeback_drilldown` and `closing/envelope_drilldown`),
            # which is the divergence this registry exists to prevent. Both dereference it now,
            # string-identical values, no read changed: naming the fact here is half the fix and
            # deleting every copy in the same commit is the other half. The snapshot and the target
            # settings are NOT asked of the registry — they are dereferenced from the declarations
            # that named them once already (`carrier_pay_drilldown`, `targets_drilldown`).
            'app/modules/commcalc/exec_month_drilldown.py': ('_lineage',),
            'app/modules/closing/ops_chargeback_drilldown.py': ('_lineage',),
            'app/modules/closing/envelope_drilldown.py': ('_lineage',),
            # THE SAME FACT, ONE LAYER DOWN. `raw_catalog` was spelled as a LITERAL four times
            # inside this registry — the ingest list, the no-month reason, the snapshot set and the
            # cadence note — and the drill-down above would have made it a fifth copy. Re-keyed
            # onto `PRODUCT_CATALOG` in the SAME commit, string-identical values, no read changed.
            # The sales-and-margin drill-down asks the registry which relation the ENTERED
            # EXPENSE SHEET is and which one the SHIFT SCHEDULE is (§CORE-17.3r). Both are NAME
            # constants added by batch 18 — and `shifts` was already spelled as a LITERAL in TWO
            # drill-down declarations (`commcalc/targets_drilldown` and
            # `account/liabilities_drilldown`), which is the divergence this registry exists to
            # prevent. Both dereference it now, string-identical values, no read changed: naming
            # the fact here is half the fix and deleting every copy in the same commit is the other
            # half, and `harness_batch18_drilldown.py` §A6/§A7 fail the build if either literal
            # returns or a caller stops dereferencing. The sales display UNION is NOT asked of the
            # registry here — it is dereferenced from `processor_money_drilldown`, which is the one
            # home for the question 'which tables did this period's sales actually come from'.
            'app/modules/commcalc/sales_margin_drilldown.py': ('_lineage',),
            # §CORE-17.3s. The two CARRIER ACTIVITY REPORTS became registry constants in that PR and
            # the registry's own five literal copies of their names were re-keyed to them in the
            # same commit — the other half of the fix, which `harness_batch19_drilldown.py` §A3
            # fails the build over if a second copy ever reappears. The APPROVED EXPECTED-TO-EARNED
            # MOVES are deliberately NOT a constant here: `expected_commission.TABLE` is already
            # that fact's one home and this declaration dereferences THAT, because a second copy in
            # the registry would be the very defect the registry exists to prevent.
            'app/modules/commcalc/carrier_owed_drilldown.py': ('_lineage',),
            # §CORE-17.3t. The TIME CLOCK became a registry constant in that PR, because a figure
            # that is output PER HOUR WORKED has to name the relation an hour came out of — and
            # `timelog` was already spelled as a LITERAL in `account/liabilities_drilldown`, which
            # is the divergence this registry exists to prevent. That copy dereferences the constant
            # now, string-identical value, no read changed, and `harness_batch20_drilldown.py` §A3c
            # fails the build if the literal reappears in any drill-down declaration. The carrier's
            # per-door report and the recorded penalty ledger are dereferenced as the constants
            # earlier batches named; the REP-PAY SNAPSHOT is deliberately not a constant here at all
            # (`carrier_pay_drilldown.REP_COMMISSIONS` is its one home), and the sales provenance is
            # resolved off the read's own meta through `processor_money_drilldown`.
            'app/modules/commcalc/performance_coaching_drilldown.py': ('_lineage',),
            # §CORE-17.3u. The STORED FINANCIAL STATEMENTS became a registry constant in that PR —
            # and `account_statements` was already spelled as a LITERAL in a drill-down declaration
            # (`account/statement_drilldown`), which is the divergence this registry exists to
            # prevent. It dereferences the constant now, string-identical value, no read changed:
            # naming the fact here is half the fix and deleting the copy in the same commit is the
            # other half, and `harness_batch21_drilldown.py` §A4b/§A5b fail the build if the literal
            # returns to any declaration or that caller stops dereferencing. The carrier's own
            # compensation statement is the registry's existing name. The sales provenance is NOT
            # asked of the registry here: it is resolved off the read's own meta through
            # `processor_money_drilldown.sales_union_tables`, which that PR extended to answer for a
            # multi-month RANGE read as well, in the ONE resolver rather than a second beside it.
            'app/modules/commcalc/sales_outcome_drilldown.py': ('_lineage',),
            # §CORE-17.3v. The subsidy-coverage drill-down asks the registry which relations the
            # CARRIER PAYMENT DETAIL and the COMPENSATION STATEMENT are, and the activation
            # drill-down which one the MONTH-END UPLOAD is. All three are constants earlier batches
            # named: no new registry entry was needed and none was added, because a second name for
            # a relation already in here is the divergence this registry exists to prevent. The
            # company commission settings these panels also name are deliberately NOT a registry
            # entry — a config row somebody typed is not an external feed and does not belong in
            # the feed watchdog's population, which is the posture `targets_drilldown.TARGETS`
            # already takes.
            'app/modules/commcalc/entitlement_coverage_drilldown.py': ('_lineage',),
            'app/modules/commcalc/activation_count_drilldown.py': ('_lineage',),
            'app/modules/account/statement_drilldown.py': ('_lineage',),
            'app/modules/account/liabilities_drilldown.py': ('_lineage',),
            # THE SAME REASON, two batches on (§CORE-17.3w): the finance device-billing, inventory
            # and credit-memo declarations each have to say WHICH relation a figure was read out of,
            # so they ask the registry rather than spelling a table name at the call site. Between
            # them they added the five NAME constants it had only ever held as literals inside its
            # own maps -- the distributor's invoice LINES and SERIALISED UNITS, its credit memos,
            # the emailed inventory-value snapshot and the same report at device grain -- and every
            # literal copy this file itself held (seven of one, one of another) was re-keyed onto
            # them in the SAME commit, which is the half of a design fix §19.18 says gets skipped.
            'app/modules/account/device_billing_drilldown.py': ('_lineage',),
            'app/modules/account/inventory_value_drilldown.py': ('_lineage',),
            'app/modules/account/memo_recon_drilldown.py': ('_lineage',),
            'app/modules/commcalc/processor_ledger.py': ('_lineage',),
            'app/modules/commcalc/dlar_sweep.py': ('_lineage',),
            'app/modules/commcalc/email_sweep.py': ('_lineage',),
            'app/modules/commcalc/epay_sweep.py': ('_lin',),
            'app/modules/commcalc/inventory_sold_recon.py': ('_dlr',),
            'app/modules/commcalc/ma_recon.py': ('SALES_DISPLAY_SOURCES',),
            # The takeover evaluation asks the registry which columns hold a row's MONTH, so its
            # "what did this file actually put in the table" measurement cannot drift from the
            # platform's own answer (`prospect_eval.substance`, owner report 2026-10-13).
            'app/modules/commcalc/prospect_eval.py': ('MONTH_COLUMNS', 'store_account_column'),
            # WHICH COLUMN NAMES THE DEALER ACCOUNT on each master-agent feed. Three hand-written
            # copies of that fact lived in this file, once per booking function, until the
            # store-takeover evaluation needed the same answer to split a prospect's uploads by
            # store (2026-10-13). Declared once in the registry, dereferenced here.
            'app/modules/account/ma_store_pnl.py': ('_MA_DAILY_TX_TABLE', '_store_col'),
            # The portal pull asks the registry whether its TARGET TABLE stores a row's month
            # (§COMMCALC-04), instead of carrying its own `has_period` flag per report spec.
            'app/modules/commcalc/report_pull.py': ('_lin',),
            'app/modules/commcalc/router.py': ('_dlr', '_lineage'),
            'app/modules/pos/inventory_integrity_router.py': ('_dlr',),
        },
    },
    'ma_reported_income': {
        # NOTE: no migration number in this string — it is a backend string, and the §19.36
        # setup-internals lock (rightly) refuses one in a shape the customer boundary cannot
        # neutralize. The column and its migration live in `ma_store_pnl.PL_CONFIG_COLUMNS`.
        "question": ('What MA/VidaPay income does a report show, on what timing basis — asked PER '
                     'month-of-life RUNG, because each rung is reported by only one of the two '
                     'feeds — at what store grain?'),
        "homes": ('app/modules/account/ma_store_pnl.py', 'app/modules/account/residual_subs.py'),
        "index": ('4a', '4b', 'COMMCALC-17'),
        "locks": ('harness_ma_income_one_home_guard.py', 'harness_mi_residual_store_grain.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            # `_msp_spec` added 2026-10-16 (§COMMCALC-15): the chart INSERTS the M2–M12 twin of the
            # commission line, taking the destination key, the label suffix and — through
            # `carrier_comm_source_line()` — which line it divides from THIS home, rather than
            # typing a second line beside the first.
            'app/modules/account/coa.py': ('_MA_COMPONENTS', '_msp', '_msp_spec', '_rs', '_rs_mi', '_rs_mi_idx'),
            # `_NO_RULE_REASON` added 2026-10-19 (§COMMCALC-20): the fulfillment-side coverage
            # read-out reports an unruled order-type family with the SAME honest-absence literal the
            # daily-tx coverage uses, and reads the org's stated reasons through THIS home's
            # `load_unbooked_reasons` rather than keeping a second copy of either.
            'app/modules/account/device_cogs.py': ('_NO_RULE_REASON', 'load_unbooked_reasons'),
            'app/modules/account/ledger_pnl.py': ('_msp',),
            'app/modules/account/router.py': ('_msp', 'residual_subs'),
            'app/modules/account/statement_engine.py': ('_msp',),
            'app/modules/commcalc/commission_received.py': ('ma_commission_components',),
            'app/modules/commcalc/comp_trend.py': ('_msp', '_rs'),
            # §COMMCALC-03 added `_rsub` — `default_ma_pnl_config()` for the takeover evaluation,
            # so a prospect's MA TX rows book under the SAME resolved config the live P&L books under.
            # `_msp_leg` (added 2026-10-15, index §COMMCALC-12): the leg trend's per-store MA grain
            # asks THIS home's `canonical_store_index` for processor account -> store, instead of the
            # trend deciding that mapping itself. One more dereference, not one more rule.
            'app/modules/commcalc/router.py': ('_MA_LEG_COMPONENTS', '_msp', '_msp_gp', '_msp_leg', '_msp_ma', '_rs_gp', '_rsub', 'load_store_index'),
            'app/modules/commcalc/whatif.py': ('residual_subs',),
            'app/modules/marketing/router.py': ('_MA_COMPONENTS',),
            'app/modules/payables/engine.py': ('_msp',),
        },
    },
    'closing_cash_era': {
        "question": 'For this daily_closing row, which columns carry the drawer and the bill-pay cash?',
        "homes": ('app/modules/closing/envelope_report.py',),
        "index": ('23m', '23p'),
        "locks": ('harness_envelope_receipt_basis.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/attention_providers.py': ('_er',),
            'app/modules/closing/billpay_pickup.py': ('envelope_report',),
            'app/modules/closing/external_credit_recon.py': ('_envelope_report',),
            'app/modules/closing/pickup_actual.py': ('count_fields',),
            # The receipt's drill-down reads the STATUS VOCABULARY from here (§CORE-17.3g) rather
            # than listing short/over/match again, so a status added to `envelope_report.STATUSES`
            # cannot leave a tile counting the wrong rows. It derives no cash figure of its own.
            'app/modules/closing/envelope_drilldown.py': ('_envelope_report',),
            'app/modules/closing/router.py': ('_er', 'envelope_report_mod'),
        },
    },
    'line_class': {
        "question": 'What kind of line is this sales row, and does it count as a new activation?',
        "homes": ('app/modules/commcalc/line_class.py',),
        "index": ('6', 'COMMCALC-03.23'),
        "locks": ('harness_line_class_lock.py', 'harness_activation_event_lock.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`, but CHECK THE
        # DIFF: the rewriter strips the hand-written comments out of these `callers` dicts, so the
        # two §COMMCALC-43 entries below were added by hand.
        "callers": {
            'app/modules/asset/router.py': ('_lc',),
            # §COMMCALC-43. "A BYOD activation and an upgrade on one number" is a question about
            # ACTIVATION EVENTS, and this home is the only thing that knows what one is: it folds an
            # invoice's activation-type lines into events keyed on the line's own phone number and
            # resolves each event's class by the org's declared precedence. The detector therefore
            # owns no class vocabulary and no phone normaliser of its own (§D7 / §G3 of its lock
            # fail the build if either appears), and the 'BYOD Upgrade' line that is ONE byod event
            # rather than a pair is correct here for free.
            'app/modules/commcalc/byod_upgrade_day.py': ('_lc',),
            # The §COMMCALC-43 endpoint and the watchdog tick resolve the ORG's rules
            # (`router._line_rules_of`) and hand them to the detector, so a tenant whose POS spells
            # the activation kind in `category` is classified by its own declaration rather than by
            # the house default — and they fall back to HOUSE_RULES by name, never by re-deriving.
            'app/modules/commcalc/watchdog_router.py': ('_lc',),
            'app/modules/closing/router.py': ('_lcls',),
            'app/modules/commcalc/calculator.py': ('_lc',),
            'app/modules/commcalc/commission_engine.py': ('_lc',),
            'app/modules/commcalc/ma_recon.py': ('_lc',),
            'app/modules/commcalc/payout_audience.py': ('_lc',),
            'app/modules/commcalc/payout_structure.py': ('_lc',),
            'app/modules/commcalc/plan_options.py': ('_lc',),
            # The takeover evaluation counts its activations per month through THIS classifier rather
            # than a New/Add tally of its own — a takeover pack's vocabulary is declared for its own
            # columns, and the classes, the count and the labels all come from here. §COMMCALC-03.23.
            'app/modules/commcalc/prospect_eval.py': ('_lc',),
            'app/modules/commcalc/router.py': ('_lc',),
            'app/modules/commcalc/sales_comparison.py': ('_lc',),
            'app/modules/commcalc/zero_sales.py': ('_lc',),
            'app/modules/core/plan_sources.py': ('_lc',),
        },
    },
    'rep_pay_snapshot': {
        "question": 'Which relation holds the CALCULATED rep-commission snapshot — the stored '
                    'result of a commission run, as opposed to anything a carrier reported? A '
                    'figure read out of it is a figure this platform computed and saved, so a '
                    'panel over it must name THAT and never a carrier feed. The name is '
                    'deliberately not a `data_lineage_registry` constant: the snapshot is written '
                    'by this platform, not fed to it, and a second spelling in the feed registry '
                    'would be the divergence that registry exists to prevent (§CORE-17.3t).',
        "homes": ('app/modules/commcalc/carrier_pay_drilldown.py',),
        "index": ('CORE-17',),
        "locks": ('harness_batch22_drilldown.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            # §CORE-17.3v. The commission dashboard and the Rep Breakdown tab fold the same
            # snapshot, so they dereference its name here rather than spelling it a third time.
            'app/modules/commcalc/rep_incentive_drilldown.py': ('_cpd',),
            # The siblings this fact was already true for, named here for the first time because
            # the fact itself had no home block until §CORE-17.3v needed one. Nothing about their
            # wiring changed: each already dereferenced `REP_COMMISSIONS` rather than spelling it.
            'app/modules/commcalc/exec_month_drilldown.py': ('_cpd',),
            'app/modules/commcalc/performance_coaching_drilldown.py': ('_cpd',),
            'app/modules/commcalc/sales_margin_drilldown.py': ('_cpd',),
            'app/modules/commcalc/targets_drilldown.py': ('_cpd',),
            'app/modules/commcalc/router.py': ('_cpdd',),
        },
    },
    'activation_bucket_column': {
        "question": 'Which payload column holds each activation bucket\'s count? The bucket '
                    'vocabulary, which families are excluded from the headline activation total '
                    'and which column each bucket is written under are ONE fact: a fold writes a '
                    'bucket under a column key and a drill-down has to name that figure by the '
                    'same key, so a second copy of the mapping is a panel that silently explains '
                    'the wrong number. It was a literal map inside `commcalc/router` until '
                    '§CORE-17.3v moved it here and deleted that copy in the same commit.',
        "homes": ('app/modules/commcalc/activation_bucketing.py',),
        "index": ('CORE-17',),
        "locks": ('harness_batch22_drilldown.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            'app/modules/commcalc/activation_count_drilldown.py': ('_act',),
            # THE OTHER HALF OF THE MOVE, registered in the same commit: the router no longer holds
            # its own `_ACT_BUCKET_FIELD` map and dereferences the one home. A hand-written edge for
            # the new home without one for the file that stopped spelling the literal is half a
            # registration, and half a design fix is what §19.18 is a list of.
            'app/modules/commcalc/router.py': ('_ab', '_ab_cats', '_ab_pol', '_act_bucketing'),
        },
    },
    'boost_payout_terms': {
        "question": "What does this tenant's Boost KPI-tier configuration actually pay, and what bar "
                    "is each measure scored against?",
        "homes": ('app/modules/commcalc/boost_terms.py',),
        "index": ('6p',),
        "locks": ('harness_boost_terms_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/calculator.py': ('_boost_terms',),
            'app/modules/commcalc/payout_structure.py': ('_bt',),
            'app/modules/commcalc/router.py': ('_bt',),
        },
    },
    # ── §COMMCALC-37 (owner ask 2026-10-26) — a device's arrival on the shelf has ONE date ──────
    'device_stock_age': {
        "question": 'WHEN DID THIS DEVICE ARRIVE ON THE SHELF, HOW LONG HAS IT SAT THERE, AND IS IT '
                    'STILL THERE? `inventory_aging_device.received_date` is NULL on all 10,525 rows '
                    'house-wide (measured 2026-10-26) — the POS export carries the unit\'s AGE, not a '
                    'date — so the receipt date must be DERIVED, and three callers each derived it: '
                    'the `/inventory-recon` endpoint, the management-incentive aging gate, and '
                    '`device_history.build_aging_inventory`, which read `days_in_stock` VERBATIM and '
                    'therefore reported a July age in October for any unit whose snapshot was stale. '
                    'The age is counted to TODAY, never read off the file. "On the shelf" is `on_hand` '
                    'AND the org\'s CURRENT snapshot date, because a failed off-hand flip leaves '
                    '`on_hand=true` on a unit long gone (balance_sheet\'s ~$129k of ghosts). The '
                    'WINDOW is not decided here: it is the carrier\'s declared `sell_within_days`, '
                    'resolved by `device_aging_policy` (§COMMCALC-26) — and an unresolved device does '
                    'NOT inherit the house 60, because printing someone else\'s deadline over a rep\'s '
                    'phone is the defect that section was opened for.',
        "homes": ('app/modules/commcalc/inventory_stock_age.py',),
        "index": ('COMMCALC-37', 'COMMCALC-36'),
        "locks": ('harness_inventory_stock_age.py', 'harness_inventory_stock_age_guard.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`. Hand-written for
        # the reason recorded in §COMMCALC-01: --bless strips per-caller comments, and the three
        # collapsed callers are exactly what a future reader needs explained. Verified by running the
        # guard (not --bless) afterwards.
        "callers": {
            # The two that already derived it correctly, and now dereference instead: the
            # `/inventory-recon` endpoint (`_derived_received`) and the management-incentive aging
            # bonus gate (`_mi_ever_over_days`), whose own comment recorded the shared fact beside its
            # private copy of it. Plus the rep's shelf card endpoint (`my_shelf`), which owns no
            # arithmetic at all.
            'app/modules/commcalc/router.py': ('_isa',),
            # The one that DISAGREED. It believed the file's `days_in_stock`, so the same device aged
            # 9 days here and 102 days through the other two paths.
            'app/modules/commcalc/device_history.py': ('_isa',),
        },
    },
    'trade_in_basis': {
        "question": 'Which record says a trade-in happened — the till or the carrier statement — and '
                    'how many does this rep get paid for?',
        "homes": ('app/modules/commcalc/trade_in_basis.py',),
        "index": ('COMMCALC-01',),
        "locks": ('harness_trade_in_basis.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`, but see the
        # caveat recorded in §COMMCALC-01: --bless rewrites a callers block to BARE lines and strips
        # the per-caller comments from every fact it touches, so this block is hand-written and the
        # derived edge below was verified by running the guard (not --bless) afterwards.
        "callers": {
            # The pay engine. It carried the answer itself — payment-detail rows whose type contained
            # 'trade' — so a month the carrier had not reimbursed yet paid $0 for trade-ins the stores
            # really did (Aug 2026: 30 at the till, 0 on the statement). It now resolves the basis,
            # the declaration and both counts here and keeps no predicate of its own.
            'app/modules/commcalc/calculator.py': ('_tib',),
        },
    },
    'attach_qualifier': {
        "question": 'Did this SALE earn the line its full declared rate, or the lesser one — and who '
                    'decides what counts toward the condition?',
        "homes": ('app/modules/commcalc/attach_qualifier.py',),
        "index": ('COMMCALC-06',),
        "locks": ('harness_attach_qualifier.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`, but see the
        # caveat recorded in §COMMCALC-01: --bless rewrites a callers block to BARE lines and strips
        # the per-caller comments from every fact it touches, so this block is hand-written and the
        # derived edge below was verified by running the guard (not --bless) afterwards.
        "callers": {
            # The plan pay engine. Before this, EVERY gate it had could only turn a payment OFF
            # (`plan_pay_gate` suppresses; `financing_tiers` re-rates from a MONTH's attainment), so no
            # rule could earn a lesser rate from a fact about the SALE its line belonged to. It now
            # resolves the condition, both rates and the on-screen sentence here, injects its own
            # `_rule_matches` and the accessory predicate, and keeps no threshold test of its own.
            'app/modules/commcalc/commission_engine.py': ('_aq',),
            # The SAVE path, and it is a caller for a reason worth stating: that path is
            # DELETE-THEN-INSERT with an explicit column list, so a qualifier column it does not
            # write back is WIPED the next time anyone saves the plan. It dereferences the measure and
            # scope allowlists from here rather than retyping them, so the vocabulary cannot drift
            # between what an admin can save and what the engine will honour.
            'app/modules/commcalc/router.py': ('_aqm',),
            # The plan editor's VOCABULARY. The screen had no input for a qualifier at all until this
            # was wired, so the columns could only be set by hand — and the save path above then wiped
            # them. This serves the measures, the scopes and the defaults by dereferencing the home, so
            # the browser names no measure and no default of its own.
            'app/modules/commcalc/plan_options.py': ('_aq',),
        },
    },
    'carrier_claim_value': {
        "question": 'Is this unpaid activation a CLAIM against the carrier at all, and what is it '
                    'worth?',
        "homes": ('app/modules/commcalc/carrier_claim_value.py',),
        "index": ('COMMCALC-22',),
        "locks": ('harness_carrier_claim_value.py',),
        # SNAPSHOT -- hand-written (--bless strips per-caller comments, SSCOMMCALC-01); the derived
        # edges were verified by RUNNING the guard afterwards.
        "callers": {
            # The carrier-statement recon: hardcoded 0.0 on every discrepancy row before this, which
            # is why the hub's open-$ card summed to nothing. Now classifies and prices through here.
            'app/modules/commcalc/ma_recon.py': ('ccv',),
            # THE SIBLING, fixed in the same change: the rate-card engine filed an unsizeable claim
            # as $0.00 too. It now writes NULL and borrows this module's subtraction.
            'app/modules/commcalc/discrepancy_engine.py': ('ccv',),
        },
    },
    'installment_exclusion_basis': {
        "question": 'WHY did this device category pay no multi-month installment -- did somebody '
                    'decide that, or did nobody?',
        "homes": ('app/modules/commcalc/installment_category.py',),
        "index": ('COMMCALC-21',),
        "locks": ('harness_installment_exclusion_basis.py',),
        # SNAPSHOT — hand-written (--bless strips per-caller comments, §COMMCALC-01); the derived
        # edges were verified by RUNNING the guard afterwards.
        "callers": {
            # The engine resolves the qualification and now gets the per-category basis with it, so a
            # category excluded BY A DECISION is reported apart from one nobody ruled on.
            'app/modules/commcalc/sale_installment_engine.py': ('icat',),
            # The notice list the owner reads after Run Calculation. `_icat_mod()` is the one accessor;
            # the bindings below are the AST import aliases, not the accessor's name.
            'app/modules/commcalc/router.py': ('CATEGORY_KEYS', '_icat', '_icat_dev', 'icat'),
            # The per-category FLAT payout (mig 256) shares this category vocabulary.
            'app/modules/commcalc/installment_category_payout.py': ('icat',),
            # Executive-MTD pay prices a TABLET / WATCH event through the same one classifier (§6n),
            # so these three share the category vocabulary without touching the exclusion basis.
            'app/modules/commcalc/line_class.py': ('_icat',),
            'app/modules/commcalc/plan_pay_gate.py': ('_icat',),
            'app/modules/commcalc/sales_comparison.py': ('_icat',),
        },
    },
    'payout_slice_evidence': {
        "question": 'Is this stored pay breakdown a figure at all, or a blank that prints as $0.00?',
        "homes": ('app/modules/commcalc/payout_slice_evidence.py',),
        "index": ('COMMCALC-09',),
        "locks": ('harness_payout_slice_evidence.py',),
        # SNAPSHOT — hand-written, not `--bless`-generated, because --bless rewrites a callers block to
        # BARE lines and strips the per-caller comments (the caveat recorded in §COMMCALC-01). The
        # derived edges were verified by RUNNING the guard afterwards.
        "callers": {
            # The ONE rep-row read. Every Rep Incentive surface goes through it (the table, the month
            # range which loops this handler, the exports, the emailed report), so the basis is stamped
            # here once instead of each surface deciding. Also the coaching card and the action plan,
            # which read the stored `subtotal` and used to multiply a never-computed one into an
            # "at risk" figure; and the plan-mode WRITER branch, which now itemises the row it creates
            # so it cannot produce a row this predicate could never flag.
            # `_pse` is the import inside the one read; `_pse_mod()` is the accessor the other
            # surfaces in this 48,000-line file call so none of them grows its own copy. The binding
            # below is the IMPORT alias the graph derives from the AST, not the accessor's name.
            'app/modules/commcalc/router.py': ('_pse',),
            # NOT LISTED, and deliberately: commcalc/rep_incentive_range.py is the fourth caller --
            # a month range is where adding an unknown in as zero printed a wrong total -- but its own
            # lock (harness_rep_incentive_range D5) forbids it ANY import, so `assemble(months,
            # per_month, evidence)` takes the module as a REQUIRED argument from the router instead.
            # There is no AST edge to declare; the dereference is locked by
            # harness_payout_slice_evidence G4a/G4b/G4c rather than by this graph.
            # The simulator BACK-DERIVES baseline sales from stored pay (acc_comm / rate), so a
            # never-written slice silently started a projection from the wrong baseline.
            'app/modules/commcalc/whatif.py': ('_pse',),
            # §CORE-17.3t. The Rep Coaching drill-down asks this home whether a stored pay breakdown
            # is EVIDENCE AT ALL before it prints an at-risk figure beside a person's name, rather
            # than reading the `component` column itself (the §CORE-17.3j ruling, one fact along).
            # An employee whose breakdown the run never itemised is shown WITHOUT a figure and is
            # not counted, in this module's own basis word — never as a person with $0.00 at risk,
            # which a manager would read as "nothing to discuss". Both the predicate and the field
            # name are bound by object identity, locked by `harness_batch20_drilldown.py` §A6.
            'app/modules/commcalc/performance_coaching_drilldown.py': ('_pse',),
            # §CORE-17.3v. The same ruling one report along: a rep whose payout slices the run never
            # itemised is shown WITHOUT a figure and is not counted, in this module's own basis
            # word — never as a rep who earned $0.00, which is what reading the raw column would
            # print beside a correct total.
            'app/modules/commcalc/rep_incentive_drilldown.py': ('_pse',),
        },
    },
    'commission_ledger_identity': {
        "question": 'Which ledger row is this, and which period does it book into?',
        "homes": ('app/modules/commcalc/commission_ledger.py',),
        "index": ('4b',),
        "locks": ('harness_ledger_identity_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/coa.py': ('_cl',),
            'app/modules/account/ledger_pnl.py': ('_cl',),
            # `_LEG_LABELS` added 2026-10-16 (§COMMCALC-15): the M2–M12 P&L line's own label is
            # built from `LEG_LABELS['trailing']`, so the statement line, the month-of-life card and
            # the per-store block cannot spell the trailing bucket three different ways.
            'app/modules/account/ma_store_pnl.py': ('_LEG_LABELS', 'month_leg_of'),
            'app/modules/commcalc/column_mapping.py': ('commission_ledger',),
            'app/modules/commcalc/commission_statement.py': ('CATEGORIES', 'CATEGORY_LABELS'),
            'app/modules/commcalc/ledger_batch.py': ('CL',),
            'app/modules/commcalc/ledger_ma_sync.py': ('commission_ledger',),
            'app/modules/commcalc/onboarding_intake.py': ('CL',),
            # `_month_token` is §60's binding of `parse_payment_month` — "does this label SAY a
            # month", which is exactly that function's own question (its docstring is explicit that
            # the month-of-life LEG question belongs to `month_leg_of` instead).
            # §COMMCALC-03.27 added `_cled` — `LEG_LABELS`, so the takeover report calls the
            # M1 and M2-M12 buckets exactly what the commission ledger calls them.
            'app/modules/commcalc/router.py': ('_cled', '_month_token', 'commission_ledger'),
            'app/modules/commcalc/sale_installment_engine.py': ('month_leg_of',),
        },
    },
    'month_focus_declaration': {
        "question": 'What did management DECLARE for this month — the focus, the pay type driving its '
                    'spiffs, the temporary spiffs on the table — and what does it still owe this week?',
        "homes": ('app/modules/commcalc/month_focus.py',),
        "index": ('64',),
        "locks": ('harness_month_focus.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04, and on 2026-10-09 it stripped the explanatory comments out of ten
        # callers dicts, so this package hand-edited the graph instead). The lock verifies it against
        # the real import graph.
        #
        # THE SIBLING THAT ANSWERS A NEIGHBOURING QUESTION: `commcalc.payout_config.custom_spiffs` is
        # what the engine actually PAYS; this fact is only an INTENT with a status. They are
        # deliberately two facts, and `month_focus.spiff_reconciliation` is the ONE place that
        # compares them — so a change to either has to keep that comparison honest rather than
        # merging the two into one.
        #
        # ONE direct caller, deliberately. The login popup's attention item
        # (`commcalc/import_audit._p_month_focus`) and the platform banner are TWO SURFACES over this
        # same due list, and both reach it through `router._month_focus_payload` rather than importing
        # this home again — so there is no second assembly to drift.
        "callers": {
            'app/modules/commcalc/router.py': ('_mfocus',),
        },
    },
    'people_visibility': {
        # Two dimensions of ONE declared fact, so they are one node: `visible_people_keyset` answers
        # whose rows are mine, `visible_store_codes` answers which stores reach me at all, and
        # `WORKED_AT_SOURCES` is the evidence registry both directions dereference (§14x). Splitting
        # them would invite exactly the drift the owner's "one fact, one home" rule forbids — a
        # manager's people list on the pins while their report moved to evidence.
        "question": 'Whose SHIFTS and whose STORES may this login read?',
        "homes": ('app/core/scope.py',),
        "index": ('14w', '14x'),
        "locks": ('harness_people_visibility_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/device_payable.py': ('store_market_resolver',),
            'app/modules/account/device_purchases.py': ('store_market_resolver',),
            'app/modules/account/residual_subs.py': ('_cscope',),
            'app/modules/account/router.py': ('_cscope',),
            'app/modules/account/statement_filter.py': ('core_scope',),
            'app/modules/asset/purchase_orders.py': ('_cscope',),
            'app/modules/asset/router.py': ('_cscope',),
            # 2026-10-09 — `closer_pick` and `month_focus` import `is_market_or_wider` from this
            # same home: "is this role's reporting scope a market or wider", which moved here when a
            # FOURTH copy of the scope-tier tuple was about to be written (index §63). They do not
            # read anybody's shifts; they read the scope tiers this file owns.
            'app/modules/closing/closer_pick.py': ('PICK_ANY_SCOPES', 'is_market_or_wider'),
            'app/modules/closing/ops_chargebacks.py': ('_cscope',),
            'app/modules/closing/router.py': ('_cscope', '_cscope_opts'),
            'app/modules/commcalc/month_focus.py': ('is_market_or_wider',),
            'app/modules/commcalc/processor_ledger.py': ('_cscope',),
            'app/modules/commcalc/router.py': ('_core_scope', '_cscope', '_tso_omo', 'sanitize_market_label'),
            'app/modules/core/onboarding.py': ('_cscope',),
            'app/modules/core/router.py': ('_cscope', '_omo', '_scope'),
            'app/modules/payables/router.py': ('_cscope',),
            'app/modules/pos/router.py': ('_cscope', 'in_keyset'),
            'app/modules/storeops/overhead_allocation.py': ('_market_by_code',),
            'app/modules/storeops/payroll_approval.py': ('_cscope',),
            'app/modules/storeops/router.py': ('_cscope',),
            'app/modules/storevisit/router.py': ('_cscope',),
            # The clock-variance board is person-keyed (one row per employee-day), so it rides
            # the SAME gate as every other person-keyed read: `gate_person_rows`, which narrows BOTH
            # the board and the login gate's blocking count to the stores this manager owns. A DM is
            # never gated by another district's unfixed rows (mig 1073, §14z).
            'app/modules/storeops/clock_variance_api.py': ('_cscope',),
        },
    },
    'payout_audience': {
        "question": 'Is this surface employee-facing, and therefore what may it show?',
        "homes": ('app/modules/commcalc/payout_audience.py',),
        "index": ('6',),
        "locks": ('harness_payout_audience_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/commission_drilldown.py': ('_pa',),
            'app/modules/commcalc/commission_statement.py': ('_pa',),
            'app/modules/commcalc/router.py': ('_pa',),
            'app/modules/core/router.py': ('_pa',),
        },
    },
    'paramount_kpi': {
        "question": "What is this store's Paramount KPI score?",
        "homes": ('app/modules/commcalc/paramount_kpi.py',),
        "index": ('10',),
        "locks": ('harness_paramount_kpi_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/router.py': ('parse_paramount_mtd_kpis',),
        },
    },
    'column_tolerance': {
        "question": 'Which column of this uploaded sheet holds the value I asked for?',
        "homes": ('app/core/column_tolerant.py',),
        "index": ('2',),
        "locks": ('harness_any_columns_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/billpay_pl.py': ('_ct',),
            'app/modules/account/ledger_pnl.py': ('_ct',),
            'app/modules/account/ma_store_pnl.py': ('_ct',),
            'app/modules/account/residual_subs.py': ('_ct',),
            # §58 — the carrier-dollar classification's per-org config columns (mig 1062) are
            # PROBED, never assumed, so the code is inert until the migration is applied.
            'app/modules/commcalc/carrier_dollar_class.py': ('_ct',),
            'app/modules/commcalc/commission_engine.py': ('_ct',),
            'app/modules/commcalc/dlar_sweep.py': ('_ct',),
            'app/modules/commcalc/expenses_effective.py': ('_ct',),
            # §19.51 — the residual installment ledger's mig-1059 columns are PROBED, never assumed,
            # so merging that code before the migration is applied writes what it wrote before.
            'app/modules/commcalc/installment_engine.py': ('_col',),
            'app/modules/commcalc/router.py': ('_ct',),
            # §COMMCALC-03 — the prospect config is read any-subset, so this module behaves
            # identically before and after mig 1077 is applied.
            'app/modules/commcalc/prospect_eval.py': ('_ct',),
            'app/modules/commcalc/setup_documents.py': ('read_row',),
            'app/modules/commcalc/whatif.py': ('_ct',),
            'app/modules/pos/customer_master.py': ('_ct',),
            'app/modules/pos/inventory_integrity_router.py': ('_ct',),
            'app/modules/pos/receipt_import.py': ('_ct',),
            'app/modules/supply/store.py': ('present_columns',),
        },
    },
    'multimonth_offer': {
        "question": 'Does this org offer multi-month / installment payouts at all?',
        "homes": ('app/modules/commcalc/multimonth_config.py',),
        "index": ('8',),
        "locks": ('harness_multimonth_offer_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/router.py': ('_mmc',),
        },
    },
    'landing_identity': {
        "question": 'Which report kind does an uploaded file land as?',
        "homes": ('app/modules/commcalc/landing_identity.py',),
        "index": ('2',),
        "locks": ('harness_landing_identity_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/onboarding_intake.py': ('_li',),
            'app/modules/commcalc/router.py': ('_landing',),
        },
    },
    'data_qa_semantic_layer': {
        "question": 'Which of this platform\'s own reports answers this business question?',
        "homes": ('app/modules/core/data_qa_registry.py',),
        "index": ('52.1',),
        "locks": ('harness_data_qa_lock.py', 'harness_data_qa_registry.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/core/data_qa_agent.py': ('reg',),
            'app/modules/core/data_qa_api.py': ('reg',),
        },
    },
    'data_qa_arithmetic': {
        "question": 'What is the total, the rank, the pivot or the chart over these report rows?',
        "homes": ('app/modules/core/data_qa_compute.py',),
        "index": ('52.2',),
        "locks": ('harness_data_qa_lock.py', 'harness_data_qa_compute.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/core/data_qa_agent.py': ('calc',),
        },
    },
    'period_stored_spelling': {
        "question": 'Which spelling is this month STORED under, and which spellings must a filter match?',
        "homes": ('app/modules/account/_period.py',),
        "index": ('19.47',),
        "locks": ('harness_period_one_spelling.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/analysis.py': ('_period',),
            'app/modules/account/autocompute.py': ('_MONTHS', 'period_keys'),
            'app/modules/account/coa.py': ('_period', 'parse_period'),
            'app/modules/account/engine.py': ('_period',),
            'app/modules/account/finance_attention.py': ('_pkeys', 'fin_period'),
            'app/modules/account/projection_engine.py': ('_period',),
            'app/modules/account/recon.py': ('_period',),
            'app/modules/account/residual_subs.py': ('parse_period', 'recent_period_keys'),
            'app/modules/account/router.py': ('_pd', '_period', 'parse_period', 'period_keys'),
            'app/modules/account/royalty.py': ('canonical_period', 'month_range', 'parse_period'),
            'app/modules/account/royalty_router.py': ('_period',),
            'app/modules/account/statement_engine.py': ('_period',),
            'app/modules/account/statement_filter.py': ('_per',),
            'app/modules/commcalc/auto_calc.py': ('_pd',),
            'app/modules/commcalc/commission_ledger.py': ('_pd',),
            'app/modules/commcalc/discrepancy_appeals.py': ('_pd',),
            'app/modules/commcalc/ledger_batch.py': ('_pd',),
            'app/modules/commcalc/ma_recon.py': ('_pd',),
            # §COMMCALC-03 added `_ap` here — a prospect's uploaded rows may be stamped in either
            # spelling, so the read asked this home for both and matched the row's text against the
            # two. §COMMCALC-03.28 REMOVED that binding and did not replace it with another: the
            # prospect's month slice now collapses the two spellings through
            # `prospect_eval.period_sort_key` (its single period parser) instead of comparing
            # strings, which is what let one slicer serve the month report and the month-over-month
            # P&L. The spelling home is unchanged and still has every other caller it had.
            'app/modules/commcalc/router.py': ('_pd',),
            # §COMMCALC-23 (owner directive 2026-10-20) — the derived-labour actuals read
            # `rep_commissions` for ONE month and must match both of its stored spellings, so the
            # month is expanded by THIS home. Added BY HAND, not blessed (§50).
            'app/modules/commcalc/labour_actuals.py': ('_pk',),
        },
    },
    'derived_labour_actuals': {
        "question": 'What did the platform\'s OWN labour rows (salary, rep commission) actually cost '
                    'in this month, per store — as opposed to what some other month cost?',
        "homes": ('app/modules/commcalc/labour_actuals.py',),
        "index": ('COMMCALC-23',),
        "locks": ('harness_labour_actuals.py', 'harness_expense_one_path.py'),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04 and still strips the per-caller comments). The lock verifies it against the
        # real import graph.
        "callers": {
            # Four surfaces whose only labour figure is an expense sheet, and which therefore may not
            # inherit another month's payroll: the GP report (SALARY only — `net_profit` already
            # subtracts `rep_pay`), the expenses trend, the Sales Comparison expense line and the
            # takeover evaluation's cost benchmark. The P&L is deliberately NOT here: it derives
            # `wages` and `rep_comm` from these same sources itself, so it needs the carried rows
            # gone and nothing put back.
            'app/modules/commcalc/router.py': ('_lact',),
        },
    },
    'statement_crosscheck_verdict': {
        "question": 'Was this statement actually crosschecked, and did it pass?',
        "homes": ('app/modules/account/analysis.py',),
        "index": ('19.49',),
        "locks": ('harness_statement_crosscheck_earned.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/engine.py': ('_analysis',),
            'app/modules/account/projection_engine.py': ('analysis',),
            'app/modules/account/router.py': ('analysis',),
            'app/modules/account/statement_engine.py': ('_analysis',),
            'app/modules/account/statement_filter.py': ('_analysis',),
            'app/modules/account/valuation.py': ('analysis',),
            # §COMMCALC-03 — the takeover evaluation's own statements get their totals from here, so
            # a prospect's four figures and a store's four figures are the same four figures. §60's
            # spiff impact used to read `pl_totals` here too; it goes through `store_pl_totals` now
            # (the fact below), which dereferences this one on its behalf.
            'app/modules/commcalc/router.py': ('_an',),
        },
    },
    'store_monthly_pl': {
        "question": 'What did this store of OURS earn in this month — its revenue, its gross '
                    'profit, its bottom line, and when was that computed?',
        "homes": ('app/modules/account/statement_filter.py',),
        "index": ('19.50', 'COMMCALC-03.24'),
        "locks": ('harness_prospect_eval.py', 'harness_spiff_impact.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            # TWO readers in one file, and the second is why this home exists: §60's spiff impact
            # had the loop inline (the deduped read, the store key, `analysis.pl_totals`, the
            # earliest `computed_at`) and §COMMCALC-03.24's takeover comparison needed the same
            # question answered a third time. The store KEY is injected — spiff impact keys by
            # store_code, the comparison by canonical address, one question either way.
            'app/modules/commcalc/router.py': ('_sf', '_sf_peer', 'resolve_store_matcher',
                                               'unbound_spellings'),
            # The module's OTHER export — `filtered_statement`, the store/market-filtered P&L the
            # Finance hub serves. It reads the same per-store snapshots, which is precisely why the
            # totals loop belongs in that module and not in whichever report wanted it first.
            'app/modules/account/router.py': ('statement_filter',),
        },
    },
    'complete_feed_read': {
        "question": 'Have I read ALL of this feed — every row, each exactly once — or only the '
                    'first N rows somebody typed, in whatever order the backend felt like?',
        "homes": ('app/modules/core/feed_read.py',),
        "index": ('19.48', 'CORE-05'),
        "locks": ('harness_feed_read_lock.py', 'harness_pay_feed_balance.py',
                  'harness_installment_month_anchor.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            # §19.51 — raw_mi is the feed §19.48's own docstring named as the next ceiling risk
            # (46,047 rows in September, growing ~4,000 a month); its private page loop is retired.
            'app/modules/commcalc/installment_engine.py': ('_feed_read',),
            'app/modules/commcalc/router.py': ('_feed_read',),
            # §CORE-05 — the appeal-recovery evidence reads. Their own copy of the page loop is
            # retired: it paged without an ORDER BY, so a device whose payment row was never served
            # read as "no evidence" and bucketed the appeal as not_recoverable.
            'app/modules/recovery/engine.py': ('_feed_read',),
            # §COMMCALC-27 — the three reads the declared-terms work added, wired to this home from
            # the start rather than keeping a fourth private page loop: the unsold per-IMEI ledger
            # (a device missed here reads as SOLD, which is the one thing the out-of-pocket verdict
            # must never get wrong), the vendor feed the invoice-due report falls back to, and the
            # model scan that builds the unit-price map (a model missed prices nothing).
            'app/modules/payables/engine.py': ('_feed_read',),
            'app/modules/payables/unsold_inventory.py': ('_feed_read',),
            'app/modules/payables/vendor_invoice.py': ('_feed_read',),
            # §COMMCALC-44 — the carrier's per-line payment feed, ~30,000 rows a month and growing.
            # It was already read through this home inline in the router; the read moved WITH the
            # question rather than reverting to a private page loop.
            'app/modules/commcalc/carrier_device_money.py': ('_feed_read',),
        },
    },
    'payment_category_map': {
        "question": 'Which pay CATEGORY did this org declare for this payment type — is this line '
                    'commission, a rebate, or something it has never mapped?',
        "homes": ('app/modules/commcalc/payment_category.py',),
        "index": ('57.1',),
        "locks": ('harness_payment_category_home_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            # §58 — the carrier-dollar COMPONENT ruling sits on top of this CATEGORY read and
            # keeps no copy of it; §57 decides the category, §58 decides what the category means.
            'app/modules/commcalc/carrier_dollar_class.py': ('_pc',),
            # The GROSS PROFIT engine reads the org's declared category for an ePay payment type
            # here rather than comparing it against four literals it spelled itself — one of which
            # only ever matched the house's own hyphenated "Re-imbursement" (owner 2026-10-08).
            'app/modules/commcalc/gp_report.py': ('_pc',),
            'app/modules/commcalc/processor_ledger.py': ('_pcat',),
            # `_pc` since §68: the entitlement-month report's router glue reads this home's own
            # `load_map` / `category_of` for "which payment types are the entitlement".
            'app/modules/commcalc/router.py': ('_payment_category', '_pc', '_pcat'),
            # §60 — the spiff-impact report folds a PAY-TYPE KEY (its option list and its per-store
            # tally must agree about whether two spellings are one type) and dereferences §57's one
            # folding rule rather than keeping a second. It reads the TABLE through §58, never here,
            # so §57's reader inventory is unchanged.
            'app/modules/commcalc/spiff_impact.py': ('_pc_fold',),
        },
    },
    'clawback_direction': {
        "question": 'Is this processor-feed row money the carrier TOOK BACK — and was it recognised '
                    'by the DIRECTION the money moved, or by a category name no tenant can declare?',
        "homes": ('app/modules/commcalc/clawback.py',),
        "index": ('55.2',),
        "locks": ('harness_clawback_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/flags.py': ('_clawback',),
            'app/modules/commcalc/router.py': ('_cb',),
            'app/modules/commcalc/withholding_report.py': ('_cb',),
        },
    },
    'appeal_state_machine': {
        "question": 'What are the legal appeal states for money the carrier has not paid, which '
                    'transitions are allowed, and who may stamp them?',
        "homes": ('app/modules/commcalc/discrepancy_appeals.py',),
        "index": ('15.3', '55.4'),
        "locks": ('harness_appeal_one_machine_lock.py',),
        # TWO row homes, ONE machine: discrepancy_results (mig 947) and commcalc.flags (mig 1060)
        # carry byte-identical column names so `apply_appeal` patches either with no branch.
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/router.py': ('_da',),
            'app/modules/commcalc/withholding_report.py': ('APPEAL_STATES',),
        },
    },
    'pay_feed_balance': {
        "question": 'Did this pay figure account for every dollar the carrier paid, or did it '
                    'silently drop what it could not place?',
        "homes": ('app/modules/commcalc/pay_data_quality.py',),
        # §19.54 — "and does this month's statement cover every day it is owed" is the same question
        # one layer out (a dollar that never ARRIVED cannot be placed), so it lives in the same home
        # and is locked by the new harness beside the old one.
        "index": ('19.48', '19.54'),
        "locks": ('harness_pay_feed_balance.py', 'harness_statement_month_coverage.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/accessory_cost_audit.py': ('_f', 'pdq'),
            # §68 — the entitlement-month report IMPORTS the three shipped verdict strings rather
            # than respelling them, so its tri-state can never drift from this home's.
            'app/modules/commcalc/entitlement_coverage.py': ('VERDICT_FAILED', 'VERDICT_NOT_MEASURED',
                                                             'VERDICT_PASSED'),
            # §55.2 — the one clawback test dereferences PLACEABLE_CATEGORIES for "which categories
            # are pay", minus the phantom `Chargeback` entry it exists to replace.
            'app/modules/commcalc/clawback.py': ('PLACEABLE_CATEGORIES',),
            'app/modules/commcalc/commission_drilldown.py': ('_pdq',),
            # §57.1 — the one payment-category home dereferences UNCATEGORISED (the word for "never
            # mapped") and PLACEABLE_CATEGORIES rather than restating either.
            'app/modules/commcalc/payment_category.py': ('PLACEABLE_CATEGORIES', 'UNCATEGORISED'),
            'app/modules/commcalc/commission_engine.py': ('_pdq_cfg',),
            'app/modules/commcalc/plan_pay_gate.py': ('_pdq',),
            'app/modules/commcalc/router.py': ('_pdq',),
        },
    },
    'expense_month_one_path': {
        "question": 'What did this store spend in this month — including a month it never saved?',
        "homes": ('app/modules/commcalc/expenses_effective.py',),
        "index": ('19.50',),
        "locks": ('harness_expense_one_path.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/coa.py': ('effective_expense_rows',),
            'app/modules/commcalc/labour_coverage.py': ('period_sort_key',),
            'app/modules/commcalc/router.py': ('_expfx', 'effective_series'),
            # §COMMCALC-03 — the takeover evaluation dereferences this home; it never queries
            # the table or re-derives the rule.
            'app/modules/commcalc/prospect_eval.py': ('period_sort_key',),
            # §COMMCALC-19 — "can this month-over-month expense change be measured at all" compares the
            # two carried periods through THIS home's spelling rule, so `2026-08` and `August 2026`
            # are one sheet rather than two. It dereferences `period_sort_key`; it does not re-spell it.
            'app/modules/commcalc/sales_comparison.py': ('_period_key',),
        },
    },
    'gated_detector_verdict': {
        "question": 'Did this check actually run, or is its clean result just an empty config?',
        "homes": ('app/modules/commcalc/labour_coverage.py',),
        "index": ('19.50',),
        "locks": ('harness_expense_one_path.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/coa.py': ('_lcov',),
            'app/modules/commcalc/gp_report.py': ('_lcov',),
            'app/modules/commcalc/router.py': ('_labour', '_lcov'),
        },
    },
    'target_axis': {
        "question": 'What is this tenant actually targetable ON — with what label, in what unit, and '
                    'is its target a column on commcalc.targets or a commcalc.target_line row?',
        "homes": ('app/modules/commcalc/targets_engine.py',),
        "index": ('COMMCALC-02',),
        "locks": ('harness_target_axis_lock.py', 'harness_target_declared_axis.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            # The carry-forward suggestion, the period summary, the calendar and the action plan all
            # walk `target_categories(catalog)` and ask `target_store(cat)` where each target lives.
            # It also hands the axis to the DM / market-manager report card, which imports nothing
            # by design (harness_manager_report_card D1) — so the card's items come from here.
            'app/modules/commcalc/router.py': ('_te', 'targets_engine'),
            # The targets drill-down asks `target_axis` what the categories are and
            # `target_store` / `BUILTIN_TARGET_COLUMN` WHERE each target physically lives, so
            # a panel names the settings row a number was typed into instead of guessing one
            # (§CORE-17.3m). It owns no axis and no arithmetic of its own —
            # `harness_batch13_drilldown` §Y13 fails the build if it grows either.
            'app/modules/commcalc/targets_drilldown.py': ('_te',),
            # The visit-priority dropdown offers the axis as assignable deliverables.
            'app/modules/storevisit/visit_plan_router.py': ('_te',),
        },
    },
    # ── §COMMCALC-11 (owner report 2026-10-15) — a check that GATES a sign-in ────────────────────
    # ── §POS-15 (owner ask 2026-10-30) — what a till may be paid with ───────────────────────────
    'register_tender_vocabulary': {
        "question": 'WHAT MAY A TILL BE PAID WITH, which of those does THIS org offer, and what '
                    'does a sale on terms owe? It was written FOUR times: two literal button '
                    'arrays in the register screen (one for a tenant with integrated card '
                    'processing, one without), the condition that chose between them, and '
                    '`CHECK (payment_method IN …)` on `pos.sale_payments`. Adding one '
                    'tender type therefore meant a screen, a condition and a constraint, in three '
                    'languages, with the build green if any of them was missed. The class a tender '
                    'RECONCILES as is NOT answered here — each entry carries the words a feed would '
                    'print and `closing.router.tender_class` (the house vocabulary, §30.13) '
                    'answers, so a terms sale files under the existing `store_acct` and no class '
                    'was invented. A sale on terms does not settle at the till: the server '
                    're-derives `pos.sales.balance` as the sum of the legs that did NOT settle, '
                    'because `pos.checkout` stores whatever it is handed, and no receipt prints '
                    '"Paid" on it (both layouts ask `settles_now`).',
        "homes": ('app/modules/pos/tender_vocabulary.py',),
        "index": ('POS-15',),
        "locks": ('harness_pos_tender_vocabulary.py', 'harness_tender_vocab_lock.py'),
        # SNAPSHOT — written BY HAND, never blessed (§50). The lock verifies it against the real
        # import graph.
        "callers": {
            # The endpoint the register reads, the checkout's gate, and the receipt's money line.
            'app/modules/pos/router.py': ('_tender',),
            'app/modules/pos/sale_receipt.py': ('_tv',),
        },
    },
    # ── §POS-16 (owner ask 2026-10-30) — a wholesale customer and their W-9 ─────────────────────
    'wholesale_w9_gate': {
        "question": 'MAY I SELL TO THIS CUSTOMER, AND IS THIS SALE TAXABLE? One question, because '
                    'tax-free is not a property of a customer TYPE — it is a property of a type '
                    'WITH a current exemption certificate on file. Answering the two separately '
                    'produces exactly one failure and it is the expensive one: a sale goes out '
                    'tax-free against a certificate nobody holds and the house carries the '
                    'liability. So `decide` answers both, no state exists where a sale is exempt '
                    'and the certificate is not current, and the sale RECORDS its basis '
                    '(`pos.sales.tax_exempt_basis`) because it cannot be re-derived later — the '
                    'certificate may be filed the next day. An admin override permits a TAXABLE '
                    'sale and never an exemption, and is named on the row. Only a PERSON can '
                    'confirm receipt: a customer\'s own upload through their return link lands '
                    '`submitted`, since an anonymous caller holding a link cannot be the user the '
                    'owner meant by "the user confirms they received the filled out w9".',
        "homes": ('app/modules/pos/wholesale_w9.py',),
        "index": ('POS-16',),
        "locks": ('harness_pos_wholesale_w9.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50).
        "callers": {
            # The W-9 panel's endpoint, the confirm / request routes, the two public token routes
            # and the checkout's gate — all in the one router, all dereferencing `decide`.
            'app/modules/pos/router.py': ('_w9',),
        },
    },
    # ── §POS-16.3 (2026-10-30) — what format an uploaded document may be ────────────────────────
    'upload_document_format': {
        "question": 'WHAT FORMAT MAY AN UPLOADED DOCUMENT BE? Sniff the MAGIC BYTES, never trust '
                    'the extension, check the result against the tenant\'s allow-list. The rule '
                    'lived inside `hr/router.py` and was reachable only from the three onboarding '
                    'upload routes; a wholesale customer sending back a completed W-9 (§POS-16) '
                    'asks the identical question from another module, and a second sniffer there '
                    'is how one path comes to accept a renamed executable while the other refuses '
                    'it. A file IS what its bytes say: the filename is read ONLY to word the '
                    'refusal when the header matches nothing known, and an empty allow-list reads '
                    'as the declared default rather than as "anything goes". One refusal wording, '
                    'so two upload screens cannot differ.',
        "homes": ('app/core/upload_format.py',),
        "index": ('POS-16',),
        "locks": ('harness_pos_wholesale_w9.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50).
        "callers": {
            # HR's three onboarding uploads (through its four thin dereferences) and the customer's
            # W-9 return upload. Neither keeps a sniffer, a magic-byte table or a refusal of its own.
            'app/modules/hr/router.py': ('_upload_format',),
            'app/modules/pos/router.py': ('_upload_format',),
        },
    },
    'human_check_on_sign_in': {
        "question": 'Does a human check ("I\'m not a robot") gate THIS page\'s sign-in, and of '
                    'which kind? Recognised by whether it is PAINTED was the defect: Google\'s '
                    'documented invisible setup renders the widget `data-size="invisible"` with a '
                    '`data-callback` and posts the form only from that callback, so it is present, '
                    'armed and gating — and `is_visible()` is False. The live session read that as '
                    'no check, auto-submitted past it, and reported a CORRECT password as a wrong '
                    'one. Three states, not a bool, because the two kinds need different words to '
                    'the operator: a visible challenge is on the screen, an invisible one appears '
                    'only once Sign in is pressed. The same fact answers "why was this submit '
                    'refused while still on the login form", which has two causes that send the '
                    'operator to opposite places (`human_check_blocked_message`) — both '
                    'post-submit sites dereference it before blaming the credentials.',
        "homes": ('app/modules/commcalc/vidapay_sweep.py',),
        "index": ('COMMCALC-11',),
        "locks": ('harness_human_check_kind.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50). The lock verifies it against the real
        # import graph.
        "callers": {
            # The live interactive login: `_human_check_kind` reads the kind and picks the
            # operator's words out of `_HUMAN_CHECK_MESSAGE`, instead of auto-submitting.
            'app/modules/commcalc/live_login.py': ('vp',),
            # The four other importers of the home. Each reaches it for the UNATTENDED paths —
            # `begin_login` / `begin_login_b2bsoft`, the browser primitives, `_classify` — and so
            # each inherits the answer through those entry points rather than asking again. NONE of
            # them may grow its own detector: a second "is a human check waiting" is the duplicate
            # this fact exists to stop, and §E of the lock pins the two post-submit sites that DO
            # ask. An unattended sweep CANNOT pass a check, which is why the honest message (not a
            # bad-password verdict) is what these paths surface to the operator.
            'app/modules/commcalc/merchant_portal_sweep.py': ('vp',),
            'app/modules/commcalc/portal_report_pull.py': ('vp',),
            'app/modules/commcalc/router.py': ('UnsafePortalUrlError', 'VidaPayAuthError',
                                              '_AuthErr', 'vp'),
            'app/modules/supply/portal.py': ('vp',),
        },
    },
    # ── §COMMCALC-13 (found 2026-10-15) — what a finished live login leaves on the row ───────────
    'live_login_terminal_status': {
        "question": 'What `data_source.auth_status` does a live login leave behind, and when is an '
                    'in-flight claim a LEFTOVER? `live_login_start` stamps \'authenticating\' '
                    'before the worker starts; §COMMCALC-10 taught a session that DIES to write '
                    '\'error\'; a session that merely ENDS (the inactivity timeout, or the operator '
                    'pressing Close) wrote only `auth_message` and left the status in flight, so the '
                    'chip read "Logging in…" for an hour after the browser had closed. A status is a '
                    'record of what IS: `terminal_status` restores the row\'s PRE-LOGIN status and '
                    'never hands back the in-flight one. The sibling is the worker KILLED mid-login, '
                    'which never writes an ending at all — and every merge to main redeploys both '
                    'hosts, so that is routine here; nothing on the row distinguishes it from a live '
                    'login, so the discriminator is AGE (`in_flight_is_stale`), with the budget '
                    'DERIVED from the two homes that already answer "how long may this take" rather '
                    'than copied. `present_auth_status` is what a surface reads. AND, the other '
                    'axis of the same home (§COMMCALC-24): WHICH PROCESS may answer about a '
                    'session at all. `_SESSIONS` is a dict in ONE process\'s memory, so on the '
                    'split deploy (§41) an endpoint that reads it from the API host sees an EMPTY '
                    'registry and reports "No live session running" about a session that is alive '
                    'on the worker. The old gate was spelled `require_browser_service` and asked '
                    '"does this launch a browser?"; /frame launches nothing, it reads a dict, so '
                    'the whole READ half of the operator surface was left ungated and the owner\'s '
                    'UPS login was invisible to him for five days while the browser sat on the '
                    'portal\'s Sign In button and timed out for operator inactivity. The question '
                    'that matters is "does this touch _SESSIONS?", so the gate is RENAMED here: '
                    '`require_session_host` is THE gate, and `sessions_live_here` is the pure fact '
                    'for a reader that must degrade rather than proxy. Both dereference '
                    '`app.core.service_role` — one reader of the role, not two. AND the third axis '
                    '(§COMMCALC-27): HOW PROMPTLY the operator\'s input reaches the page. The loop '
                    'is single-threaded — it streams frames, detects state and executes input on one '
                    'thread — and it serviced ONE queued command per iteration behind a full `_pump` '
                    '(80ms on CDP, 300ms without), while `_do_click` ended with a mandatory ~750ms '
                    'burst plus `_wait_settle` plus a re-classification. So each click was charged '
                    'for the previous click\'s VERIFICATION before being dequeued, the window felt '
                    'frozen ("i hvae to press hard", 2026-10-22), and pressing again lengthened the '
                    'queue. The rule: work that only makes sense once the operator has STOPPED never '
                    'sits on the latency path of their next action. `operator_input_pending` is the '
                    'ONE reader of "still acting" (pure; an unreadable queue reads as not pending, '
                    'keeping the old always-settle path), `_next_input` drains human input WITHOUT '
                    'reordering SUBMIT_CODE / RESEND / CANCEL / PULL, and BOTH loops drain, bounded '
                    'by `_INPUT_DRAIN_MAX` so a flood never starves the idle check. AND the fourth '
                    'axis of the same question (§COMMCALC-28), on the OTHER side of the wire: the '
                    'page polled `/live-login/frame` on a fixed-rate `setInterval(300)` and fanned '
                    'out three more timers per input. A fixed-rate timer fires whether or not the '
                    'last response came back, so once a poll outlived its interval — three hops, '
                    'browser to the page\'s API route to the API host to the session host — the '
                    'polls overlapped permanently, each carrying a stale `since` and so each '
                    'answered with a FULL frame of the SAME paint, holding the per-origin '
                    'connection budget the operator\'s own input needs. The owner reported the view '
                    'still laggy after the input fix landed ("it is very laggy", 2026-10-23). A '
                    'poll\'s cadence is a GAP BETWEEN RESPONSES: `pollFrames` is the ONE issuer, '
                    '`frameBusyRef` the ONE reader of "a request is in flight", a mid-flight nudge '
                    'is remembered rather than sent in parallel, and the next request is armed in a '
                    '`finally` from the moment the last one settled. AND the fifth axis '
                    '(§COMMCALC-30): WHO IS THERE. `_touch` is called by operator COMMANDS only, '
                    'never by the /state or /frame polls — deliberately, so an abandoned tab cannot '
                    'hold a browser open — but that left one fact answering two questions, and the '
                    '`human_action` budget, SHORTENED to 8 minutes on the assumption nobody is '
                    'acting, was applied to an operator who was reading the page or solving the '
                    'captcha the phase is named for. It closed the owner\'s 00:48 session at ~00:56 '
                    'while he was in it (2026-10-23). `_note_viewer` records a sighting from the two '
                    'methods that ANSWER about a session (`state`, `frame_since`), `viewer_present` '
                    'is the ONE reader of "a human is there" (pure; never seen reads as NOT watched), '
                    'and watching buys the ORDINARY budget, never a longer one — a longer one was '
                    'written and reverted because `in_flight_stale_after_s` derives the staleness '
                    'window from the longest an operator may hold a session, and widening it would '
                    'have made a killed worker\'s leftover row read as LIVE. That derivation now '
                    'dereferences `max_inactivity_limit`. Watching is not acting: `_note_viewer` '
                    'never touches `_last_activity`. AND the sixth axis of the '
                    'same question (§COMMCALC-31), which is §COMMCALC-27 found in a SECOND place: '
                    'the loop\'s own DETECTION sat on the operator\'s latency path too. '
                    '`_preauth_detect` walks every frame of the page (`_classify` reads each '
                    'frame\'s text, `_captcha_present` queries selectors), it ran on a fixed '
                    '`_CLASSIFY_POLL_S` timer, and it ran BEFORE the input drain — so a click could '
                    'wait a full `_pump` PLUS a whole page re-read before it was dequeued ("it is '
                    'very slow to react", 2026-10-26, four days after the click fix shipped). The '
                    'input drain now LEADS the tick in both loops, and `detect_due` is the ONE '
                    'answer to "may detection run now": deferred while `operator_input_pending` — '
                    'the same home §COMMCALC-27 already established, not a second queue peek — and '
                    'BOUNDED by `_DETECT_DEFER_MAX_S`, because a deferral with no ceiling is how a '
                    'captcha stops being noticed; past the ceiling it runs regardless. It is pure, '
                    'and unreadable inputs read as DUE, so the failure mode is the old always-detect '
                    'behaviour. AND the seventh (§COMMCALC-31 again, the reporting half): WHAT IS '
                    'THIS SESSION DOING RIGHT NOW. `phase` + `message` are a standing INSTRUCTION '
                    '("complete the login on the screen"), so a session grinding through a page '
                    're-read and a session that had stopped looked identical ("it shoudl show what '
                    'is going on it seems nothing is moving"). The step in flight is a DIFFERENT '
                    'fact from the phase: `_note_act` is the one setter (lock-only, never touches '
                    'the page or the row — it runs on the hot path), `activity` the one reader, and '
                    '`state`/`frame_since` DEREFERENCE it rather than composing their own, so the '
                    'line moves on an unchanged frame payload too. Frame age is None before the '
                    'first frame, never a fabricated 0 that would read as "just updated".',
        "homes": ('app/modules/commcalc/live_login.py',),
        "index": ('COMMCALC-13', 'COMMCALC-24', 'COMMCALC-27', 'COMMCALC-28', 'COMMCALC-30',
                  'COMMCALC-31'),
        "locks": ('harness_terminal_status.py', 'harness_live_login_progress.py',
                  'harness_live_session_host.py', 'harness_live_input_latency.py'),
        # SNAPSHOT — written BY HAND, never blessed (§50). The lock verifies it against the real
        # import graph.
        "callers": {
            # The data_source row decorator — the ONE place a row reaches a surface, and therefore
            # the only reader of `present_auth_status`. It also owns the stamp whose order the
            # pre-login capture depends on (`live_login_start` reads the row BEFORE stamping it).
            'app/modules/commcalc/router.py': ('_ll', 'live_login'),
            # The vendor-ordering door onto the same live login (§51). It REUSES commcalc's
            # endpoints — the commcalc ROUTER's `live_login_start` for the start, and `get_session` here to
            # read state — so it inherits this answer rather than presenting its own. It may never grow a second
            # presenter: a status is one fact, whichever page asks for it.
            'app/modules/supply/router.py': ('live_login',),
        },
    },
}

def keys() -> tuple:
    """Every fact in the graph, in a stable order."""
    return tuple(sorted(FACTS))


def fact(key: str) -> dict:
    """The one entry for `key`. KeyError is deliberate — a typo must not read as "no edges"."""
    return FACTS[key]


def homes_for(key: str) -> tuple:
    return tuple(FACTS[key]["homes"])


def callers_for(key: str) -> tuple:
    return tuple(sorted(FACTS[key]["callers"]))


def bound_names(key: str, path: str) -> tuple:
    """The name(s) `path` binds a home of `key` to — what an anti-un-wiring check looks for."""
    return tuple(FACTS[key]["callers"].get(path, ()))


def home_under_app(key: str, index: int = 0) -> str:
    """A home's path relative to `backend/app` — the shape the `harness_*_lock.py` walks use.

    The locks used to each carry their own `HOME = "modules/commcalc/line_class.py"` literal. They
    dereference this instead, so the ruling lives in one place and `--bless` cannot leave a lock
    pointing at a file that has moved.
    """
    return homes_for(key)[index].split("app/", 1)[-1]


def locks_for(key: str) -> tuple:
    return tuple(FACTS[key]["locks"])


def index_refs_for(key: str) -> tuple:
    return tuple(FACTS[key]["index"])


def all_homes() -> dict:
    """home path -> the fact key it answers. A home answers exactly one question by construction."""
    out: dict = {}
    for k in keys():
        for h in FACTS[k]["homes"]:
            out.setdefault(h, k)
    return out


def all_locks() -> tuple:
    return tuple(sorted({l for k in keys() for l in FACTS[k]["locks"]}))


def facts_touching(path: str) -> tuple:
    """Every fact `path` participates in, whether as a home or as a caller."""
    return tuple(k for k in keys()
                 if path in FACTS[k]["homes"] or path in FACTS[k]["callers"])


def connected(path: str) -> dict:
    """THE QUESTION THIS FILE EXISTS TO ANSWER: "I am changing `path` — what else is wired to it?"

    Returns {fact_key: {"role", "question", "homes", "siblings", "index", "locks"}}.
    `siblings` are the OTHER files wired to the same fact: for a home, every caller; for a caller,
    the homes plus every other caller. Those are the pieces a change has to be true for.
    """
    out: dict = {}
    for k in facts_touching(path):
        f = FACTS[k]
        is_home = path in f["homes"]
        sibs = set(f["callers"]) if is_home else set(f["homes"]) | set(f["callers"])
        sibs.discard(path)
        out[k] = {
            "role": "home" if is_home else "caller",
            "question": f["question"],
            "homes": tuple(f["homes"]),
            "siblings": tuple(sorted(sibs)),
            "index": tuple(f["index"]),
            "locks": tuple(f["locks"]),
        }
    return out


def impact_report(paths) -> str:
    """`connected()` for a changed-file list, rendered for a human reading a pull request."""
    lines: list = []
    for p in sorted(set(paths)):
        conn = connected(p)
        if not conn:
            continue
        lines.append(f"{p}")
        for k, c in sorted(conn.items()):
            lines.append(f"  [{c['role']}] {k} — {c['question']}")
            lines.append(f"      home(s): {', '.join(c['homes'])}")
            lines.append(f"      index:   {', '.join(c['index'])}   locks: {', '.join(c['locks'])}")
            if c["siblings"]:
                lines.append(f"      also answers this question ({len(c['siblings'])}):")
                for s in c["siblings"]:
                    lines.append(f"        - {s}")
        lines.append("")
    if not lines:
        return "No file in this change is wired to a fact in the module graph."
    return "\n".join(lines).rstrip()
