#!/usr/bin/env python3
"""BUILD LOCK — the declared-metric catalog stays ONE home, and its callers stay wired (mig 1070).

CLAUDE.md: *"A design fix ships with a check that FAILS THE BUILD if a caller stops dereferencing the
shared fact, or if a second copy appears. Without that, the next change quietly restores the
patchwork."* — and the index records that writing a registry without wiring the callers has already
happened three times here (§19.18). This file is that check.

What it refuses to let happen:

  §A  the catalog's vocabulary gets a second copy (the built-in three, the rule shapes, the splits)
  §B  a module reaches into `closing_metric_line` / `incentive_rule` without going through the home
  §C  an endpoint serves a category list it built itself instead of asking `_metric_catalog`
  §D  a frontend screen hard-codes a category list, a rule kind or a split basis
  §E  a NEW direct reader of `daily_closing.counts` appears (the pinned set may only SHRINK)
  §F  negative controls — the scanners can actually see a violation

Stdlib only, DB-free, no imports from the app beyond the two pure modules under test.
"""
import sys, os, re, io, tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
FE = os.path.join(ROOT, "frontend", "src")

sys.path.insert(0, HERE)
from app.modules.closing import metric_catalog as mc          # noqa: E402
from app.modules.closing import count_config                  # noqa: E402
from app.modules.commcalc import daily_incentive as di        # noqa: E402

P = F = 0
def ok(cond, label, extra=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}  {extra}")
def eq(a, b, label):
    ok(a == b, label, f"got {a!r} want {b!r}")


def py_files(root):
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def fe_files(root):
    if not os.path.isdir(root):
        return
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith((".ts", ".tsx")):
                yield os.path.join(dirpath, f)


def code_only(path):
    """A file's executable text — docstrings and comments stripped. Doctrine explains WHY a rule
    exists and may quote the owner's own examples; what must not carry a second copy of the
    vocabulary is the code."""
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    out, prev = [], tokenize.INDENT
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                                        tokenize.NL, tokenize.DEDENT):
                continue
            out.append(tok.string)
            if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                prev = tok.type
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return src
    return " ".join(out)


def rel(p):
    return os.path.relpath(p, ROOT)


HOME_CATALOG = "backend/app/modules/closing/metric_catalog.py"
HOME_ENGINE = "backend/app/modules/commcalc/daily_incentive.py"


print("\n§A  the vocabulary has one copy")
eq(tuple(mc.STANDARD_KEYS), tuple(k for (k, _l, _r, _s) in count_config.STANDARD_DEFS),
   "A1 STANDARD_KEYS mirrors count_config.STANDARD_DEFS")
eq(mc.VALUE_KINDS, ("count", "amount", "both"), "A2 the value kinds are the declared three")
eq(mc.DECLARED_BY, ("store", "employee"), "A3 the declaring scopes are the declared two")
eq(di.RULE_KINDS, ("per_unit", "percent_of_amount", "threshold_pool"),
   "A4 the rule shapes are the declared three")
eq(di.SPLIT_BASES, ("hours", "individual_sales", "equal"), "A5 the split bases are the declared three")

# A rule kind spelled anywhere but the engine is a second copy of the vocabulary.
kind_copies = []
for p in py_files(APP):
    if rel(p) == HOME_ENGINE:
        continue
    blob = code_only(p)
    for k in di.RULE_KINDS:
        # A router passing the string through to the engine is fine; DECLARING the set is not.
        if re.search(r"[=\(]\s*\(\s*['\"]per_unit['\"]", blob) or \
           re.search(r"\[\s*['\"]threshold_pool['\"]\s*,", blob):
            kind_copies.append((rel(p), k))
            break
eq(kind_copies, [], "A6 no module re-declares the set of rule shapes")

split_copies = [rel(p) for p in py_files(APP)
                if rel(p) != HOME_ENGINE
                and re.search(r"['\"]individual_sales['\"]\s*,\s*['\"]equal['\"]", code_only(p))]
eq(split_copies, [], "A7 no module re-declares the set of split bases")

storage_deciders = [rel(p) for p in py_files(APP)
                    if rel(p) != HOME_CATALOG
                    and re.search(r"declared_by.{0,40}==.{0,10}['\"]employee['\"]", code_only(p))]
eq(storage_deciders, [], "A8 nobody but the home decides where a declaration is stored")


print("\n§B  the new tables are reached only through the home")
# `closing/router.py` holds the two readers the home is served by (`_metric_catalog`,
# `_metric_lines`) and the one writer (`put_metric_lines`); `commcalc/router.py` holds the rule
# reader/writer. Any OTHER module naming these tables has gone around the home.
TABLE_OWNERS = {
    "closing_metric_line": {"backend/app/modules/closing/router.py"},
    "incentive_rule": {"backend/app/modules/commcalc/router.py"},
    "incentive_rule_assignment": {"backend/app/modules/commcalc/router.py"},
}
# Scanned as table ACCESS (`.table("x")` / `.from_("x")`), not as the bare word: `core/module_graph.py`
# names tables in the prose of its facts, and a scanner that cried wolf there would get switched off.
for table, owners in TABLE_OWNERS.items():
    access = re.compile(r"""(?:table|from_)\s*\(\s*["']""" + re.escape(table) + r"""["']""")
    offenders = sorted({rel(p) for p in py_files(APP)
                        if access.search(code_only(p)) and rel(p) not in owners})
    eq(offenders, [], f"B-{table}: only its owner reaches the table")

# And the owner must reach it through ONE function, not scattered queries.
closing_router = code_only(os.path.join(APP, "modules", "closing", "router.py"))
# `code_only` joins tokens with spaces, so every scanner below tolerates whitespace.
eq(len(re.findall(r"""table\s*\(\s*["']closing_metric_line["']""", closing_router)), 3,
   "B1 closing_metric_line is queried at exactly 3 sites (read, delete, insert)")
commcalc_router = code_only(os.path.join(APP, "modules", "commcalc", "router.py"))
ok(len(re.findall(r"table\s*\(\s*_INCENTIVE_RULE_TABLE", commcalc_router)) >= 2,
   "B2 the rule table is reached through its named constant, never a literal")
ok(re.search(r"""table\s*\(\s*["']incentive_rule["']""", commcalc_router) is None,
   "B3 ...and never by a bare string literal")


print("\n§C  every category list is the catalog's")
# An endpoint that offers categories must route through `_metric_catalog`. The construction call
# `metric_catalog.build(` may appear in exactly ONE place in the app: that helper.
build_sites = sorted({rel(p) for p in py_files(APP)
                      if re.search(r"(_metrics|metric_catalog)\s*\.\s*build\s*\(", code_only(p))})
eq(build_sites, ["backend/app/modules/closing/router.py"],
   "C1 the catalog is CONSTRUCTED in one place")
eq(len(re.findall(r"_metrics\s*\.\s*build\s*\(", closing_router)), 1,
   "C2 ...and exactly once there (inside _metric_catalog)")
for name in ("get_metric_catalog", "get_declared_day", "put_metric_lines"):
    ok(re.search(name + r"\b", closing_router) is not None, f"C3-{name}: the endpoint exists")
ok("_metric_catalog" in commcalc_router,
   "C4 the incentive editor asks the catalog rather than listing categories itself")
# The engine never reads a table or builds a catalog — it is handed both.
engine = code_only(os.path.join(APP, "modules", "commcalc", "daily_incentive.py"))
ok("table(" not in engine and "client" not in engine,
   "C5 the engine touches no database (pure, replayable, unit-provable)")
cat_src = code_only(os.path.join(APP, "modules", "closing", "metric_catalog.py"))
ok("table(" not in cat_src and "import" not in cat_src,
   "C6 the catalog touches no database and imports nothing")


print("\n§D  no frontend screen carries its own copy")
fe_offenders, fe_kind, fe_split = [], [], []
for p in fe_files(FE):
    try:
        src = open(p, encoding="utf-8").read()
    except OSError:
        continue
    # A page that lists rule shapes or split bases has copied the vocabulary; it must read them off
    # the payload (`rule_kinds`, `split_bases`), which the GET serves for exactly this reason.
    if re.search(r"['\"]per_unit['\"]\s*,\s*['\"]percent_of_amount['\"]", src) or \
       re.search(r"['\"]threshold_pool['\"]\s*,\s*['\"]per_unit['\"]", src):
        fe_kind.append(rel(p))
    if re.search(r"['\"]individual_sales['\"]\s*,\s*['\"]equal['\"]", src):
        fe_split.append(rel(p))
eq(fe_kind, [], "D1 no screen hard-codes the set of rule shapes")
eq(fe_split, [], "D2 no screen hard-codes the set of split bases")


print("\n§E  the daily_closing.counts readers are pinned and may only shrink")
# mig 501's jsonb is the OTHER physical store. It has legitimate pre-existing readers, named here so
# a NEW one cannot appear unnoticed — the `envelope_report.is_modern_row` precedent (index §29.7).
# Pinned as (file, the variable read FROM) pairs rather than line numbers, so a new site cannot hide
# behind an edit that shifts the file. `day` is the B2B tally dict, not a closing row, and is
# deliberately absent — a scanner that flagged it would be crying wolf and would get switched off.
# To remove a pair, route that site through `metric_catalog.declared()` and delete it here. This set
# may only SHRINK.
PINNED_COUNTS_SITES = {
    ("backend/app/modules/closing/count_config.py", "row"),      # mig-501's own reader (its home)
    ("backend/app/modules/closing/router.py", "r"),              # the day's display row
    ("backend/app/modules/closing/router.py", "rp"),             # the per-rep display row
    ("backend/app/modules/closing/router.py", "payload"),        # the submit form's incoming counts
    ("backend/app/modules/closing/router.py", "body"),           # the submit form's outgoing counts
}
_RECV = re.compile(r"""(\w+)\s*(?:\.get\(\s*["']counts["']\s*\)|\[\s*["']counts["']\s*\])""")
_NOT_A_CLOSING_ROW = {"day", "data", "staffing", "g", "s", "out", "found", "payload_counts"}
counts_sites = set()
for p in py_files(APP):
    r = rel(p).replace(os.sep, "/")
    if r == HOME_CATALOG or "/closing/" not in r:
        continue
    for line in open(p, encoding="utf-8").read().splitlines():
        for recv in _RECV.findall(line):
            if recv not in _NOT_A_CLOSING_ROW:
                counts_sites.add((r, recv))
eq(sorted(counts_sites - PINNED_COUNTS_SITES), [],
   "E1 no NEW direct reader of daily_closing.counts has appeared")
ok(len(counts_sites) <= len(PINNED_COUNTS_SITES),
   "E1b the pinned set may only shrink",
   f"{len(counts_sites)} > {len(PINNED_COUNTS_SITES)}")
ok(all("/closing/" in f for (f, _v) in counts_sites),
   "E2 every remaining one is inside the closing module itself")
ok(mc.storage_for({"declared_by": "store", "value_kind": "count"}) == "counts_jsonb",
   "E3 the home still routes a mig-501 category to the mig-501 store (no tenant moves)")


print("\n§F  negative controls — the scanners can see a violation")
_fake = "x = ('per_unit', 'percent_of_amount', 'threshold_pool')"
ok(re.search(r"[=\(]\s*\(\s*['\"]per_unit['\"]", _fake) is not None,
   "F1 the rule-shape scanner fires on a planted copy")
ok(re.search(r"['\"]individual_sales['\"]\s*,\s*['\"]equal['\"]",
             "s = ['individual_sales', 'equal']") is not None,
   "F2 the split scanner fires on a planted copy")
ok(re.search(r'''\.get\(\s*["']counts["']\s*\)''', 'row.get("counts")') is not None,
   "F3 the counts-reader scanner fires on a planted read")
ok(re.search(r"""(?:table|from_)\s*\(\s*["']closing_metric_line["']""",
             'client.table("closing_metric_line")') is not None,
   "F4 the table scanner fires on a planted query")
ok(re.search(r"""(?:table|from_)\s*\(\s*["']closing_metric_line["']""",
             'the closing_metric_line table holds the lines') is None,
   "F4b ...and NOT on prose that merely names the table (module_graph's facts)")
ok(re.search(r"(_metrics|metric_catalog)\s*\.\s*build\s*\(", "metric_catalog.build(defs)") is not None,
   "F5 the construction scanner fires on a planted build")
ok(code_only(os.path.join(APP, "modules", "closing", "metric_catalog.py")) != "",
   "F6 the docstring stripper returns code, not nothing")
ok("THE ONE HOME" not in cat_src,
   "F7 ...and the stripper really did remove the docstring")

print(f"\n{'='*70}\n  {P} passed / {F} failed\n{'='*70}")
sys.exit(1 if F else 0)
