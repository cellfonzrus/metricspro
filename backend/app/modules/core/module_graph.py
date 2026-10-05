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
            'app/modules/closing/router.py': ('_lcls',),
            'app/modules/commcalc/calculator.py': ('_lc',),
            'app/modules/commcalc/commission_engine.py': ('_lc',),
            'app/modules/commcalc/ma_recon.py': ('_lc',),
            'app/modules/commcalc/payout_audience.py': ('_lc',),
            'app/modules/commcalc/plan_options.py': ('_lc',),
            'app/modules/commcalc/router.py': ('_lc',),
            'app/modules/commcalc/sales_comparison.py': ('_lc',),
            'app/modules/commcalc/zero_sales.py': ('_lc',),
            'app/modules/core/plan_sources.py': ('_lc',),
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
            'app/modules/commcalc/router.py': ('commission_ledger',),
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
            'app/modules/commcalc/commission_engine.py': ('_ct',),
            'app/modules/commcalc/dlar_sweep.py': ('_ct',),
            'app/modules/commcalc/expenses_effective.py': ('_ct',),
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
