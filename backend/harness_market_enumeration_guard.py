"""Guard: every market ENUMERATION site (a `markets` / `market_options` payload key) is pinned with
a reviewed classification — and every CANONICAL site really composes through the canonical
vocabulary helpers.

WHY (owner directive 2026-09-04, verbatim: "B-1115 is under super nova and LI market under Cellfonz
R us, that has been missing from a lot of reports when market is chosen, this needs to be fixed as
a design not a band aid as this could happen to a new store also"). §13a + the sibling guard
(harness_market_resolution_guard.py) made store→market RESOLUTION canonical. The remaining half of
the bug class was ENUMERATION: each report's market DROPDOWN was fed from its own source — the rows
it happened to load, one vocabulary table, a module-local ledger aggregate — so a market recorded
on only one side (live: B-1115 "1115 Liberty Ave" carries LI ONLY on storeops.stores; no
store_mapping row, no alias) appeared in some dropdowns and silently vanished from others, and the
same will happen to any NEW store's new market. The §13c design rule:

    EVERY market option list = core.scope.canonical_markets (the union vocabulary the resolver and
    the grant machinery bind) ∪ the surface's own row stamps, composed via
    core.scope.merge_market_options / org_market_options. Sentinels ("(no market)") append after.

THIS GUARD scans app/modules (+ core/scope.py) for any dict literal carrying a `markets` /
`market_options` key and FAILS the build unless the enclosing function is pinned below. A pin of
CANONICAL additionally requires the function body to reference a canonical composition/vocabulary
helper, so the pin cannot rot into a bypass. A new divergent enumeration cannot merge green.

Classifications:
  CANONICAL  — the option list is composed from the canonical vocabulary (org_market_options /
               merge_market_options / canonical_markets / market_index / a canonical resolver's
               `all_markets`, or a local delegate of those). Body reference verified.
  FEEDER     — a PURE options builder over rows whose CALLING endpoint composes the canonical
               union before shipping (the endpoint is pinned CANONICAL in the same table).
  ECHO       — echoes the caller's APPLIED market selection (or an empty error payload), not an
               option source.
  DISPLAY    — report CONTENT keyed/aggregated by market (a rollup row, a summary count), not an
               option source.
  GRANT      — app_users market GRANTS (DM roster / people directory); the option universe for
               grants is /core/markets (CANONICAL); these enumerate what is granted.
  PAY-MIRROR — commission plan-assignment audit: mirrors the PAY-ENGINE's deliberately
               store_mapping-only view (see harness_market_resolution_guard PAY-ENGINE pin).
  STORED     — asset's conflict-audited stored-market machinery (NIT-2), not an option payload.

To add a new site: compose through core.scope (then pin CANONICAL), or justify a classification in
the code comment and pin it here in the same PR.

No DB, no network, no app imports (source scan only).
Run:  cd backend && python3 harness_market_enumeration_guard.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULES = os.path.join(_HERE, "app", "modules")
_CORE_SCOPE = os.path.join(_HERE, "app", "core", "scope.py")

_KEY_RE = re.compile(r"""["'](markets|market_options)["']\s*:""")

# Body tokens that prove a CANONICAL site composes via the canonical vocabulary. `all_markets` /
# `market_resolver` are the house convention for the canonical resolver's (resolve, markets) pair;
# local delegates (_org_markets, _collect_markets, _trend_markets) themselves resolve canonically
# and are guarded by their own pins.
_CANONICAL_TOKENS = ("org_market_options", "merge_market_options", "canonical_markets",
                     "market_index", "market_resolver", "market_by_code", "all_markets",
                     "_org_markets", "_collect_markets", "_trend_markets")

# ── THE PINNED INVENTORY ─────────────────────────────────────────────────────────────────────────
PINNED = {
    "core/scope.py": {
        "build_market_index": "CANONICAL",      # the vocabulary's own constructor
    },
    "account/residual_subs.py": {"compute": "CANONICAL"},
    "asset/router.py": {
        "_filter_options_legacy_scan": "FEEDER",   # consumed by get_filter_options (CANONICAL)
        "_filter_options_via_rpc": "FEEDER",       # consumed by get_filter_options (CANONICAL)
        "_store_mapping_market_index": "STORED",
        "get_filter_options": "CANONICAL",
    },
    # Card Settlement Recon (owner 2026-09-04, migs 960/961): composes org_market_options over the
    # canonical vocabulary ∪ its own rows' stamps — including SETTLEMENT-ONLY store-days, which have
    # no roster row at all, so an option list built from the loaded roster would have hidden them.
    "closing/router.py": {"external_credit_recon": "CANONICAL"},
    "commcalc/custom_report.py": {"option_values": "FEEDER"},   # custom_report_run unions canonical
    "commcalc/device_cost_recon.py": {"filter_options": "FEEDER"},
    "commcalc/imei_rebate_report.py": {"filter_options": "FEEDER"},
    "commcalc/ma_handset_cogs.py": {"filter_options": "FEEDER"},
    # Processor Daily Debits & Credits (owner 2026-09-04): the day × transaction-type ledger
    # composes its market dropdown as canonical vocabulary ∪ the markets its own cells
    # carry (stamped from core.scope.market_by_code), so a one-vocabulary market (B-1115/LI)
    # is always offered AND always selectable.
    "commcalc/processor_ledger.py": {"assemble": "CANONICAL"},
    "commcalc/router.py": {
        "_commission_mtd_result": "ECHO",          # scope echo of the applied market selection
        "_dcr_empty": "ECHO",                      # empty error payload
        "_exec_mtd": "CANONICAL",
        "_prod_gather": "CANONICAL",
        "accessory_flags": "CANONICAL",
        "activation_counts": "DISPLAY",            # by-market rollup rows (report content)
        "atu_opportunity_report": "CANONICAL",
        "commission_plan_assignment_audit": "PAY-MIRROR",
        "commission_plan_roster": "GRANT",
        "commission_trend": "CANONICAL",
        "custom_report_run": "CANONICAL",
        "device_cost_recon_endpoint": "CANONICAL",
        "expenses_trend": "CANONICAL",
        "get_productivity": "ECHO",                # error-path empty filters payload
        "get_productivity_rankings": "ECHO",
        "get_productivity_review": "ECHO",
        "get_targets_summary": "CANONICAL",
        "gp_trend": "CANONICAL",
        "imei_rebate_report_endpoint": "CANONICAL",
        "list_markets": "CANONICAL",
        "ma_handset_cogs_endpoint": "CANONICAL",
        "sales_comparison": "ECHO",                # echoes the applied selection
        "sales_report": "CANONICAL",
        "tax_collected": "CANONICAL",
    },
    "core/router.py": {
        "filter_options": "CANONICAL",
        "grant_universe": "CANONICAL",
    },
    "payables/router.py": {"payables_filter_options": "CANONICAL"},
    "pos/router.py": {"tax_code_markets": "CANONICAL"},
    "storeops/router.py": {
        "list_markets": "CANONICAL",
        "org_build_standard": "DISPLAY",           # build summary count, not options
        "schedule_hours_trend": "ECHO",            # scope echo
    },
    "storeops/target_attribution.py": {
        "attribute_rows_to_dms": "GRANT",
        "dm_roster_from_app_users": "GRANT",
    },
}

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def functions_with_key(path):
    """{top_level_function_name: body} for every TOP-LEVEL function that (transitively) contains a
    markets/market_options dict key. Attribution is deliberately to the module-level def — an inner
    helper (`def add`, `def _opts`) belongs to, and is reviewed with, the function that owns it."""
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")
    # TOP-LEVEL def start lines only (column 0)
    defs = []
    for i, l in enumerate(lines):
        m = re.match(r"(?:async\s+)?def\s+(\w+)", l)
        if m:
            defs.append((i, m.group(1)))
    out = {}
    for i, l in enumerate(lines):
        if not _KEY_RE.search(l):
            continue
        cand = [(j, name) for (j, name) in defs if j <= i]
        name = cand[-1][1] if cand else "<module>"
        if name in out:
            continue
        if cand:
            j, _ = cand[-1]
            end = next((k for (k, _n) in defs if k > j), len(lines))
            out[name] = "\n".join(lines[j:end])
        else:
            out[name] = src
    return out


def main():
    files = [_CORE_SCOPE]
    for root, _dirs, names in os.walk(_MODULES):
        for n in names:
            if n.endswith(".py"):
                files.append(os.path.join(root, n))

    seen = {}
    unpinned = []
    for f in files:
        rel = ("core/scope.py" if f == _CORE_SCOPE
               else os.path.relpath(f, _MODULES).replace(os.sep, "/"))
        funcs = functions_with_key(f)
        if not funcs:
            continue
        seen[rel] = funcs
        for fn, body in funcs.items():
            cls = (PINNED.get(rel) or {}).get(fn)
            if cls is None:
                unpinned.append(f"{rel}: def {fn}() ships a markets/market_options key — not pinned")
            elif cls == "CANONICAL":
                ok(any(t in body for t in _CANONICAL_TOKENS),
                   f"{rel}: {fn}() is pinned CANONICAL but references no canonical vocabulary "
                   f"helper ({', '.join(_CANONICAL_TOKENS[:4])}, …)")
    for site in unpinned:
        ok(False, site + "\n      → compose via core.scope.org_market_options/merge_market_options "
                         "(pin CANONICAL) or pin a reviewed classification in "
                         "harness_market_enumeration_guard.py (same PR).")
    ok(not unpinned, f"{len(unpinned)} unpinned market-enumeration site(s)")

    stale = []
    for rel, funcs in PINNED.items():
        for fn in funcs:
            if fn not in (seen.get(rel) or {}):
                stale.append(f"{rel}: pinned {fn}() no longer ships a markets key — remove the pin")
    for s in stale:
        ok(False, s)
    ok(not stale, f"{len(stale)} stale pin(s)")

    # The canonical composition helpers must exist with their contracted names.
    scope_src = open(_CORE_SCOPE, encoding="utf-8").read()
    for name in ("def merge_market_options", "def org_market_options", "def canonical_markets"):
        ok(name in scope_src, f"core/scope.py lost its contracted helper: {name}")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
