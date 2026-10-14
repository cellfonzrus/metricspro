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
    # ── §CORE-07 (owner report 2026-10-14) — which PERSON a login belongs to ─────────────────────
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
                    'their own and are PINNED with reasons in the lock, a count that may only shrink.',
        "homes": ('app/modules/commcalc/portal_report_pull.py',),
        "index": ('COMMCALC-07',),
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
            # the live session's dispatch and the scheduled cron both ask the registry
            'app/modules/commcalc/router.py': ('_prp',),
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
        "index": ('COMMCALC-03',),
        "locks": ('harness_prospect_eval.py', 'harness_upload_trace_coverage.py'),
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
        "callers": {
            'app/modules/commcalc/router.py': ('_pev',),
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
        },
    },
    'device_price_band': {
        "question": 'What price band did the CUSTOMER pay for this device, which model was it, and is '
                    'this rep low on accessory $ per box AND on port-ins at the same time?',
        "homes": ('app/modules/commcalc/product_mix.py',),
        "index": ('62',),
        "locks": ('harness_product_mix.py',),
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
        },
    },
    'accessory_target_allocation': {
        "question": 'Given ONE company accessory sales goal, what target should each store carry, '
                    'derived from its own accessories-per-box history against the boxes it sells?',
        "homes": ('app/modules/commcalc/accessory_target_plan.py',),
        "index": ('61',),
        "locks": ('harness_accessory_target_plan.py',),
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
                  'harness_device_line_reimbursement.py'),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            'app/modules/commcalc/router.py': ('_drr',),
            # The COST side (§69) reads this fact's own verdict reader to place a handset's cost on
            # the activating store. One reconciliation, two readers, never two derivations.
            'app/modules/account/device_cogs.py': ('_drr',),
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
            'app/modules/storeops/payroll_identity.py': ('fold_accents',),
            # The remaining five identity folds, found by the lock's second axis and wired in the
            # same PR rather than left as "the ones we did not get to". Each folds a KEY nothing
            # ever shows a human — a store code, a carrier label, a clock-in evidence key, a role
            # name — so routing it through the one fold gains matches and moves none.
            'app/modules/closing/router.py': ('_sid_fold',),
            'app/modules/commcalc/b2b_sweep.py': ('_sid_fold',),
            'app/modules/commcalc/vidapay_sweep.py': ('_sid_fold',),
            'app/modules/storeops/clockin_evidence.py': ('_sid_fold',),
            'app/modules/storeops/clock_variance.py': ('_sid_fold',),
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
            'app/modules/commcalc/router.py': ('_cdc', '_cdc_gp', '_cdc_leg'),
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
    'feed_lineage': {
        "question": "Which table is the LIVE feed for this item, and which column means 'arrived'?",
        "homes": ('app/modules/commcalc/data_lineage_registry.py',),
        "index": ('19.43',),
        "locks": ('harness_data_lineage_guard.py', 'harness_feed_watchdog.py', 'harness_ingest_freshness.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/auto_calc.py': ('_lineage',),
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
        "question": 'What MA/VidaPay income does a report show, on what timing basis, at what store grain?',
        "homes": ('app/modules/account/ma_store_pnl.py', 'app/modules/account/residual_subs.py'),
        "index": ('4a', '4b'),
        "locks": ('harness_ma_income_one_home_guard.py', 'harness_mi_residual_store_grain.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/coa.py': ('_MA_COMPONENTS', '_msp', '_rs', '_rs_mi', '_rs_mi_idx'),
            'app/modules/account/ledger_pnl.py': ('_msp',),
            'app/modules/account/router.py': ('_msp', 'residual_subs'),
            'app/modules/account/statement_engine.py': ('_msp',),
            'app/modules/commcalc/commission_received.py': ('ma_commission_components',),
            'app/modules/commcalc/comp_trend.py': ('_msp', '_rs'),
            # §COMMCALC-03 added `_rsub` — `default_ma_pnl_config()` for the takeover evaluation,
            # so a prospect's MA TX rows book under the SAME resolved config the live P&L books under.
            'app/modules/commcalc/router.py': ('_MA_LEG_COMPONENTS', '_msp', '_msp_gp', '_msp_ma', '_rs_gp', '_rsub', 'load_store_index'),
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
            'app/modules/closing/router.py': ('_er', 'envelope_report_mod'),
        },
    },
    'line_class': {
        "question": 'What kind of line is this sales row, and does it count as a new activation?',
        "homes": ('app/modules/commcalc/line_class.py',),
        "index": ('6', 'COMMCALC-03.23'),
        "locks": ('harness_line_class_lock.py', 'harness_activation_event_lock.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/asset/router.py': ('_lc',),
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
    'commission_ledger_identity': {
        "question": 'Which ledger row is this, and which period does it book into?',
        "homes": ('app/modules/commcalc/commission_ledger.py',),
        "index": ('4b',),
        "locks": ('harness_ledger_identity_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/coa.py': ('_cl',),
            'app/modules/account/ledger_pnl.py': ('_cl',),
            'app/modules/account/ma_store_pnl.py': ('month_leg_of',),
            'app/modules/commcalc/column_mapping.py': ('commission_ledger',),
            'app/modules/commcalc/commission_statement.py': ('CATEGORIES', 'CATEGORY_LABELS'),
            'app/modules/commcalc/ledger_batch.py': ('CL',),
            'app/modules/commcalc/ledger_ma_sync.py': ('commission_ledger',),
            'app/modules/commcalc/onboarding_intake.py': ('CL',),
            # `_month_token` is §60's binding of `parse_payment_month` — "does this label SAY a
            # month", which is exactly that function's own question (its docstring is explicit that
            # the month-of-life LEG question belongs to `month_leg_of` instead).
            'app/modules/commcalc/router.py': ('_month_token', 'commission_ledger'),
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
            # §COMMCALC-03 added `_ap` — a prospect's uploaded rows may be stamped in either
            # spelling, so the read asks this home for both rather than matching one.
            'app/modules/commcalc/router.py': ('_ap', '_pd'),
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
            # The visit-priority dropdown offers the axis as assignable deliverables.
            'app/modules/storevisit/visit_plan_router.py': ('_te',),
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
