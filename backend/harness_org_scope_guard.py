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
_ISG = os.path.join(_HERE, "app", "modules", "commcalc", "ingest_store_guard.py")

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


# ── LEAK CLASS 2: WRONG-TENANT ATTRIBUTION AT INGEST (the Diversey class, 2026-09-03) ────────────
# The scan above proves every query CARRIES an org filter — it cannot see a query whose org_id VALUE
# is wrong when the row is written (the 2026-07-14 incident: a Luxelink sales export ingested under
# the house org; every chain was correctly .eq('org_id', …)-scoped, this guard was green, and six
# foreign line items still fed a phantom payout). The runtime control for that class is
# `ingest_store_guard.screen` (migration 280): before a sales-basis batch is written, every store
# string is resolved against the ORG'S OWN roster. Statically enforceable half: EVERY literal
# insert/upsert into a guarded sales-basis table must be fronted by an `_isg.screen(` call inside
# the same function — an unscreened sales ingest is a build failure, so no new code path can write
# the pay basis without asking "does this org actually have a store called that?".
INGEST_GUARDED = {"raw_sales", "daily_sales_feed"}   # ingest_store_guard.GUARDED_TABLES, pinned below


def screened_ingests(src):
    """[(pos, table, screened)] for every literal `.table('<guarded>').insert|upsert(` in src.
    `screened` = an `_isg.screen(` call appears between the enclosing function's `def` and the
    write — the promotion/upload shape, where the batch is screened before the loop that inserts it."""
    out = []
    write_re = re.compile(
        r"""\.table\(\s*["'](?P<t>%s)["']\s*\)\s*\.\s*(?:insert|upsert)\(""" %
        "|".join(sorted(INGEST_GUARDED)))
    for m in write_re.finditer(src):
        fn_start = max(src.rfind("\ndef ", 0, m.start()), src.rfind("\nasync def ", 0, m.start()), 0)
        screened = "_isg.screen(" in src[fn_start:m.start()] or "screen_and_record(" in src[fn_start:m.start()]
        out.append((m.start(), m.group("t"), screened))
    return out


def _ingest_screen_self_test():
    yes = "\ndef promote(x):\n    g = _isg.screen(client, org_id, rows, 'raw_sales')\n" \
          "    client.schema('commcalc').table('raw_sales').insert(g['kept']).execute()\n"
    no = "\ndef sneak(x):\n    client.schema('commcalc').table('daily_sales_feed').insert(rows).execute()\n"
    got_yes = screened_ingests(yes)
    got_no = screened_ingests(no)
    ok(len(got_yes) == 1 and got_yes[0][2] is True,
       "ingest-screen self-test: a screened insert must classify as screened")
    ok(len(got_no) == 1 and got_no[0][2] is False,
       "ingest-screen self-test: an unscreened insert must classify as a violation")


def _ingest_screen_guard(src, lineno):
    writes = screened_ingests(src)
    unscreened = [(lineno(p), t) for p, t, s in writes if not s]
    for ln, t in unscreened:
        print(f"  ✗ router.py:{ln}  .table('{t}') insert/upsert with NO ingest_store_guard screen "
              f"in the enclosing function — the wrong-tenant-attribution class (Diversey, 2026-07-14)")
    ok(not unscreened,
       f"{len(unscreened)} sales-basis ingest write(s) not fronted by _isg.screen(...) — every "
       f"raw_sales/daily_sales_feed write must pass the cross-tenant ingest guard (migration 280)")
    ok(len(writes) >= 1,
       f"ingest-screen guard found no guarded sales-basis writes at all ({len(writes)}) — "
       f"detection may be broken")
    # The guard module itself must keep covering the pay basis: shrinking GUARDED_TABLES would
    # quietly unguard these tables while this scan still passes.
    isg_src = open(_ISG, encoding="utf-8").read()
    m = re.search(r"GUARDED_TABLES\s*=\s*\{(?P<body>[^}]*)\}", isg_src)
    covered = set(re.findall(r"""["']([a-z_]+)["']\s*:""", m.group("body"))) if m else set()
    ok(INGEST_GUARDED <= covered,
       f"ingest_store_guard.GUARDED_TABLES no longer covers {sorted(INGEST_GUARDED - covered)} — "
       f"the pay-basis tables must stay screened")
    print(f"ingest-screen guard — {len(writes)} sales-basis write(s) inspected, all fronted by "
          f"ingest_store_guard.screen; GUARDED_TABLES covers {sorted(covered)}")


# ── LEAK CLASS 3: ENTITY ENUMERATION OUTSIDE THE CANONICAL HELPER (Nova Wave/Luxlink, 2026-09-04) ─
# The owner's cellfonz Cash Flow scope dropdown offered ANOTHER TENANT'S companies. The data half was
# poisoned `commcalc.companies` rows (LuxeLink entities created under the house org, mig 952); the
# SYSTEMIC half is here: `coa.org_companies` is THE one read of `commcalc.companies` for the whole
# backend — org-scoped, fail-closed, double-filtered by the pure `own_entities`
# (harness_finance_entity_enumeration.py). This scan fails the build on any OTHER select against the
# table, so no future page/endpoint can grow its own sibling enumeration that forgets the org scope
# or the fail-closed cross-check. Allowed outside the helper: writes (org_id in payload/chain, the
# CRUD endpoints) and org-scoped `count=` probes (billing's quantity driver counts rows, returns none).
_APP_DIR = os.path.join(_HERE, "app")
_CANON_ENUM_FILE = os.path.join("modules", "account", "coa.py")


def classify_company_chain(chain, relpath):
    """One `.table('companies') … .execute(` chain →
      'canonical' — the org-scoped select inside account/coa.py (org_companies itself)
      'write'     — insert/upsert (org_id in payload) or org-scoped update/delete
      'count'     — an org-scoped count-only probe (returns a number, never rows)
      'violation' — any other select: a sibling enumeration; must call coa.org_companies."""
    if _OPT_OUT in chain:
        return "write"          # explicit opt-out, same escape hatch as the sensitive-table scan
    has_ins = (".insert(" in chain) or (".upsert(" in chain)
    has_mut = (".update(" in chain) or (".delete(" in chain)
    if has_ins or has_mut:
        return "write" if (has_ins and not has_mut) or ("org_id" in chain) else "violation"
    if "count=" in chain:
        return "count" if "org_id" in chain else "violation"
    if relpath.endswith(_CANON_ENUM_FILE):
        return "canonical" if "org_id" in chain else "violation"
    return "violation"


def _entity_enum_self_test():
    cases = [
        (".table('companies').select(sel).eq('org_id', org_id).order('name').execute(",
         "modules/account/coa.py", "canonical"),
        (".table('companies').select('id,name').eq('org_id', org_id).execute(",
         "modules/account/router.py", "violation"),      # sibling enumeration — the leak class
        (".table('companies').select('id,name').execute(",
         "modules/account/coa.py", "violation"),          # canonical file but scope dropped
        (".table('companies').insert(row).execute(", "modules/account/router.py", "write"),
        (".table('companies').update(upd).eq('org_id', org_id).eq('id', cid).execute(",
         "modules/account/router.py", "write"),
        (".table('companies').update(upd).eq('id', cid).execute(",
         "modules/account/router.py", "violation"),       # unscoped mutate
        (".table('companies').select('id', count='exact').eq('org_id', org_id).execute(",
         "modules/billing/router.py", "count"),
        (".table('companies').select('id', count='exact').execute(",
         "modules/billing/router.py", "violation"),       # unscoped count
    ]
    for chain, rel, want in cases:
        got = classify_company_chain(chain, rel)
        ok(got == want, f"entity-enum self-test: expected {want}, got {got} for: {chain[:70]}")


def _entity_enum_guard():
    comp_re = re.compile(r"""\.table\(\s*["']companies["']\s*\)""")
    counts = {"canonical": 0, "write": 0, "count": 0}
    violations = []
    for root, _dirs, files in os.walk(_APP_DIR):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, _APP_DIR)
            src = open(path, encoding="utf-8").read()
            for m in comp_re.finditer(src):
                seg = src[m.start(): m.start() + _WINDOW]
                exec_at = seg.find(".execute(")
                chain = seg if exec_at == -1 else seg[: exec_at + len(".execute(")]
                kind = classify_company_chain(chain, rel)
                if kind == "violation":
                    ln = src.count("\n", 0, m.start()) + 1
                    violations.append((rel, ln, " ".join(chain.split())[:110]))
                else:
                    counts[kind] += 1
    for rel, ln, snippet in violations:
        print(f"  ✗ {rel}:{ln}  .table('companies') outside the canonical enumeration:  {snippet} …")
    ok(not violations,
       f"{len(violations)} commcalc.companies quer(ies) outside coa.org_companies — every entity "
       f"enumeration must go through the canonical fail-closed helper (owner 2026-09-04: foreign "
       f"companies in the cellfonz Cash Flow dropdown)")
    ok(counts["canonical"] == 1,
       f"expected exactly ONE canonical companies read (coa.org_companies), found "
       f"{counts['canonical']} — the helper was moved/duplicated; update _CANON_ENUM_FILE with care")
    # the fail-closed double filter must stay wired inside the helper
    coa_src = open(os.path.join(_APP_DIR, _CANON_ENUM_FILE), encoding="utf-8").read()
    body = coa_src.split("def org_companies", 1)[-1].split("\ndef ", 1)[0]
    ok("own_entities(" in body and 'eq("org_id"' in body.replace("'", '"'),
       "coa.org_companies must keep BOTH the .eq('org_id', …) scope and the pure own_entities "
       "double filter (fail-closed defense in depth)")
    print(f"entity-enumeration guard — 1 canonical read, {counts['write']} write(s), "
          f"{counts['count']} org-scoped count probe(s); no sibling enumerations")


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

    _ingest_screen_guard(src, lineno)
    _entity_enum_guard()

    _self_test()
    _ingest_screen_self_test()
    _entity_enum_self_test()

    print()
    print(f"{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
