"""THE LOCK — a commission-family column-mapping key is DERIVED per statement type, never spelled.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check
that FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

THE CLASS THIS LOCKS (index §30.10; PR #254 review, §30.8 OPEN (c)). Every reader and writer of the
ledger's column mapping keyed `commcalc.column_mapping` by the literal 'commission_ledger', so a
second statement type from the same carrier (a residual statement — a different layout, its own
sign) OVERWROTE the first's column map and its `sign_convention`. The fix is ONE derivation,
`commission_ledger.mapping_report_key(statement_type, registry_rows)`, that every caller goes
through; the default type maps onto today's key byte-for-byte.

WHAT FAILS THE BUILD
  (a) the literal 'commission_ledger' used AS A MAPPING KEY anywhere in backend/app, the harnesses,
      the scratchpad or frontend/src — on a line that reads or writes the mapping (`load_rules(`,
      `target_fields(`, `default_mapping(`, `identity_fields(`, `suggest(`, `drop_footer_rows(`,
      `required_fields(`, `period_source_field(`, `derive_row_periods(`, `report_key=` / `report_key:`
      / `"report_key":` / `.eq("report_key"`, `layout=`) — outside the ALLOW set below, where every
      entry carries its reason and a STALE entry (token no longer present) fails too. The literal as a
      TABLE name (`.table("commission_ledger")`, `target_table`, `file_type`) and as the registry's own
      dict key (`TARGET_FIELDS` / `TABLE_MAP` in column_mapping.py) is not a mapping-key use.
  (b) a module constant standing in for the key (`_INTAKE_REPORT_KEY` — the constant WAS the defect),
      or `MAPPING_REPORT_KEY` referenced outside the module that derives from it.
  (c) the derivation defined ONCE (`def mapping_report_key(` exactly once under backend/app), the
      router's I/O wrapper `_ledger_mapping_key` present and calling it, the older wizard's page saving
      under the key the analyze payload names (no literal).
  (d) NEGATIVE CONTROLS over synthetic sources: a literal key in a load → RED; in a save dict → RED; in
      a page → RED; the old constant → RED; a stale allow entry → RED; the table name and the registry's
      dict key → GREEN. A lock that cannot go red proves nothing.

Extends the carrier-vocab guard's posture (a dependency-free static scan on bare Python, one CI job):
.github/workflows/carrier-vocab-guard.yml runs it beside the report-kind lock.

  python3 backend/harness_mapping_key_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE_APP = os.path.join(ROOT, "backend", "app")
BE = os.path.join(ROOT, "backend")
FE = os.path.join(ROOT, "frontend", "src")
DERIVATION_FILE = "backend/app/modules/commcalc/commission_ledger.py"
ROUTER_FILE = "backend/app/modules/commcalc/router.py"
SETUP_PAGE = "frontend/src/app/(platform)/commcalc/commission-ledger/setup/page.tsx"

LITERAL = re.compile(r"""(?<![A-Za-z0-9_])["']commission_ledger["']""")
CONTEXT = re.compile(r"\b(load_rules|target_fields|default_mapping|identity_fields|required_fields|period_source_field"
                     r"|derive_row_periods|drop_footer_rows|suggest|registry_tuples)\s*\("
                     r"""|\breport_key\s*[=:]|["']report_key["']\s*:|\.eq\(\s*["']report_key["']|\blayout\s*=""")
OLD_CONSTANT = re.compile(r"\b_INTAKE_REPORT_KEY\b")
BASE_CONSTANT = re.compile(r"\bMAPPING_REPORT_KEY\b")

# ── ALLOW SET — (relative path, class) → reason. A stale entry fails. ────────────────────────────
ALLOW = {
    ("backend/app/modules/commcalc/report_kinds.py", "literal_key"):
        "HOUSE_KINDS is the byte-equal mirror of the 1010 seed: the commission card's `layout=` is DATA, pinned equal to the SQL by harness_report_kinds §A",
    ("backend/harness_commission_ledger_sign.py", "literal_key"):
        "the sign proof pins the DEFAULT statement type's layout by its literal key — the compatibility pin that the default maps onto today's key",
    ("backend/harness_onboarding_intake.py", "literal_key"):
        "Stage A's proof pins the default type's key (target_fields / suggest / identity_fields / the saved rows' report_key) — the compatibility pin",
    ("backend/harness_ledger_ma_sync.py", "literal_key"):
        "a fixture row of a PRE-EXISTING mapping (report_key as saved before statement types existed) — the compatibility pin",
    ("backend/harness_report_kinds.py", "literal_key"):
        "the learn hook is called with the default type's layout literal — pins that a residual under the default layout still keys to the residual card",
    ("backend/harness_statement_type_mapping.py", "literal_key"):
        "§C the migration-free compatibility pin: rows saved under the pre-change key must load byte-identically",
    ("backend/scratchpad/ledger_ma_sync_differential.py", "literal_key"):
        "a one-off differential over the default type's layout (scratchpad, not shipped)",
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
        print("  FAIL  %s   %s" % (label, str(detail)[:400]))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def strip_prose(src, ext):
    """Comment and docstring lines removed (crude, line-based) — a guard that grepped raw source would
    fail on its own explanation and PASS on a defect merely commented out."""
    if ext == ".py":
        src = re.sub(r'"""[\s\S]*?"""', '""', src)
        src = re.sub(r"'''[\s\S]*?'''", "''", src)
    out, inblock = [], False
    for ln in src.split("\n"):
        s = ln.strip()
        if inblock:
            if "*/" in s:
                inblock = False
            out.append("")
            continue
        if ext == ".py" and s.startswith("#"):
            out.append("")
            continue
        if ext != ".py" and (s.startswith("//") or s.startswith("*")):
            out.append("")
            continue
        if ext != ".py" and s.startswith(("/*", "{/*")):
            if "*/" not in s:
                inblock = True
            out.append("")
            continue
        out.append(ln)
    return out


def scan_text(rel, src):
    """Violations in ONE file: [(rel, class, line no, text)]. PURE — the negative controls feed it
    synthetic sources. `rel` is the repo-relative path (decides which rules apply)."""
    ext = os.path.splitext(rel)[1]
    lines = strip_prose(src, ext)
    v = []
    for i, ln in enumerate(lines, 1):
        if LITERAL.search(ln) and CONTEXT.search(ln):
            v.append((rel, "literal_key", i, ln.strip()[:140]))
        if rel.startswith("backend/app/") and OLD_CONSTANT.search(ln):
            v.append((rel, "old_constant", i, ln.strip()[:140]))
        if rel.startswith("backend/app/") and rel != DERIVATION_FILE and BASE_CONSTANT.search(ln):
            v.append((rel, "base_constant", i, ln.strip()[:140]))
    return v


def walk():
    files = {}
    for base, pat in ((BE_APP, (".py",)), (BE, (".py",)), (FE, (".ts", ".tsx"))):
        if base == BE:
            # every harness but this file: its (d) fixtures are the synthetic defects the scanner must catch
            cands = [os.path.join(BE, f) for f in os.listdir(BE)
                     if f.startswith("harness_") and f.endswith(".py") and f != os.path.basename(__file__)]
            sp = os.path.join(BE, "scratchpad")
            if os.path.isdir(sp):
                cands += [os.path.join(sp, f) for f in os.listdir(sp) if f.endswith(".py")]
            for p in cands:
                files[os.path.relpath(p, ROOT).replace(os.sep, "/")] = read(p)
            continue
        for dp, _d, fs in os.walk(base):
            if "node_modules" in dp:
                continue
            for f in fs:
                if f.endswith(pat):
                    p = os.path.join(dp, f)
                    files[os.path.relpath(p, ROOT).replace(os.sep, "/")] = read(p)
    return files


def scan(files, allow):
    """(violations, stale) over every file, honouring the allow set."""
    v, seen = [], set()
    for rel, src in sorted(files.items()):
        for item in scan_text(rel, src):
            key = (rel, item[1])
            seen.add(key)
            if key not in allow:
                v.append(item)
    stale = [k for k in allow if k not in seen]
    return v, stale


def main():
    print("=" * 78)
    print("MAPPING-KEY LOCK — one derivation per statement type; no literal 'commission_ledger' as a key (index §30.10)")
    print("=" * 78)
    files = walk()
    v, stale = scan(files, ALLOW)
    check("(a)+(b) no literal mapping key / stand-in constant across backend/app, harnesses, scratchpad, frontend/src", not v, v[:6])
    for rel, cls, ln, what in v[:12]:
        print("      · %s:%d  [%s]  %s" % (rel, ln, cls, what))
    check("no STALE allow entry (every allowed token is still present)", not stale, stale)
    for k, why in sorted(ALLOW.items()):
        print("      allow %-62s [%s] — %s" % (k[0], k[1], why[:70]))

    # (c) the derivation is ONE function; the router reaches it through its I/O wrapper
    n_def = sum(src.count("def mapping_report_key(") for rel, src in files.items() if rel.startswith("backend/app/"))
    check("(c) `mapping_report_key` is defined exactly once under backend/app", n_def == 1, n_def)
    deriv = files.get(DERIVATION_FILE, "")
    check("(c) …in commission_ledger.py, next to the base key it derives from",
          "def mapping_report_key(" in deriv and 'MAPPING_REPORT_KEY = "commission_ledger"' in deriv)
    router = files.get(ROUTER_FILE, "")
    check("(c) the router has the ONE I/O wrapper `_ledger_mapping_key` and it calls the derivation",
          "def _ledger_mapping_key(" in router and router.count("def _ledger_mapping_key(") == 1
          and "commission_ledger.mapping_report_key(" in router)
    check("(c) the intake analyze carries the derived key on its context and the commit saves under it",
          '"report_key": report_key,' in router and 'report_key = ctx["report_key"]' in router
          and 'kw = {"report_key": report_key,' in router)
    check("(c) the older wizard's analyze / import take `statement_type` and derive the same key",
          router.count("statement_type: str = Form(\"\")") >= 2 and router.count("rk = _ledger_mapping_key(client, org_id, statement_type, source_report)") == 2)
    setup = files.get(SETUP_PAGE, "")
    check("(c) the setup page saves under the key the analyze payload names (`report_key: rk`), never a literal",
          "report_key: rk" in setup and "analysis?.report_key" in setup and not LITERAL.search(setup))

    # (d) NEGATIVE CONTROLS — synthetic sources through the same scanner
    red = lambda rel, src, cls: any(i[1] == cls for i in scan_text(rel, src))
    check("(d) a literal key in a load → RED",
          red("backend/app/modules/x.py", 'rules = column_mapping.load_rules(client, org_id, "commission_ledger", cid)\n', "literal_key"))
    check("(d) a literal key in a save dict → RED",
          red("backend/app/modules/x.py", 'kw = {"report_key": "commission_ledger", "target_field": tf}\n', "literal_key"))
    check("(d) a literal key in a page's save → RED",
          red("frontend/src/app/x/page.tsx", "await api('/x', { body: JSON.stringify({ report_key: 'commission_ledger', target_field: k }) })\n", "literal_key"))
    check("(d) a literal key as a learned layout → RED",
          red("backend/app/modules/x.py", 'learn(headers, key, layout="commission_ledger")\n', "literal_key"))
    check("(d) the old stand-in constant → RED",
          red("backend/app/modules/x.py", "_INTAKE_REPORT_KEY = commission_ledger.MAPPING_REPORT_KEY\n", "old_constant"))
    check("(d) MAPPING_REPORT_KEY referenced outside the deriving module → RED",
          red("backend/app/modules/commcalc/router.py", "layout=commission_ledger.MAPPING_REPORT_KEY)\n", "base_constant")
          and not red(DERIVATION_FILE, 'MAPPING_REPORT_KEY = "commission_ledger"\n', "base_constant"))
    check("(d) the TABLE name and the registry's own dict key → GREEN",
          not scan_text("backend/app/modules/x.py", 'client.schema("commcalc").table("commission_ledger").select("*")\n'
                                                     '"commission_ledger": "commission_ledger",\n'
                                                     '{"org_id": org_id, "file_type": "commission_ledger", "target_table": "commission_ledger"}\n'))
    check("(d) a commented-out defect is NOT a pass (the comment is stripped, the live line is caught)",
          red("backend/app/modules/x.py", '# rules = load_rules(c, o, "commission_ledger")\nrules = load_rules(c, o, "commission_ledger")\n', "literal_key")
          and not red("backend/app/modules/x.py", '# rules = load_rules(c, o, "commission_ledger")\n', "literal_key"))
    _v, _stale = scan({"backend/app/modules/x.py": "x = 1\n"}, {("backend/app/modules/gone.py", "literal_key"): "was here"})
    check("(d) a stale allow entry → RED", _stale == [("backend/app/modules/gone.py", "literal_key")])

    print("\n%d passed, %d failed" % (P, F))
    return 1 if F else 0


if __name__ == "__main__":
    sys.exit(main())
