"""THE LOCK — a ledger statement has ONE identity, ONE period spelling and ONE lander; every reader
filters by the family and by period_keys; N landings are never summed silently. (index §30.15)

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check
that FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

THE CLASS THIS LOCKS (owner 2026-09-22; index §30.15). The ledger key was ROUTE-DEPENDENT (the intake
wrote `<carrier>__<statement slug>`, the older wizard the bare carrier code, the MA refresh the bare
template key), the PERIOD was stored as typed ('Aug 2026' / 'aug 2026' / 'August 2026'), and no reader
guarded against N copies of one statement × period — so one statement was two, the scoped replace
never saw the other copy, orphans counted nowhere, and a ledger-sourced P&L would have booked a
month twice. The fix is ONE derivation (`commission_ledger.ledger_source_report` / `ledger_identity` /
`source_report_family`), ONE period home (`account/_period.canonical_period` / `period_keys`,
dereferenced by `commission_ledger.canonical_period` / `ledger_period_keys`), ONE lander
(`router._ledger_land_rows`) and ONE guard (`commission_ledger.landings_for` / `landing_conflicts`).

WHAT FAILS THE BUILD
  (a) a second derivation: `def ledger_source_report(` / `ledger_identity(` / `source_report_family(` /
      `identity_key(` / `landings_for(` / `landing_conflicts(` defined anywhere but commission_ledger.py;
      `def period_keys(` / `def parse_period(`-with-canonical anywhere but account/_period.py (the
      commcalc module's `canonical_period` / `ledger_period_keys` must DEREFERENCE `_pd.`); a second
      `<base>__<type>` key COMPOSITION (`__{` in an f-string) outside commission_ledger.py and
      column_mapping.variant_report_key.
  (b) a ledger READ that filters by a literal: any chain on `commission_ledger` (or on the router's
      `_ledger_query(`) that carries `.eq("period"`, `.eq("source_report"` or `_pvariants(` — readers
      filter the FAMILY (`in_("source_report", …family…)`) and EVERY period spelling (`in_("period",
      ledger_period_keys(…))`) through `_ledger_query`.
  (c) a second lander: `.table("commission_ledger").insert(` anywhere in backend/app outside
      `router._ledger_land_rows`; the MA refresh must call `_ledger_land_rows(` and hold no insert.
  (d) the intake un-wired: `onboarding_intake.source_report_key` / `slug` / `STATEMENT_TYPE_DEFAULT`
      must dereference `CL.ledger_source_report(` / `CL.ledger_slug(` / `CL.LEDGER_STATEMENT_TYPE_DEFAULT`.
  (e) the guard un-wired: `commission_ledger_summary` → `_ledger_guarded_summary(`; by-rep,
      observed-types and `_statement_buckets` → `landing_conflicts(`; `ledger_pnl.ledger_bookings` →
      `_cl.landing_conflicts(`; coa hands `conflicts=` to `divergence`; the lander stamps
      `ledger_source_report(` + `canonical_period(` and its wipe goes through `_ledger_delete_scoped(`.
  (f) the pages: the Commission Ledger page renders `landing_conflict` and calls
      `/commission-ledger/landings/retire`; the intake page renders `replaced_note`.
  (g) NEGATIVE CONTROLS over synthetic sources — a chain with `.eq("period"` → RED; with `_pvariants(`
      → RED; an insert outside the lander → RED; a second `__{` composition → RED; a stale allow entry
      → RED; a chain through `_ledger_query(...).eq("category"` → GREEN.

Extends the carrier-vocab guard's posture (a dependency-free static scan on bare Python, one CI job):
.github/workflows/carrier-vocab-guard.yml runs it beside the mapping-key and landing-identity locks.

  python3 backend/harness_ledger_identity_lock.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE_APP = os.path.join(ROOT, "backend", "app")
FE = os.path.join(ROOT, "frontend", "src")
HOME = "modules/commcalc/commission_ledger.py"
PERIOD_HOME = "modules/account/_period.py"
ROUTER = "modules/commcalc/router.py"
INTAKE = "modules/commcalc/onboarding_intake.py"
LEDGER_PNL = "modules/account/ledger_pnl.py"
COA = "modules/account/coa.py"
COLUMN_MAPPING = "modules/commcalc/column_mapping.py"
LEDGER_PAGE = "app/(platform)/commcalc/commission-ledger/page.tsx"
INTAKE_PAGE = "app/(platform)/onboarding/intake/page.tsx"
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

DERIVATIONS = ("ledger_source_report", "ledger_identity", "source_report_family", "identity_key",
               "landings_for", "landing_conflicts", "template_key", "ledger_slug")
PERIOD_FUNCS = ("period_keys", "canonical_period", "parse_period", "is_canonical_period")
# `parse_period` has a documented, pre-existing sibling in commcalc/calculator.py (month-name only — the
# _period docstring names it); the LEDGER's spelling functions are the three below, one home each
PERIOD_ONLY_HOME = ("period_keys", "is_canonical_period")
LEDGER_CHAIN = re.compile(r"""(?<!def )(?:\.table\(\s*["']commission_ledger["']\s*\)|_ledger_query\()""")
LEDGER_CONST_CHAIN = re.compile(r"\.table\(\s*(?:_cl\.|commission_ledger\.|CL\.)?LEDGER_TABLE\s*\)")
LITERAL_FILTER = re.compile(r"""\.eq\(\s*["'](?:period|source_report)["']|_pvariants\(""")
INSERT = re.compile(r"""\.table\(\s*["']commission_ledger["']\s*\)\s*\.insert\(""")
COMPOSE = re.compile(r"""__\{""")
_WINDOW = 1400

# ── ALLOW — (relative path, class) → reason. A stale entry (token no longer present) fails. ───────
ALLOW = {
    ("modules/commcalc/report_kinds.py", "second_slug"):
        "`_tok` normalises the STATEMENT-TYPE TOKEN vocabulary (report_kinds.statement_type_token) — a different key than the ledger slug, pre-existing; folding the two normalisers is a follow-up, not a second ledger identity",
}
assert all(v for v in ALLOW.values()), "every allow entry carries a reason"

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:500]))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def walk(root, exts):
    files = {}
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules")]
        for f in fs:
            if f.endswith(exts):
                p = os.path.join(dp, f)
                files[os.path.relpath(p, root).replace(os.sep, "/")] = read(p)
    return files


_DOCSTRING = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'')


def strip_comments(src):
    """Python source without comment lines and docstrings (a comment or docstring naming `.eq("period"`
    as the thing NOT to do must not trip the scanner)."""
    src = _DOCSTRING.sub('""', src)
    out = []
    for ln in src.split("\n"):
        s = ln.strip()
        if s.startswith("#"):
            continue
        out.append(ln.split("  # ")[0])
    return "\n".join(out)


def functions(src):
    """{name: source} for every top-level function (sliced by line once)."""
    tree = ast.parse(src)
    lines = src.split("\n")
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = (node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1
            out[node.name] = "\n".join(lines[start:node.end_lineno])
    return out


# ── (b) literal-filtered ledger chains ────────────────────────────────────────────────────────────
def literal_ledger_filters(rel, src):
    """Every ledger chain (from `.table("commission_ledger")` / `_ledger_query(` to the next `.execute(`)
    that filters `period` / `source_report` by a literal or through `_pvariants`. Returns [(rel, snippet)]."""
    body = strip_comments(src)
    bad = []
    pat = LEDGER_CHAIN if rel != HOME else re.compile(LEDGER_CHAIN.pattern + "|" + LEDGER_CONST_CHAIN.pattern)
    for m in pat.finditer(body):
        window = body[m.start(): m.start() + _WINDOW]
        end = window.find(".execute(")
        chain = window[: end if end >= 0 else _WINDOW]
        hit = LITERAL_FILTER.search(chain)
        if hit:
            bad.append((rel, chain[max(0, hit.start() - 60): hit.end() + 20].replace("\n", " ")))
    return bad


def inserts_outside_lander(files):
    bad = []
    for rel, src in sorted(files.items()):
        body = strip_comments(src)
        if not INSERT.search(body):
            continue
        if rel == ROUTER:
            for name, fsrc in functions(src).items():
                if INSERT.search(strip_comments(fsrc)) and name != "_ledger_land_rows":
                    bad.append((rel, name))
        else:
            bad.append((rel, "<module>"))
    return bad


def second_derivations(files):
    bad = []
    for rel, src in sorted(files.items()):
        if rel == HOME:
            continue
        for fn in DERIVATIONS:
            if re.search(r"^\s*def %s\(" % fn, src, re.M):
                bad.append((rel, fn))
        if rel != PERIOD_HOME:
            for fn in PERIOD_ONLY_HOME:
                if re.search(r"^\s*def %s\(" % fn, src, re.M):
                    bad.append((rel, fn))
    return bad


def second_compositions(files, allow):
    bad = []
    for rel, src in sorted(files.items()):
        if rel == HOME:
            continue
        body = strip_comments(src)
        if COMPOSE.search(body):
            if (rel, "compose") in allow:
                continue
            bad.append((rel, COMPOSE.search(body).group(0)))
    return bad


def second_slugs(files, allow):
    """A second `[^a-z0-9]+ → _` slug normaliser in COMMCALC (the ledger's neighbourhood — the intake
    carried one until §30.15). Other modules' slugs key other things and are out of scope here."""
    bad = []
    for rel, src in sorted(files.items()):
        if rel == HOME or not rel.startswith("modules/commcalc/"):
            continue
        body = strip_comments(src)
        if re.search(r"""re\.sub\(\s*r?["']\[\^a-z0-9\]\+["']\s*,\s*["']_["']""", body):
            if (rel, "second_slug") in allow:
                continue
            bad.append(rel)
    return bad


def stale_allows(files, allow):
    stale = []
    for (rel, cls), _why in allow.items():
        src = strip_comments(files.get(rel, ""))
        present = (COMPOSE.search(src) if cls == "compose"
                   else re.search(r"""re\.sub\(\s*r?["']\[\^a-z0-9\]\+["']\s*,\s*["']_["']""", src))
        if not present:
            stale.append((rel, cls))
    return stale


def main():
    global P, F
    be = walk(BE_APP, (".py",))
    fe = walk(FE, (".ts", ".tsx"))
    home = be.get(HOME, "")
    period_home = be.get(PERIOD_HOME, "")
    router = be.get(ROUTER, "")
    rf = functions(router)
    print("LEDGER STATEMENT IDENTITY LOCK — one derivation, one period home, one lander, one guard (index §30.15)")
    print("=" * 100)

    # (a) one home
    check("(a) every derivation is defined ONCE, in commission_ledger.py",
          all(len(re.findall(r"^def %s\(" % fn, home, re.M)) == 1 for fn in DERIVATIONS) and not second_derivations(be),
          second_derivations(be))
    check("(a) the period home: period_keys / parse_period / canonical_period / is_canonical_period live in account/_period.py",
          all(len(re.findall(r"^def %s\(" % fn, period_home, re.M)) == 1 for fn in PERIOD_FUNCS))
    cl_f = functions(home)
    check("(a) commission_ledger.canonical_period / ledger_period_keys / is_orphan_period DEREFERENCE _period (never a copy)",
          "_pd.canonical_period(" in cl_f.get("canonical_period", "") and "_pd.period_keys(" in cl_f.get("ledger_period_keys", "")
          and "_pd.is_canonical_period(" in cl_f.get("is_orphan_period", "")
          and not re.search(r"_MONTHS\s*=|month_name", strip_comments(home)))
    cp_copies = [rel for rel, src in be.items() if rel != PERIOD_HOME and re.search(r"^def canonical_period\(", src, re.M)
                 and "_pd.canonical_period(" not in functions(src).get("canonical_period", "")]
    check("(a) every other `canonical_period` (commission_ledger, ma_recon) DEREFERENCES the period home — no second copy of the rule",
          not cp_copies and re.search(r"^def canonical_period\(", period_home, re.M), cp_copies)
    check("(a) no second `<base>__<type>` composition outside commission_ledger.py (+ the generic variant_report_key)",
          not second_compositions(be, ALLOW), second_compositions(be, ALLOW))
    check("(a) no second slug normaliser in commcalc outside the allow set", not second_slugs(be, ALLOW), second_slugs(be, ALLOW))
    check("(a) no stale allow entry", not stale_allows(be, ALLOW), stale_allows(be, ALLOW))

    # (b) every ledger read filters the family / every spelling
    bad = [b for rel, src in be.items() for b in literal_ledger_filters(rel, src)]
    check("(b) no ledger chain filters `period` / `source_report` by a literal or through _pvariants (%d chains scanned)"
          % sum(len(LEDGER_CHAIN.findall(strip_comments(s))) for s in be.values()), not bad, bad)
    check("(b) the router's ONE query builder exists, filters the FAMILY and EVERY period spelling",
          "def _ledger_query(" in router and "source_report_family(" in rf.get("_ledger_query", "")
          and "ledger_period_keys(" in rf.get("_ledger_query", ""))
    readers = ("commission_ledger_summary", "commission_ledger_rows", "commission_ledger_observed_types",
               "commission_ledger_by_rep", "_intake_reread", "_ledger_existing_by_origin", "commission_ledger_provenance",
               "_mcw_ledger_rows", "_statement_buckets", "commission_ledger_landings", "_ledger_landings_present")
    check("(b) every ledger reader in the router builds on _ledger_query",
          all("_ledger_query(" in rf.get(fn, "") for fn in readers), [fn for fn in readers if "_ledger_query(" not in rf.get(fn, "")])
    check("(b) the P&L reader filters period through _period.period_keys (in_), never a literal",
          '.in_("period", keys)' in be.get(LEDGER_PNL, "") and 'eq("period"' not in strip_comments(be.get(LEDGER_PNL, "")))
    check("(b) the rule reads use the family too (load_rules_meta, _intake_house_defaults, get_commission_category_map)",
          'in_("source_report", source_report_family(source_report) + ["*"])' in cl_f.get("load_rules_meta", "")
          and "source_report_family(" in rf.get("_intake_house_defaults", "")
          and "source_report_family(" in rf.get("get_commission_category_map", ""))

    # (c) one lander
    check("(c) the ONLY insert into commission_ledger is router._ledger_land_rows",
          not inserts_outside_lander(be) and INSERT.search(strip_comments(rf.get("_ledger_land_rows", ""))) is not None,
          inserts_outside_lander(be))
    sync = rf.get("commission_ledger_ma_sync", "")
    check("(c) the MA refresh lands THROUGH the lander (origin ma_sync) and holds no insert of its own",
          "_ledger_land_rows(" in sync and "origin=ledger_ma_sync.ORIGIN_SYNC" in sync and not INSERT.search(strip_comments(sync)))
    check("(c) the older wizard and the intake commit land through the lander",
          "_ledger_land_rows(" in rf.get("commission_ledger_import", "") and "_ledger_land_rows(" in rf.get("_intake_commit_commission", ""))
    land = rf.get("_ledger_land_rows", "")
    check("(c) the lander stamps the derived identity and the canonical period on EVERY row, and wipes through _ledger_delete_scoped",
          "commission_ledger.ledger_source_report(" in land and "commission_ledger.canonical_period(" in land
          and 'r["source_report"] = stored_key' in land and "_ledger_delete_scoped(" in land)
    dele = rf.get("_ledger_delete_scoped", "")
    check("(c) the wipe is the FAMILY × every period spelling, measured first (replaced landings on the meta)",
          'in_("source_report", family)' in dele and 'in_("period", keys)' in dele and "_ledger_landings_present(" in dele
          and 'meta["replaced"]' in dele)

    # (d) the intake dereferences
    intake = be.get(INTAKE, "")
    ifn = functions(intake)
    check("(d) onboarding_intake.source_report_key / slug / STATEMENT_TYPE_DEFAULT dereference commission_ledger",
          "CL.ledger_source_report(" in ifn.get("source_report_key", "") and "CL.ledger_slug(" in ifn.get("slug", "")
          and "STATEMENT_TYPE_DEFAULT = CL.LEDGER_STATEMENT_TYPE_DEFAULT" in intake)

    # (e) the guard is wired
    check("(e) /commission-ledger/summary refuses through _ledger_guarded_summary; by-rep / observed-types / _statement_buckets through landing_conflicts",
          "_ledger_guarded_summary(" in rf.get("commission_ledger_summary", "")
          and all("landing_conflicts(" in rf.get(fn, "") for fn in ("commission_ledger_by_rep", "commission_ledger_observed_types", "_statement_buckets", "_ledger_guarded_summary")))
    lp = functions(be.get(LEDGER_PNL, ""))
    check("(e) the P&L booking dereferences the ledger's guard (ledger_bookings → _cl.landing_conflicts; identity_key for the held set) and coa hands the conflicts to the words",
          "_cl.landing_conflicts(" in lp.get("ledger_bookings", "") and "_cl.identity_key(" in lp.get("ledger_bookings", "")
          and "conflicts=None" in lp.get("divergence", "") and 'conflicts=_lb.get("conflicts")' in be.get(COA, ""))
    check("(e) summarize carries the additive landings keys (landings, landing_conflict) and its totals are computed before them",
          '"landings": _landings' in cl_f.get("summarize", "") and '"landing_conflict"' in cl_f.get("summarize", ""))
    check("(e) the intake's retire covers a commission instance (its statement × period family) through the SAME counted remove",
          '== "commission"' in rf.get("_intake_landed_slice_of", "") and "_ledger_landing_slice(" in rf.get("_intake_landed_slice_of", "")
          and 'slice_.get("ledger")' in rf.get("_intake_remove_landed", "")
          and "_intake_remove_landed(" in rf.get("commission_ledger_landing_retire", ""))

    # (f) the pages
    page = fe.get(LEDGER_PAGE, "")
    check("(f) the Commission Ledger page renders the refusal and retires a landing through the counted endpoint",
          "landing_conflict" in page and "/commission-ledger/landings/retire" in page and "confirm_rows" in page)
    ipage = fe.get(INTAKE_PAGE, "")
    check("(f) the intake's 3.9 card says what a landing replaced and can retire the statement",
          "replaced_note" in ipage and "retireStatement" in ipage and "remove_landed" in ipage)

    # (g) negative controls — a lock that cannot go red proves nothing
    syn = 'def f(client, org_id, period):\n    q = client.schema("commcalc").table("commission_ledger").select("*").eq("org_id", org_id).eq("period", period)\n    return q.execute().data\n'
    check("(g) a chain filtering period by a literal → RED", bool(literal_ledger_filters("modules/x.py", syn)))
    syn2 = 'def f(client, org_id, period):\n    q = _ledger_query(client, org_id, "k", "").in_("period", _pvariants(period))\n    return q.execute().data\n'
    check("(g) a chain through _pvariants → RED", bool(literal_ledger_filters("modules/x.py", syn2)))
    syn3 = 'def f(client, org_id, sr, period):\n    q = client.schema("commcalc").table("commission_ledger").select("*").eq("org_id", org_id).eq("source_report", sr)\n    return q.execute().data\n'
    check("(g) a chain filtering source_report by a literal → RED", bool(literal_ledger_filters("modules/x.py", syn3)))
    good = 'def f(client, org_id):\n    q = _ledger_query(client, org_id, "k", "p").eq("category", "spiff")\n    return q.execute().data\n'
    check("(g) a chain through _ledger_query with an unrelated .eq → GREEN", not literal_ledger_filters("modules/x.py", good))
    check("(g) a comment naming the forbidden filter → GREEN",
          not literal_ledger_filters("modules/x.py", '# never .eq("period", period) on .table("commission_ledger")\n'))
    b2 = dict(be); b2["modules/commcalc/other.py"] = 'def land(c, rows):\n    c.schema("commcalc").table("commission_ledger").insert(rows).execute()\n'
    check("(g) an insert outside the lander → RED", bool(inserts_outside_lander(b2)))
    b3 = dict(be); b3["modules/commcalc/other.py"] = 'def k(b, t):\n    return f"{b}__{t}"\n'
    check("(g) a second key composition → RED", bool(second_compositions(b3, ALLOW)))
    b4 = dict(be); b4["modules/commcalc/other.py"] = 'def ledger_identity(x):\n    return x\n'
    check("(g) a second derivation → RED", bool(second_derivations(b4)))
    a2 = dict(ALLOW); a2[("modules/commcalc/nowhere.py", "compose")] = "stale"
    check("(g) a stale allow entry → RED", bool(stale_allows(be, a2)))

    # CI
    wf = read(WORKFLOW) if os.path.exists(WORKFLOW) else ""
    check("this lock runs in CI (carrier-vocab-guard.yml lists and runs it)",
          "harness_ledger_identity_lock.py" in wf and "python3 harness_ledger_identity_lock.py" in wf)

    print("\n%d passed, %d failed" % (P, F))
    if F:
        print("FAIL — the ledger statement identity is un-wired or copied somewhere; see above.")
        return 1
    print("OK — one identity derivation, one period home, one lander, every reader on the family, the guard wired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
