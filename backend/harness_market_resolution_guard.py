"""Guard: every store→market resolution site maps to THE canonical resolver — or is explicitly
pinned here with its documented classification.

WHY (owner directive 2026-09-03: "1115 liberty ave which has been assigned LI as the market does
not show up under any filter as in the rep incentive report but shows in the daily target report,
again this is an issue with index and using the same data everywhere, why can this not be fixed
once for all"). The codebase carries TWO market vocabularies (storeops.stores.market and
commcalc.store_mapping.market) plus commcalc.store_aliases, and they are KNOWN to diverge. Every
time a report resolved market through ONE vocabulary it created this bug class: 2026-08 market
grants that bound nothing, the 2026-09-02 P&L filter bug, the 2026-09-02 DM pickup bug, and
2026-09-03's 1115-Liberty/Rep-Incentive bug. The cure is ONE resolver —
`app.core.scope.build_store_market_lookup` / `store_market_resolver` / `market_by_code` over the
canonical union index — and THIS GUARD is the "once for all": it scans the backend for any query
that reads a market column off a store vocabulary table and FAILS the build unless the site is
either canonical or pinned below with a reviewed classification. A new sibling resolver cannot
merge green.

Classifications (the pinned inventory — docs/SYSTEM_DATA_FLOW_INDEX.md §13 carries the same table):
  CANONICAL   — delegates to core.scope (store_market_resolver / market_by_code / market_index).
  OVERLAY     — reads one vocabulary for its own fields, then fills BLANK markets from the
                canonical map (set markets never overwritten).
  EDITOR      — the vocabulary's own read/write surface (store setup, store-matching, market
                normalizer). It IS the data being edited; resolution does not apply.
  AUDITOR     — deliberately reads BOTH vocabularies raw to REPORT their divergence.
  GRANT       — app_users.market permission grants; resolution runs through core.scope's grant
                machinery (login_grant_breakdown / resolve_market_grant), not through this read.
  ROSTER      — storeops roster surface where market is carried for display next to a store the
                caller already selected (no market filtering/bucketing happens on this read).
  PAY-ENGINE  — commission plan attachment (`_read_store_market`): MONEY path, deliberately
                store_mapping-only until the owner approves the union there (changing it changes
                payouts; commission-agent + approval required). The plan-assignment audit mirrors
                the engine ON PURPOSE and must keep matching it.
  ATTRIBUTION — joins that ATTRIBUTE money to a store via store_mapping structure (salesforce_id /
                street-number); market on those rows is enriched via OVERLAY where it feeds a
                filter, but the join itself is structural to store_mapping.
  STORED      — asset's documented stored-market design: `_store_mapping_market_index` builds
                BOTH-vocabulary candidates and EXCLUDES+reports conflicting keys (NIT-2), then
                `_backfill_market` WRITES the market onto ledger rows; filters compare the stored
                value. Same union, different (persisted, conflict-audited) mechanism.

To add a new site: use core.scope (preferred — then pin it CANONICAL/OVERLAY), or justify one of
the classifications above IN THE CODE COMMENT and pin it here in the same PR.

WHERE IT RUNS (2026-10-10): a step of `.github/workflows/carrier-vocab-guard.yml` — the lock-harness
job, beside the org-tree walk lock (§48.7) whose refactor this guard failed to follow. Before that it
ran in NO workflow at all and had drifted red for weeks: a guard nobody runs is not a gate. The
harness is named in that workflow's `pull_request.paths` too, because a lock behind an allowlist that
does not name its own subject skips silently (the §13e lesson).

No DB, no network, no app imports.  Run:  cd backend && python3 harness_market_resolution_guard.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULES = os.path.join(_HERE, "app", "modules")
_CORE_SCOPE = os.path.join(_HERE, "app", "core", "scope.py")

# A "market vocabulary read" = a .table("stores"|"store_mapping") chain whose select list includes
# a market column. Window is generous enough to span multi-line chains.
_TABLE_RE = re.compile(r"\.table\(\s*['\"](stores|store_mapping)['\"]\s*\)")
_WINDOW = 400

# ── THE SECOND AXIS: a read through a LOCAL HELPER (2026-10-10) ──────────────────────────────────
# `_TABLE_RE` + a literal `.select("…market…")` only sees a read spelled inline. A module that wraps
# its reads in a helper — `def _read(table, cols): return c.table(table).select(cols)…` — passes BOTH
# the table and the columns as VARIABLES, so the chain carries no literal select list and the read is
# INVISIBLE to the first axis. That is not hypothetical: `storeops.router.org_chain_inputs` reads
# `stores(store_code,org_unit_id,market)` exactly that way, and when the org-tree walk was factored
# into `storeops.org_chain` (§48.7) the market read MOVED there — out of `_dm_for_store` /
# `_managers_above_dm`, whose pins then read as merely "stale" while the live read went unguarded.
# A guard that loses sight of a read when the read moves behind a helper is not a guard, so the
# wrapper-call form is scanned too: a call whose FIRST argument is a store-vocabulary table literal
# and whose SECOND is a column list naming market. Same pinning rule, same inventory.
_INDIRECT_RE = re.compile(
    r"""\(\s*['\"](stores|store_mapping)['\"]\s*,\s*['\"]([^'\"]*\bmarket\b[^'\"]*)['\"]""")

# ── THE PINNED INVENTORY ─────────────────────────────────────────────────────────────────────────
# {relative file: {enclosing function: classification}}. A site not listed here fails the build.
PINNED = {
    "core/scope.py": {"market_index": "CANONICAL"},   # the union index's own source reads
    "commcalc/router.py": {
        "store_unmatched": "AUDITOR",        # store-matching audit of the mapping itself
        "_compute_gp": "OVERLAY",
        "commission_trend": "ATTRIBUTION",   # store_mapping code join; market via _trend_market_by_code
        "_leg_store_index": "OVERLAY",
        "_store_maps": "AUDITOR",            # /store-resolution report: reads BOTH vocabularies raw
        "get_targets_summary": "OVERLAY",
        "_prod_store_maps": "ATTRIBUTION",   # code join; market delegated to _store_market_resolver
        "_ir_store_resolver": "ATTRIBUTION", # salesforce/street-num residual-leg join
    },
    "commcalc/commission_engine.py": {"_read_store_market": "PAY-ENGINE"},
    "commcalc/agency.py": {"store_candidates": "ROSTER"},   # SUB-org roster for agency links
    "closing/router.py": {
        "closing_submissions": "OVERLAY", "closing_stores": "OVERLAY", "closing_rollup": "OVERLAY",
        "_closing_summary_org_ctx": "OVERLAY", "closing_recon": "OVERLAY",
        "get_missed_dm_verifies": "OVERLAY", "envelope_report": "OVERLAY",
        "closing_pickups": "OVERLAY", "_cash_position_core": "OVERLAY",
        # Management Review's close-attempt log + the five-stage accountability chain: both read the
        # storeops roster for market and pass it through `_overlay_canonical_market` (which IS
        # core.scope.market_by_code) before any market filter or display — the closing family's
        # standard OVERLAY, not a fourth spelling of "which market".
        "closing_attempts": "OVERLAY", "accountability_chain": "OVERLAY",
        "_billpay_position_core": "OVERLAY", "billpay_pickups": "OVERLAY",
        "cash_recon_management": "OVERLAY", "deposit_accountability_board": "OVERLAY",
    },
    "closing/ops_chargebacks.py": {"_dm_for_stores_batch": "OVERLAY"},
    "storeops/router.py": {
        "timeclock_stores": "ROSTER", "_sync_store_mapping_update": "EDITOR",
        # The org-tree walk's ONE set of inputs (index §48.7). `_dm_for_store` /
        # `_managers_above_dm` were pinned here until the walk was factored into
        # `storeops.org_chain`; the market read moved WITH it, into this helper, which overlays the
        # canonical union (`core.scope.market_by_code`) onto the roster so the district-by-market
        # fallback binds a store whose market is spelled only in commcalc.store_mapping.
        "org_chain_inputs": "OVERLAY",
        "list_hours_budgets": "OVERLAY",
        "org_tree": "ROSTER", "org_build_standard": "EDITOR",
        "list_google_review_stores": "ROSTER", "my_google_reviews": "ROSTER",
        "google_reviews_dm_dashboard": "ROSTER", "google_review_store_detail": "ROSTER",
        "google_review_employee_detail": "ROSTER", "google_reviews_employee_summary": "ROSTER",
    },
    "storeops/google_reviews.py": {"sweep_org": "ROSTER"},
    "storevisit/router.py": {"stores_in_market": "OVERLAY"},
    "pos/router.py": {"tax_code_markets": "OVERLAY", "tax_code_store_grid": "OVERLAY"},
    "payables/router.py": {"payables_filter_options": "OVERLAY"},
    "core/import_health.py": {"_p_unmapped_stores": "AUDITOR"},
    "core/onboarding.py": {"_cov_pos_tax_rate": "OVERLAY"},
    "core/router.py": {"filter_options": "OVERLAY"},
    "account/residual_subs.py": {"compute": "OVERLAY"},
    "asset/router.py": {"_store_mapping_market_index": "STORED", "_registry_stores": "OVERLAY"},
}

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def enclosing_def(src, pos):
    """Name of the nearest `def`/`async def` above pos (module level -> '<module>')."""
    best = "<module>"
    for m in re.finditer(r"^(?:async\s+)?def\s+(\w+)", src[:pos], re.M):
        best = m.group(1)
    return best


def scan_src(src):
    """Yield (function, table, line) for every market-column read of a store vocabulary table in
    `src` — PURE (text in, sites out), so the negative controls can drive it on fixtures."""
    hits = []
    for m in _TABLE_RE.finditer(src):                      # axis 1: inline .table().select("…")
        window = src[m.start(): m.start() + _WINDOW]
        sel = re.search(r"\.select\(\s*['\"]([^'\"]*)['\"]", window)
        if not sel or "market" not in sel.group(1):
            continue
        hits.append((enclosing_def(src, m.start()), m.group(1),
                     src.count("\n", 0, m.start()) + 1))
    for m in _INDIRECT_RE.finditer(src):                   # axis 2: helper("table", "cols")
        hits.append((enclosing_def(src, m.start()), m.group(1),
                     src.count("\n", 0, m.start()) + 1))
    return hits


def scan_file(path):
    """`scan_src` over a file's text."""
    return scan_src(open(path, encoding="utf-8").read())


def stale_pins(pinned, seen):
    """Pins whose function no longer reads a market vocabulary — PURE, so a control can drive it."""
    out = []
    for rel, funcs in pinned.items():
        for fn in funcs:
            if fn not in seen.get(rel, set()):
                out.append(f"{rel}: pinned {fn}() no longer reads a market vocabulary — remove the pin")
    return out


# ── NEGATIVE CONTROLS ────────────────────────────────────────────────────────────────────────────
# A scan that cannot go red is not a scan. Every axis above is driven here over a FIXTURE that
# should trip it and one that should not, so an edit that quietly stops matching (a tightened regex,
# a renamed helper, a window that no longer spans the chain) fails the build instead of passing an
# empty inventory. Each control is verified RED by breaking the matcher and watching THAT control —
# and only that one — fail. The trap being guarded against: a scan whose pattern can never match
# will report "0 unpinned sites" forever and read exactly like a clean codebase.
_C_INLINE = """
def reads_market():
    rows = client.schema("storeops").table("stores").select(
        "store_code,address,market").eq("org_id", org_id).execute().data
"""
_C_NO_MARKET = """
def reads_no_market():
    rows = client.schema("storeops").table("stores").select(
        "store_code,address").eq("org_id", org_id).execute().data
"""
_C_HELPER = """
def outer_reader(org_id):
    def _read(table, cols):
        return c.table(table).select(cols).eq("org_id", org_id).execute().data or []
    stores = _read("stores", "store_code,org_unit_id,market")
"""
_C_HELPER_MAPPING = """
def outer_mapping_reader(org_id):
    rows = _read("store_mapping", "store_code,market")
"""
_C_HELPER_OTHER = """
def reads_a_grant(org_id):
    rows = _read("app_users", "role,employee_id,market,store_code")
"""


def controls():
    def funcs(src):
        return {f for f, _t, _l in scan_src(src)}

    ok("reads_market" in funcs(_C_INLINE),
       "CONTROL C1: an inline .table(\"stores\").select(\"…market…\") read is NOT detected")
    ok(funcs(_C_NO_MARKET) == set(),
       "CONTROL C2: a store read WITHOUT a market column is reported as a market read")
    ok("outer_reader" in funcs(_C_HELPER),
       "CONTROL C3: a read through a local helper — _read(\"stores\", \"…market…\") — is NOT "
       "detected (the org_chain_inputs shape; this axis is why the 2026-10-10 stale pins were "
       "hiding a live read)")
    ok("_read" not in funcs(_C_HELPER),
       "CONTROL C4: a helper read is attributed to the NESTED def rather than the enclosing "
       "function — a pin on the real function would then read as permanently stale")
    ok("outer_mapping_reader" in funcs(_C_HELPER_MAPPING),
       "CONTROL C5: the second vocabulary (store_mapping) is NOT detected through a helper")
    ok(funcs(_C_HELPER_OTHER) == set(),
       "CONTROL C6: a market column on a NON-vocabulary table (a permission grant / asset ledger) "
       "is reported as a store-vocabulary read")
    ok(stale_pins({"x.py": {"gone": "OVERLAY"}}, {"x.py": {"alive"}}) != [],
       "CONTROL C7: a pin whose function no longer reads a vocabulary is NOT reported stale")
    ok(stale_pins({"x.py": {"alive": "OVERLAY"}}, {"x.py": {"alive"}}) == [],
       "CONTROL C8: a LIVE pin is reported stale (the prune would delete a real pin)")


def main():
    files = [_CORE_SCOPE]
    for root, _dirs, names in os.walk(_MODULES):
        for n in names:
            if n.endswith(".py"):
                files.append(os.path.join(root, n))
    unpinned = []
    seen = {}
    for f in files:
        rel = os.path.relpath(f, os.path.join(_HERE, "app", "modules"))
        if f == _CORE_SCOPE:
            rel = "core/scope.py"
        rel = rel.replace(os.sep, "/")
        for func, table, line in scan_file(f):
            cls = (PINNED.get(rel) or {}).get(func)
            seen.setdefault(rel, set()).add(func)
            if cls is None:
                unpinned.append(f"{rel}:{line} def {func}() reads {table}.market — not pinned")
    for site in unpinned:
        ok(False, site + "\n      → resolve through app.core.scope (store_market_resolver / "
                         "market_by_code) or pin a reviewed classification in "
                         "harness_market_resolution_guard.py (same PR).")
    ok(not unpinned, f"{len(unpinned)} unpinned market-vocabulary read(s)")

    # Stale pins (a pinned function that no longer reads a vocabulary) — prune so the pin list
    # stays an honest inventory, not an ever-growing bypass.
    stale = stale_pins(PINNED, seen)
    for s in stale:
        ok(False, s)
    ok(not stale, f"{len(stale)} stale pin(s)")

    # The canonical helpers themselves must exist with their contracted names.
    scope_src = open(_CORE_SCOPE, encoding="utf-8").read()
    for name in ("def build_store_market_lookup", "def store_market_resolver",
                 "def build_market_by_code", "def market_by_code", "def build_market_index"):
        ok(name in scope_src, f"core/scope.py lost its contracted helper: {name}")

    controls()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
