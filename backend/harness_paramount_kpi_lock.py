"""THE LOCK — which column a KPI is read from is ONE exact-match table, and a header we do not know
yields NO value rather than the neighbouring column's.

CLAUDE.md, "A fix is a DESIGN fix": *"Name the class, not the instance… Lock it so it cannot un-wire.
A design fix ships with a check that FAILS THE BUILD if a caller stops dereferencing the shared fact,
or if a second copy appears."*

THE INSTANCE (owner 2026-09-25): the LuxeLink / Total door report carries BOTH `Current TWP+%` and
`Current TWP ALL%`, with TWP+ first. The reader matched the SUBSTRING "current twp" and took the FIRST
header containing it — so the Management-Incentive qualifier gated on TWP+ while the owner's rule is
TWP ALL. Door 168872: 50.0% against 67.0%. No error, no warning, a different answer.

THE CLASS: **a column chosen by resemblance is a gate that changes meaning when the report grows a
similarly-named column.** The report is someone else's; it gains columns without telling us. So the
fix is not "put TWP ALL first" — it is that every column is named EXACTLY, once, and an unrecognised
header resolves to nothing. Missing beats wrong on a money gate: a pending qualifier gets looked at,
a confidently wrong one gets paid.

WHAT FAILS THE BUILD
  (a) THE DECISION IS PURE. Which column is which lives in `resolve_columns(header)` — a function over
      a header row with no HTML parser behind it. Behavioural, over the OWNER'S REAL header row: `twp`
      is TWP ALL and `twp_plus` is TWP+, they are different columns, spacing variants land the same,
      and every metric the owner named resolves.
  (b) MISSING BEATS WRONG. An unrecognised look-alike header (`Current TWP SOMETHING%`) fills nothing;
      a report carrying ONLY TWP+ leaves `twp` ABSENT rather than borrowing TWP+ — the original bug's
      exact shape, asserted as a behaviour and not as a comment.
  (c) EXACT, NEVER SUBSTRING. `resolve_columns` compares whole keys (`h == w`); a substring, prefix or
      `find()` match reappearing in it → RED. The ONE substring left in the module is the Door TSP key
      column, quarantined in `door_column` where a miss costs a skipped row, never a wrong number.
  (d) NO TWO METRICS SHARE A HEADER. Every header key in the table is unique, so no report column can
      feed two metrics and no metric can shadow another.
  (e) ONE HOME. The header vocabulary exists in backend/app NOWHERE but paramount_kpi.py — a second
      copy is the divergence the index rules forbid.
  (f) THE CALLER DEREFERENCES. The import endpoint writes EVERY key the resolver returns
      (`for mk, val in mets.items()`) and carries no hardcoded metric whitelist — otherwise adding a
      column here would silently reach nothing.
  (g) NEGATIVE CONTROLS over synthetic sources: a resolver reverted to substring → RED; two metrics
      declaring one header → RED; a caller that re-introduces a whitelist → RED.

Runs beside the carrier-vocab / report-kind / line-class locks (.github/workflows/carrier-vocab-guard.yml).
Stdlib only, DB-free, no BeautifulSoup — that is the point of (a).

  python3 backend/harness_paramount_kpi_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE_APP = os.path.join(ROOT, "backend", "app")
HOME = "modules/commcalc/paramount_kpi.py"
HOME_ABS = os.path.join(BE_APP, "modules", "commcalc", "paramount_kpi.py")
ROUTER_ABS = os.path.join(BE_APP, "modules", "commcalc", "router.py")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

# The owner's own header row, 2026-09-25, verbatim and in his order. TWP+ BEFORE TWP ALL — that
# ordering is the whole defect, so the fixture must keep it.
OWNER_HEADER = ["Door TSP", "Address", "City", "Current Acts", "Pacing Acts", "Current Quota",
                "Pacing % to Quota", "Current TWP+%", "Current TWP ALL%", "Current Tablets",
                "Current FWA Acts", "Current Upgrades", "Current Edge Apply", "Current Edge Approve",
                "Current Edge Acts", "Current Autopay TA%", "Current Autopay all%"]

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


def fn_body(src, name):
    """Source text of one top-level def, from its line to the next top-level statement."""
    m = re.search(r"^def %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    rest = src[m.start():]
    nxt = re.search(r"\n(?=\S)", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


# ── (c) the exact-match scan, as a function so (g) can run it over a mutated source ───────────────
_RESEMBLE = re.compile(r"""
    (?: \bin\s+h\b                     # `w in h` / `sub in h`
      | \bh\s+in\s+(?!enumerate\b)\w   # `h in w`  (the `for i, h in enumerate(...)` binding is not a test)
      | \.startswith\(
      | \.endswith\(
      | \.find\(
      | \bre\.(?:search|match)\(
    )""", re.X)


def scan_exact(body):
    """-> list of complaints about a resolver that matches by resemblance instead of identity."""
    bad = []
    if "h == w" not in body:
        bad.append("resolve_columns no longer compares whole keys (`h == w` is gone)")
    for m in _RESEMBLE.finditer(body):
        bad.append("resolve_columns matches by RESEMBLANCE: %r" % body[max(0, m.start() - 30):m.end() + 10])
    return bad


# ── (d) header uniqueness, as a function so (g) can run it over a synthetic table ─────────────────
def scan_unique(table):
    seen, bad = {}, []
    for mk, wanted in table:
        for w in wanted:
            if w in seen:
                bad.append("header %r feeds BOTH %r and %r" % (w, seen[w], mk))
            seen[w] = mk
    return bad


# ── (f) the caller scan, as a function so (g) can run it over a mutated endpoint ──────────────────
_WHITELIST = re.compile(r"""(?:
      \bif\s+mk\s+(?:not\s+)?in\s*[\(\[\{]
    | \bmets\.get\(\s*['"]
    | \bfor\s+mk\s+in\s*[\(\[\{]
)""", re.X)


def scan_caller(body):
    bad = []
    if "for mk, val in mets.items()" not in body:
        bad.append("the import endpoint no longer writes every key the resolver returned")
    for m in _WHITELIST.finditer(body):
        bad.append("the import endpoint filters metric keys: %r" % body[max(0, m.start() - 20):m.end() + 40])
    return bad


# ═══ the engine itself — stdlib only, no bs4 (that is what (a) buys) ══════════════════════════════
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.modules.commcalc import paramount_kpi as _PK          # noqa: E402

SRC = read(HOME_ABS)
RT = read(ROUTER_ABS)
RESOLVER = fn_body(SRC, "resolve_columns")
ENDPOINT = fn_body(RT, "import_paramount_mtd")

print("\nParamount KPI column lock — the qualifier reads the column it was told to, or none\n")

# ── (a) the decision is pure, and it is right on the owner's real header row ──────────────────────
cols = _PK.resolve_columns(OWNER_HEADER)
check("(a) resolve_columns is a pure function over a header row (no HTML parser reached)",
      callable(getattr(_PK, "resolve_columns", None)) and "BeautifulSoup" not in RESOLVER)
check("(a) `twp` is Current TWP ALL%%  (index %s)" % cols.get("twp"),
      cols.get("twp") == OWNER_HEADER.index("Current TWP ALL%"), cols)
check("(a) `twp_plus` is Current TWP+%%  (index %s)" % cols.get("twp_plus"),
      cols.get("twp_plus") == OWNER_HEADER.index("Current TWP+%"), cols)
check("(a) they are DIFFERENT columns — the twin the substring match collapsed",
      cols.get("twp") != cols.get("twp_plus"), cols)
for mk, label in [("acts", "Current Acts"), ("pacing_acts", "Pacing Acts"), ("quota", "Current Quota"),
                  ("pacing_quota", "Pacing % to Quota"), ("tablets", "Current Tablets"),
                  ("fwa_acts", "Current FWA Acts"), ("upgrades", "Current Upgrades"),
                  ("edge_apply", "Current Edge Apply"), ("edge_approve", "Current Edge Approve"),
                  ("edge_acts", "Current Edge Acts"), ("autopay_ta", "Current Autopay TA%"),
                  ("autopay_all", "Current Autopay all%")]:
    check("(a) `%s` is %s" % (mk, label), cols.get(mk) == OWNER_HEADER.index(label), cols)
check("(a) the key column is found by name, not by position",
      _PK.door_column([h.lower() for h in OWNER_HEADER]) == 0)
spaced = [h.replace("TWP ALL%", "TWP ALL %").replace("Autopay TA%", "Autopay TA %") for h in OWNER_HEADER]
check("(a) spacing the export varies ('TWP ALL %') lands on the same column",
      _PK.resolve_columns(spaced).get("twp") == cols.get("twp")
      and _PK.resolve_columns(spaced).get("autopay_ta") == cols.get("autopay_ta"))
check("(a) the other sections' columns resolve too (zulu · finalized 3MR beats pacing 3MR)",
      _PK.resolve_columns(["Door TSP", "Pacing 3MR%", "Finalized 3MR%", "Current Zulu%"])
      == {"zulu": 3, "tmr3": 2})

# ── (b) missing beats wrong ───────────────────────────────────────────────────────────────────────
check("(b) an unrecognised look-alike ('Current TWP SOMETHING%') fills NOTHING",
      "twp" not in _PK.resolve_columns(["Door TSP", "Current TWP SOMETHING%"]))
check("(b) a report carrying ONLY TWP+ leaves `twp` ABSENT — it does not borrow TWP+  ← THE BUG",
      _PK.resolve_columns(["Door TSP", "Current TWP+%"]) == {"twp_plus": 1})
check("(b) a report carrying ONLY TWP ALL fills `twp` and leaves `twp_plus` absent",
      _PK.resolve_columns(["Door TSP", "Current TWP ALL%"]) == {"twp": 1})
check("(b) a header row we recognise nothing in resolves to {} (the table is skipped, not guessed)",
      _PK.resolve_columns(["Door TSP", "Address", "City"]) == {})
check("(b) '✔ On Track' / '—' / blank are NOT zero — they are no value",
      _PK._num("✔ On Track") is None and _PK._num("—") is None and _PK._num("") is None)
check("(b) numbers still read ('85.1%' · '1,234' · '0')",
      _PK._num("85.1%") == 85.1 and _PK._num("1,234") == 1234.0 and _PK._num("0") == 0.0)

# ── (c) exact, never substring ────────────────────────────────────────────────────────────────────
check("(c) resolve_columns compares whole keys — no substring / prefix / regex resemblance",
      not scan_exact(RESOLVER), scan_exact(RESOLVER))
check("(c) the ONE remaining substring is the Door TSP key column, quarantined in door_column",
      '"door" in h' in fn_body(SRC, "door_column") and '"door" in h' not in RESOLVER)

# ── (d) no two metrics share a header ─────────────────────────────────────────────────────────────
check("(d) every declared header is unique across the table", not scan_unique(_PK.QUALIFIER_COLUMNS),
      scan_unique(_PK.QUALIFIER_COLUMNS))
check("(d) every declared header is already whitespace-stripped and lowercase (or it can never match)",
      all(w == _PK._hkey(w) for _, ws in _PK.QUALIFIER_COLUMNS for w in ws),
      [w for _, ws in _PK.QUALIFIER_COLUMNS for w in ws if w != _PK._hkey(w)])

# ── (e) one home ──────────────────────────────────────────────────────────────────────────────────
VOCAB = re.compile(r"current\s*twp|currenttwp|finalized\s*3mr|current\s*zulu|pacing\s*%\s*to\s*quota", re.I)
second = []
for dp, dirs, fs in os.walk(BE_APP):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in fs:
        if not f.endswith(".py"):
            continue
        rel = os.path.relpath(os.path.join(dp, f), BE_APP).replace(os.sep, "/")
        if rel == HOME:
            continue
        if VOCAB.search(read(os.path.join(dp, f))):
            second.append(rel)
check("(e) the header vocabulary lives in ONE file — no second copy in backend/app", not second, second)

# ── (f) the caller dereferences ───────────────────────────────────────────────────────────────────
check("(f) the import endpoint exists and calls the one parser",
      bool(ENDPOINT) and "parse_paramount_mtd_kpis(html)" in ENDPOINT)
check("(f) it writes EVERY key the resolver returned — no metric whitelist",
      not scan_caller(ENDPOINT), scan_caller(ENDPOINT))

# ── (g) negative controls — each guard above must actually be able to go red ──────────────────────
check("(g) a resolver reverted to substring matching → RED",
      bool(scan_exact(RESOLVER.replace("if h == w", "if w in h"))))
check("(g) a resolver matching by prefix → RED",
      bool(scan_exact(RESOLVER.replace("h == w", "h.startswith(w)"))))
check("(g) two metrics declaring one header → RED",
      bool(scan_unique([("twp", ["currenttwpall%"]), ("twp_plus", ["currenttwpall%"])])))
check("(g) a caller that re-introduces a whitelist → RED",
      bool(scan_caller(ENDPOINT.replace("for mk, val in mets.items()",
                                        "for mk, val in mets.items() if mk in ('zulu', 'tmr3', 'twp')"))))
check("(g) a caller that stops writing what it parsed → RED",
      bool(scan_caller(ENDPOINT.replace("for mk, val in mets.items()", "for mk in ('zulu',)"))))

# ── the lock is wired into CI, or it is not a lock ────────────────────────────────────────────────
wf = read(WORKFLOW) if os.path.exists(WORKFLOW) else ""
check("(wired) this lock runs in carrier-vocab-guard.yml", "harness_paramount_kpi_lock.py" in wf)
check("(wired) the guard re-runs when the parser or its caller changes",
      "backend/app/modules/commcalc/paramount_kpi.py" in wf)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one exact column table; an unknown header yields no value, never the neighbour's.")
