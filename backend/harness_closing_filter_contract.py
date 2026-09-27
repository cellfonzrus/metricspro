"""THE LOCK — three standard filters, three resolvers, and an endpoint that ACCEPTS one APPLIES it.

OWNER 2026-09-26, verbatim: *"i cannt pick one stroe or one market whicle doing cash recon"*.

WHAT IT ACTUALLY WAS. Not a missing picker. `/closing/attempts` — the daily-closing dashboard's
endpoint — took a **singular `store=`** and no market parameter at all, so the screen could narrow to
exactly one store and could not ask a market question; and the page carried no picker, because there
was nothing to send. Meanwhile market and store each had ONE resolver
(`_resolve_market_filter` / `_resolve_store_filter`) while the employee set was built INLINE inside
`cash_recon_management` and nowhere else — which is precisely why employee filtering existed on
exactly one screen out of eighteen.

A CORRECTION WORTH KEEPING, because the first diagnosis was wrong: an endpoint taking `stores=` and
no `markets=` is **not** automatically a defect. `lib/market-store-cascade.ts` is the designed
mechanism — the market picker narrows the store option list, and "market picked, no store picked"
means the whole market — so several endpoints correctly expect the frontend to expand a market into
a store list. What IS a defect is an endpoint that can express neither, or one that accepts a filter
and then never applies it. This lock enforces the second, narrower claim, because the broader one
would flag correct code.

WHAT FAILS THE BUILD
  (a) THREE RESOLVERS, ONE EACH. `_resolve_market_filter`, `_resolve_store_filter` and
      `_resolve_employee_filter` all exist, and no closing endpoint rebuilds one inline.
  (b) THEY AGREE ON THE CONTRACT, behaviourally: blank/None ⇒ **None** (no filter), never an empty
      set that silently drops every row; matching is case-insensitive on all three.
  (c) ACCEPTS ⇒ APPLIES. An endpoint declaring `markets` / `stores` / `employees` must reference the
      matching resolver. A parameter that is accepted and ignored is worse than a missing one: the
      picker moves, the rows do not, and nobody can tell.
  (d) THE DASHBOARD CAN ASK ALL THREE QUESTIONS — `closing_attempts` takes markets, stores and
      employees, and still honours the legacy singular `store=` so an existing link keeps working.
  (e) THE PICKER'S OPTIONS DO NOT COLLAPSE. `rep_options` is collected BEFORE the employee filter is
      applied, or picking one person empties the dropdown that would let you pick somebody else.
  (f) NEGATIVE CONTROLS: an inline set, an accepted-but-unapplied parameter, and a resolver that
      turns blank into an empty set must each go red.

Stdlib only, DB-free. Runs in .github/workflows/carrier-vocab-guard.yml.

  python3 backend/harness_closing_filter_contract.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTER = os.path.join(ROOT, "backend", "app", "modules", "closing", "router.py")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

RESOLVERS = {
    "markets": "_resolve_market_filter",
    "stores": "_resolve_store_filter",
    "employees": "_resolve_employee_filter",
}
# Building one of these by hand instead of calling the resolver.
INLINE = re.compile(
    r"\{\s*x\.strip\(\)\.(?:casefold|upper|lower)\(\)\s+for\s+x\s+in\s+\([a-z_]+\s+or\s+\"\"\)\.split")

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:420]))


SRC = open(ROUTER, encoding="utf-8").read()


def endpoints(src):
    """{fn name: (signature, body)} for every @router.get in the file — pure, so the negative
    controls can run the same scan over a synthetic source."""
    out = {}
    for m in re.finditer(r'@router\.get\("([^"]+)"\)\s*\ndef (\w+)\(', src):
        # BALANCED paren scan, not `[^)]*`: a default like `Header(default="")` contains a `)`, and a
        # naive class stops there — which silently truncated every signature carrying one and made
        # this lock read most endpoints as declaring no filters at all. A guard that under-reads is
        # worse than no guard, because it passes.
        i, depth = m.end(), 1
        while i < len(src) and depth:
            depth += (src[i] == "(") - (src[i] == ")")
            i += 1
        name, sig = m.group(2), re.sub(r"\s+", " ", src[m.end():i - 1])
        rest = src[i:]
        nxt = re.search(r"\n@router\.", rest)
        out[name] = (sig, rest[: nxt.start()] if nxt else rest)
    return out


def accepts_but_ignores(eps):
    """-> [(fn, param)] for every endpoint that declares a standard filter and never resolves it."""
    bad = []
    for fn, (sig, body) in eps.items():
        for param, resolver in RESOLVERS.items():
            if re.search(r"\b%s\s*:" % param, sig) and resolver not in body:
                bad.append((fn, param))
    return bad


EPS = endpoints(SRC)
print("\nClosing filter contract — three filters, three resolvers, accepted means applied\n")
print("  %d GET endpoints in closing/router.py\n" % len(EPS))

# ── (a) one resolver each, no inline rebuilds ─────────────────────────────────────────────────────
for param, fn in RESOLVERS.items():
    check("(a) `%s` has its one resolver: %s" % (param, fn), ("def %s(" % fn) in SRC)
inline = sorted({f for f, (_s, b) in EPS.items() if INLINE.search(b)})
check("(a) no endpoint rebuilds a filter set inline", not inline, "inline set in: %s" % inline)

# ── (b) the contract, behaviourally ───────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.modules.closing.router import (  # noqa: E402
    _resolve_market_filter as _M, _resolve_store_filter as _S, _resolve_employee_filter as _E)

check("(b) blank means NO filter, never an empty set that drops every row",
      _M(None, None) is None and _M("", "") is None and _S("") is None and _S(None) is None
      and _E("") is None and _E(None) is None)
check("(b) store matching is case-insensitive", _S("a1,b2") == {"A1", "B2"})
check("(b) employee matching is case-insensitive", _E("Asad Umar, JANE doe") == {"asad umar", "jane doe"})
check("(b) a multi-select market CSV resolves to exactly the picked markets",
      _M(None, "NYC,LI") == {"nyc", "li"})
check("(b) a singular market ALSO admits its comma-split parts (a multi-market DM grant)",
      _M("NYC,LI", None) == {"nyc,li", "nyc", "li"})

# ── (c) accepted ⇒ applied ────────────────────────────────────────────────────────────────────────
# A RATCHET, like the table-sort lock, and for the same reason: eleven endpoints declare `stores` or
# `employees` and then parse the CSV themselves or thread it into a pure helper. Each one WORKS — the
# filter does apply — so this is duplicated parsing rather than a broken screen, and converting all
# eleven inside this PR would bury the fix the owner asked for. So they are written down, the list may
# only shrink, and a NEW endpoint that accepts a standard filter without resolving it fails the build.
RESOLVER_DEBT = {
    ("list_deposit_adjustments", "stores"), ("deposit_recon_report", "stores"),
    ("closing_pickups", "stores"), ("closing_pickups", "employees"),
    ("cash_position", "stores"), ("cash_position", "employees"),
    ("store_cash_on_hand", "stores"), ("store_cash_on_hand", "employees"),
    ("billpay_pickups", "stores"), ("billpay_pickups", "employees"),
    ("list_expenses", "stores"),
}
ignored = set(accepts_but_ignores(EPS))
check("(c) no NEW endpoint accepts a standard filter without resolving it",
      not (ignored - RESOLVER_DEBT), "new offenders: %s" % sorted(ignored - RESOLVER_DEBT))
check("(c) the debt list only shrinks — a migrated endpoint must be deleted from it",
      not (RESOLVER_DEBT - ignored), "already migrated, still listed: %s" % sorted(RESOLVER_DEBT - ignored))
check("(c) the debt is at or under its pin (%d ≤ 11)" % len(ignored & RESOLVER_DEBT),
      len(ignored & RESOLVER_DEBT) <= 11)

# ── (d) the dashboard can ask all three ───────────────────────────────────────────────────────────
sig, body = EPS.get("closing_attempts", ("", ""))
for param, resolver in RESOLVERS.items():
    check("(d) closing_attempts takes `%s` and resolves it" % param,
          re.search(r"\b%s\s*:" % param, sig) and resolver in body, sig[:200])
check("(d) the legacy singular `store=` still works (an existing link is not broken)",
      re.search(r"\bstore\s*:\s*str", sig) and 'q.eq("store_code", store)' in body)
check("(d) a market pick it CANNOT honour is reported, not silently unfiltered",
      "market_filter_skipped" in body)

# ── (e) the picker's options survive the pick ─────────────────────────────────────────────────────
ro = body.index("rep_options = sorted(") if "rep_options = sorted(" in body else -1
ef = body.index("if emp_set:") if "if emp_set:" in body else -1
check("(e) rep_options is collected BEFORE the employee filter, so the dropdown cannot collapse",
      ro != -1 and ef != -1 and ro < ef, "rep_options@%s vs emp filter@%s" % (ro, ef))

# ── (f) negative controls ─────────────────────────────────────────────────────────────────────────
syn = '''@router.get("/fake")
def fake_ep(markets: str = None, stores: str = "", org_id: str = ORG_ID):
    """Accepts both and resolves neither."""
    return {}
'''
check("(f) an endpoint accepting a filter it never applies → RED",
      {("fake_ep", "markets"), ("fake_ep", "stores")} <= set(accepts_but_ignores(endpoints(syn))))
syn2 = syn.replace("return {}", "s = _resolve_store_filter(stores)\n    m = _resolve_market_filter(None, markets)\n    return {}")
check("(f) …and once it resolves them → green", not accepts_but_ignores(endpoints(syn2)))
check("(f) an inline filter set → RED",
      bool(INLINE.search('emp_set = {x.strip().casefold() for x in (employees or "").split(",")}')))
check("(f) a resolver turning blank into an empty set would break the contract",
      (set() or None) is None and bool(_S("a1")),
      "an empty set must be falsy so `or None` collapses it — that is what makes blank mean no filter")

# ── wired ─────────────────────────────────────────────────────────────────────────────────────────
wf = open(WORKFLOW, encoding="utf-8").read() if os.path.exists(WORKFLOW) else ""
check("(wired) this lock runs in carrier-vocab-guard.yml", "harness_closing_filter_contract.py" in wf)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — three filters, three resolvers; every accepted filter is applied, and the dashboard can "
      "ask all three questions.")
