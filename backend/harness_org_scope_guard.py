"""Guard: every query against a SENSITIVE money/sales table in the commcalc router is org-scoped.

WHY (audit fix #3, owner 2026-08-30). Every commcalc table ships with `open_all` row-level security
(USING(true)) — the database does NOT isolate tenants; isolation depends ENTIRELY on the application
carrying `.eq('org_id', …)` (reads/updates/deletes) or an `org_id` in the row payload (inserts/upserts)
on every query. One omitted filter silently leaks — or pays — one tenant's data into another, with
nothing to catch it. Flipping to real RLS needs a live DB to validate safely; until then THIS guard is
the safety net: it fails the build if a query on a money/sales table forgets its org scope.

Scope: the highest-severity tables — where a missing org filter is a cross-tenant MONEY or SALES leak.
An intentional cross-org read (rare) opts out with a `# org-guard-ok: <reason>` marker in the chain.

No DB, no network, no imports of the app.  Run:  cd backend && python3 harness_org_scope_guard.py
Exit 0 = all sensitive queries are org-scoped; exit 1 = a query that could leak across tenants.
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROUTER = os.path.join(_HERE, "app", "modules", "commcalc", "router.py")

# The tables where a missing org_id filter is a cross-tenant MONEY or SALES exposure. Deliberately a
# curated high-value set (not every commcalc table) so the guard is precise and false-positive-free.
SENSITIVE = {
    "rep_commissions", "mtd_commission_payout", "raw_sales", "daily_sales_feed",
    "carrier_commission", "commission_plan", "commission_plan_assignment", "raw_mi",
}
# How far past `.table('X')` to look for the `.execute(` that closes the query chain.
_WINDOW = 1400
_OPT_OUT = "org-guard-ok"

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def classify(chain):
    """How a single `.table('X') … .execute(` chain is scoped:
      'scoped'    — carries org_id (an .eq('org_id',…) filter, or an inline payload with 'org_id')
      'payload'   — insert/upsert; org_id lives in the row payload (a var the static scan can't read)
      'probe'     — a bare `.select(<cols>).limit(1)` existence check, no filter, exposes nothing
      'optout'    — an explicit '# org-guard-ok: <reason>' marker in the chain
      'violation' — an unscoped read that returns rows, or an unscoped update/delete (the leak class)."""
    if _OPT_OUT in chain:
        return "optout"
    if "org_id" in chain:
        return "scoped"
    has_write = (".insert(" in chain) or (".upsert(" in chain)
    has_mutate = (".update(" in chain) or (".delete(" in chain)
    if has_write and not has_mutate:
        return "payload"
    if ((".select(" in chain) and (".limit(1)" in chain) and not has_write and not has_mutate
            and ".eq(" not in chain and ".in_(" not in chain):
        return "probe"
    return "violation"


def _self_test():
    """The classifier must actually distinguish the leak class from the safe patterns, else a refactor
    could make the guard silently pass on everything."""
    cases = [
        (".table('raw_sales').select('*').eq('org_id', org_id).execute(", "scoped"),
        (".table('rep_commissions').insert(comms).execute(", "payload"),
        (".table('commission_plan').select('mtd_rates').limit(1).execute(", "probe"),
        (".table('raw_sales').select('*').eq('period', p).execute(", "violation"),   # unscoped READ
        (".table('rep_commissions').delete().eq('period', p).execute(", "violation"),  # unscoped DELETE
        (".table('raw_sales').delete().eq('org_id', org_id).eq('period', p).execute(", "scoped"),
        (".table('raw_sales').select('*').execute(  # org-guard-ok: distinct periods picker", "optout"),
    ]
    for chain, want in cases:
        got = classify(chain)
        ok(got == want, f"classify() self-test: expected {want}, got {got} for: {chain.strip()[:70]}")


def main():
    src = open(_ROUTER, encoding="utf-8").read()
    # Precompute line numbers by character offset.
    line_starts = [0]
    for i, ch in enumerate(src):
        if ch == "\n":
            line_starts.append(i + 1)

    def lineno(pos):
        # binary-ish scan is overkill; linear over ~35k lines is fine for a CI guard
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    table_re = re.compile(r"""\.table\(\s*["'](?P<t>[a-z_]+)["']\s*\)""")
    checked = payload_scoped = schema_probe = 0
    violations = []
    for m in table_re.finditer(src):
        t = m.group("t")
        if t not in SENSITIVE:
            continue
        # the query chain = from this .table(...) to the next .execute( within the window
        seg = src[m.start(): m.start() + _WINDOW]
        exec_at = seg.find(".execute(")
        chain = seg if exec_at == -1 else seg[: exec_at + len(".execute(")]
        checked += 1
        kind = classify(chain)
        if kind in ("scoped", "optout"):
            continue
        if kind == "payload":
            payload_scoped += 1
            continue
        if kind == "probe":
            schema_probe += 1
            continue
        # 'violation' — an unscoped READ that returns rows, or an unscoped UPDATE/DELETE — the real
        # cross-tenant leak class this guard exists to stop.
        violations.append((lineno(m.start()), t, " ".join(chain.split())[:130]))

    print(f"org-scope guard — {checked} chain(s) on sensitive tables inspected "
          f"({payload_scoped} insert/upsert payload-scoped, {schema_probe} schema-probe, "
          f"rest org-filtered)")
    for ln, t, snippet in violations:
        print(f"  ✗ router.py:{ln}  .table('{t}') read/mutate with NO org_id scope:  {snippet} …")
    ok(not violations,
       f"{len(violations)} sensitive read/mutate chain(s) missing an org_id scope (see above); "
       f"add .eq('org_id', org_id) or, for a deliberate cross-org read, a '# {_OPT_OUT}: <reason>' marker")

    # Guard's own integrity: it must actually be finding and clearing real chains, else a refactor that
    # renames .table()/.execute() would make it silently pass on everything.
    ok(checked >= 20, f"guard inspected too few chains ({checked}) — detection may be broken")

    _self_test()

    print()
    print(f"{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
