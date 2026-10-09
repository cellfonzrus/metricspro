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
                    'distributor CLAIM it reimbursed, and may the two be compared at all?',
        "homes": ('app/modules/commcalc/device_reimb_recon.py',),
        "index": ('19.53',),
        "locks": ('harness_device_reimb_recon.py',),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            'app/modules/commcalc/router.py': ('_drr',),
        },
    },
    'store_identity': {
        "question": 'Which STORE is this string? Asked by the GP report, the commission-leg '
                    'trend/breakout, the residual report, flags 7/8, the closing store-day match, '
                    'the P&L store grain and the MA account index. The answer is the org\'s own '
                    'declared spellings; AMBIGUITY RESOLVES TO NOTHING, and money nothing can '
                    'place is reported on its own row rather than dropped.',
        "homes": ('app/modules/account/store_identity.py',),
        "index": ('63',),
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
            # `_leg_store_index` + the two commission-leg endpoints' row keys.
            'app/modules/commcalc/router.py': ('_store_identity',),
        },
    },
    'carrier_dollar_component': {
        "question": 'Is this carrier dollar a commission, a spiff, a residual or a reimbursement — '
                    'and is that the ORG\'S OWN declaration or a guess the platform made? Which '
                    'P&L LINE and which GROSS-PROFIT COLUMN that component lands on is the same '
                    'fact, read twice, and lives here too (`component_line` / `gp_column`).',
        "homes": ('app/modules/commcalc/carrier_dollar_class.py',),
        "index": ('58',),
        "locks": ('harness_carrier_dollar_class.py',
                  'harness_gp_carrier_class_dereference.py'),
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
            'app/modules/commcalc/portout_flags.py': ('_reg',),
            'app/modules/commcalc/router.py': ('flag_registry',),
            'app/modules/commcalc/sale_installment_engine.py': ('_reg',),
            'app/modules/commcalc/sales_recon.py': ('_reg',),
            'app/modules/commcalc/void_watchdog.py': ('_reg',),
            'app/modules/commcalc/watchdog_router.py': ('_reg',),
            'app/modules/payables/engine.py': ('_reg',),
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
            'app/modules/commcalc/router.py': ('_MA_LEG_COMPONENTS', '_msp', '_msp_gp', '_msp_ma', '_rs_gp', 'load_store_index'),
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
        "index": ('6',),
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
            'app/modules/closing/ops_chargebacks.py': ('_cscope',),
            'app/modules/closing/router.py': ('_cscope', '_cscope_opts'),
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
            'app/modules/commcalc/router.py': ('_pd',),
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
            # §60 — the spiff-impact endpoint reads each store's revenue / net income off the STORED
            # per-store P&L through `analysis.pl_totals`, this module's one home for those totals.
            'app/modules/commcalc/router.py': ('_an',),
        },
    },
    'complete_feed_read': {
        "question": 'Have I read ALL of this feed, or only the first N rows somebody typed?',
        "homes": ('app/modules/core/feed_read.py',),
        "index": ('19.48',),
        "locks": ('harness_pay_feed_balance.py', 'harness_installment_month_anchor.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            # §19.51 — raw_mi is the feed §19.48's own docstring named as the next ceiling risk
            # (46,047 rows in September, growing ~4,000 a month); its private page loop is retired.
            'app/modules/commcalc/installment_engine.py': ('_feed_read',),
            'app/modules/commcalc/router.py': ('_feed_read',),
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
            'app/modules/commcalc/router.py': ('_payment_category', '_pcat'),
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
            'app/modules/commcalc/router.py': ('_expfx',),
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
