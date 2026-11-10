"""LOCK — the per-tenant feature gate stays ONE truth table, and the three mechanisms keep
dereferencing it. Stdlib only, STATIC on purpose: a caller that simply stops asking the gate compiles,
renders and ships, so neither a type-check nor a test run can see it.

OWNER (2026-11-10): *"why is vidapay connector showing in boost ? we need to get these things under
control before we roll out for outside customers."*

WHY A LOCK AT ALL (CLAUDE.md, "A fix is a DESIGN fix"): the registry-written-but-not-wired failure
has happened in this repo three times (index §19.18), and this feature is exactly that shape — a
fact in one home that is only a fix while every caller reads it. Each line below is one of the ways
it could quietly un-wire, with a negative control proving the line can go red:

  A  the gate module is the ONLY place the truth table exists
  B  the three mechanisms dereference it (module entitlement, connectors, report kinds)
  C  the grant home is NOT the derived mirror, and there is only one reader of the grant rows
  D  the posture column has ONE reader
  E  the degrade direction stays `inherit` — a gate must never start refusing on its own
  F  the connector-keyed report-pull rows keep being asked the connector predicate (the owner's leak)
  G  the dashboard decides nothing: no list of its own, no truth table, no vendor/tenant name
  H  RULE TWO + the house-org trap that produced the bug

    python3 backend/harness_feature_gate_lock.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
P = F = 0
TQ = chr(34) * 3
TQ1 = chr(39) * 3


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {name}")
    else:
        F += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def ctl(name, cond, detail=""):
    check("CONTROL " + name, cond, detail)


def read(*parts):
    p = os.path.join(ROOT, *parts)
    with open(p, encoding="utf-8") as f:
        return f.read()


def code_only(src):
    """The EXECUTING lines of PYTHON — docstrings and comments stripped — so a rule about code is
    neither satisfied nor broken by prose.

    Triple quotes are counted ANYWHERE on the line, not only at its start. A docstring whose closing
    quotes END a sentence is the common shape in this repo, and a stripper that only looks at the
    start of a line never sees that close: it then swallows all the code after it and reports a
    FALSE PASS. That is exactly what the first draft of this lock did.
    """
    out, in_doc = [], False
    for line in src.splitlines():
        n = line.count(TQ) + line.count(TQ1)
        if in_doc:
            if n:
                in_doc = (n % 2 == 0)
            continue
        if n:
            if n % 2:
                in_doc = True
            continue
        t = line.strip()
        if t.startswith('#'):
            continue
        out.append(line.split('  #')[0])
    return '\n'.join(out)


def tsx_code_only(src):
    """The EXECUTING lines of a .tsx file — `//` and `/* */` comments stripped."""
    src = re.sub(r'/\*[\s\S]*?\*/', '', src)
    return '\n'.join(ln for ln in src.splitlines() if not ln.strip().startswith('//'))


FG = read("backend", "app", "modules", "core", "feature_gate.py")
EN = read("backend", "app", "modules", "core", "entitlements.py")
CR = read("backend", "app", "modules", "commcalc", "connector_registry.py")
RK = read("backend", "app", "modules", "commcalc", "report_kinds.py")
RP = read("backend", "app", "modules", "commcalc", "report_pull.py")
API = read("backend", "app", "modules", "core", "tenant_gates_api.py")
PAGE = read("frontend", "src", "app", "(platform)", "admin", "tenant-gates", "page.tsx")
MIG = read("database", "migrations", "1119_tenant_feature_gate_posture.sql")
RBAC = read("frontend", "src", "lib", "rbac.ts")

print("\nA. one truth table, in one file")
check("A1 the gate declares the posture vocabulary and nothing else does",
      'POSTURE_GATED = "gated"' in FG and
      not any('POSTURE_GATED = ' in src for src in (EN, CR, RK, RP, API)))
check("A2 the four bases are declared once, in the gate",
      all(b in FG for b in ("BASIS_GRANT", "BASIS_DENY", "BASIS_GATED_DEFAULT", "BASIS_RULE")) and
      not any(re.search(r"^BASIS_\w+ *=", src, re.M) for src in (EN, CR, RK, API)))
check("A3 no other module re-derives the table — the words 'gated' and 'inherit' are not compared "
      "to a string anywhere else",
      not any(re.search(r'==\s*[\'"](gated|inherit)[\'"]', code_only(src))
              for src in (EN, CR, RK, RP, API)),
      "a literal posture comparison outside the gate IS the second copy")
ctl("A3 can go red", bool(re.search(r'==\s*[\'"](gated|inherit)[\'"]', 'x == "gated"')))
check("A4 `decide` is keyword-only, so no call site can swap grant and posture",
      re.search(r"def decide\(\*,", FG) is not None)

print("\nB. the three mechanisms dereference it")
check("B1 module entitlement asks the gate, per module",
      "feature_gate" in EN and "_fg.allowed(" in EN and "_fg.load_posture(" in EN)
check("B2 layer 1 is still available on its own, so the board shows both halves",
      "def effective_modules_by_rule(" in EN and "effective_modules_by_rule(client, org_id)" in EN)
check("B3 the shared predicate (report_kinds) asks the gate instead of its own if-ladder",
      "_gate(grant=" in RK and "feature_gate" in RK)
check("B4 the connector registry passes the posture STRAIGHT THROUGH — it keeps no rule of its own",
      "posture=posture" in CR and "def visible(rows, declaration, caps=None, org_id=None, posture=None)" in CR)
check("B5 the connector scope context carries the posture, so every connector surface inherits it",
      '"posture": load_posture(client, org_id)' in CR and 'ctx.get("posture")' in CR)
ctl("B3 can go red", "_gate(grant=" in RK)

print("\nC. the grant home — one reader, and NOT the derived mirror")
check("C1 the grant home is the override table, never storeops.tenant_modules",
      "ui_label_override" in FG and
      not re.search(r'table\("tenant_modules"\)', code_only(FG)),
      "tenant_modules is DERIVED by sync_tenant, so it cannot also BE the decision")
check("C2 exactly one function reads the grant rows",
      len(re.findall(r'table\("ui_label_override"\)', code_only(FG))) <= 2 and
      "def load_grants(" in FG)
check("C3 the connector registry's cap loader DEREFERENCES that reader rather than reading again",
      "_fg.load_grants(client, org_id, _fg.KIND_CONNECTOR)" in CR and
      not re.search(r'table\("ui_label_override"\)', code_only(CR)))
check("C4 a grant has THREE states and absence is one of them",
      "def grant_state(" in FG and "if value is None:" in FG and "return None" in FG)
check("C4b what is READ OUT of the shared grant home is read STRICTLY — only the two words this "
      "module writes, so another writer's synonym cannot mean 'granted'",
      "def stored_grant(" in FG and "g = stored_grant(r.get(\"label\"))" in FG,
      "load_grants must use the strict reader, not the lenient inbound one")
ctl("C4b can go red", 'g = stored_grant(r.get("label"))' in FG)
check("C5 clearing a decision DELETES the row rather than storing the opposite",
      re.search(r"if value is None:\s*\n\s*tbl\.delete\(\)", FG) is not None)

print("\nD. the posture column — one reader, one writer")
check("D1 the column is named once and read through the gate",
      'COLUMN = "feature_gate_posture"' in FG and
      not any("feature_gate_posture" in code_only(src) for src in (EN, CR, RK, RP)))
check("D2 the API and the migration name the same column",
      "feature_gate_posture" in MIG and "FG.COLUMN" in API)
check("D3 a write refuses a value the vocabulary does not name",
      "raise ValueError" in FG and "def save_posture(" in FG)
check("D4 the migration's CHECK pins the same two words",
      "CHECK (feature_gate_posture IN ('inherit','gated'))" in MIG)

print("\nE. the degrade direction — never a new refusal")
check("E1 an unknown posture resolves to inherit, in the gate",
      "POSTURE_DEFAULT = POSTURE_INHERIT" in FG)
check("E2 every posture read is exception-guarded and returns the default, not a refusal",
      FG.count("return POSTURE_DEFAULT") >= 2 and
      re.search(r"def load_posture[\s\S]{0,900}?except Exception:\s*\n\s*return POSTURE_DEFAULT", FG)
      is not None)
check("E3 a grant read that fails withholds nothing",
      re.search(r"def load_grants[\s\S]{0,1200}?except Exception:\s*\n\s*return \{\}", FG) is not None)
check("E4 a feature_gate that cannot be imported leaves the old answer in place",
      all("except Exception:" in src for src in (RK, CR)) and
      "from app.modules.core import feature_gate as _fg" in RK)
# The EXECUTING SQL only: the REVERT notes at the top of every migration here carry example
# statements, and a search over the whole file finds those first.
_MIG_SQL = "\n".join(l for l in MIG.splitlines() if not l.strip().startswith("--"))
_backfill = re.search(r"UPDATE storeops\.tenants[\s\S]*?;", _MIG_SQL).group(0)
check("E5 the migration BACKFILLS every tenant alive at its cutoff to inherit, on a FIXED date",
      "SET feature_gate_posture = 'inherit'" in _backfill and "DEFAULT 'gated'" in MIG and
      "NOW()" not in _backfill and "::timestamptz" in _backfill,
      "NOW() here would re-open a deliberately gated new tenant on any re-run")
ctl("E5 can go red", "DEFAULT 'gated'" in MIG)

print("\nF. the owner's leak — connector-keyed config rows are asked the predicate")
check("F1 the report-pull resolution asks the connector registry at its ONE home",
      "def withheld_processors(" in RP and "connector_registry" in RP and
      "scope_for(ctx, s)" in RP)
check("F2 the vendor slug family is DEREFERENCED from the registry, not a literal pair in code",
      "def connector_family(" in RP and 'row.get("aliases")' in RP)
# `scoped` is a REQUIRED keyword, not a default, because this home answers two different questions:
# "what may this tenant PULL" (the gate applies) and "what is this report's SHAPE" (a column map, an
# upload catalog — the gate is not that question). A default would let a new pull caller inherit the
# wrong one silently, which is the whole class §CORE-41 exists to close. So: the signature must carry
# the bare `*, scoped` marker, and EVERY call site in the tree must name it.
_rp_sig = re.search(r"def resolve_report_specs\(([^)]*)\)", RP).group(1)
check("F3 `scoped` is a REQUIRED keyword — no default, so no caller can answer without asking",
      "*, scoped" in _rp_sig and "scoped=True)" not in _rp_sig and "scoped=False)" not in _rp_sig)
def _arglist(src, at):
    """The text between the call's own parentheses, counted — `sb()` inside an argument list means a
    lazy regex would stop at the wrong bracket and read every call as argument-free."""
    depth, i = 0, at
    while i < len(src):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[at + 1:i]
        i += 1
    return ""


_rp_calls = []
for _f in ("router.py", "vidapay_sweep.py", "report_pull.py"):
    _src = read("backend", "app", "modules", "commcalc", _f)
    for _m in re.finditer(r"\brp\.resolve_report_specs(?=\()", _src):
        _rp_calls.append((_f, _arglist(_src, _m.end())))
check("F3b every caller NAMES the question it is asking (a bare call cannot reach this home)",
      len(_rp_calls) >= 5 and all("scoped=" in _a for _f, _a in _rp_calls),
      "an omitted `scoped` is a pull path that forgot the gate, or a shape read that gained it")
check("F3c an unREADABLE registry withholds NOTHING (a mirror verdict never hides a tenant's config)",
      'if not ctx.get("ready"):' in RP and re.search(
          r'if not ctx\.get\("ready"\):\s*\n\s*return set\(\)', RP))
check("F4 what was withheld is REPORTED, never dropped silently",
      '"withheld": withheld' in read("backend", "app", "modules", "commcalc", "router.py"))
check("F5 the admin page prints the registry's own words for it",
      "withheld" in read("frontend", "src", "app", "(platform)", "commcalc",
                         "report-mappings", "page.tsx"))
ctl("F1 can go red", "def withheld_processors(" in RP)

print("\nG. the dashboard decides nothing")
check("G1 the page keeps no list of modules / connectors / report kinds",
      not re.search(r"(MODULE_CATALOG|HOUSE_CONNECTORS|HOUSE_KINDS)", PAGE) and
      "rows = shown?.rows" in PAGE)
check("G2 the page does not re-implement the truth table — it renders `allowed` and `basis`",
      "r.allowed" in PAGE and "r.basis" in PAGE and
      not re.search(r"by_rule\s*&&|grant\s*===\s*true\s*\?", PAGE))
check("G3 the API holds no judgement — it gathers and hands to the pure layer",
      "FG.row(" in API and "FG.summary(" in API and
      not re.search(r"^BASIS|def decide\(", API, re.M))
check("G4 by_rule for modules is read from entitlements, not recomputed",
      "effective_modules_by_rule" in API)
check("G5 by_rule for the two registries is the registries' own predicate",
      "RK.applies(r, decl)" in API and "tenant_declaration" in API)
check("G6 every endpoint is THE one super-admin gate, and no second gate is defined",
      "_require_super_admin" in API and API.count("def _gate(") == 1 and
      not re.search(r"super_admin\s*=|def _authority", API))
check("G7 the board takes an explicit org, never the middleware-rewritten org_id",
      "org: str = \"\"" in API and "org_id: str = ORG_ID" not in API)
check("G8 the page is registered in NAV as platform-only",
      "/admin/tenant-gates" in RBAC and
      re.search(r"/admin/tenant-gates'[^}]*platformOnly: true", RBAC) is not None)

print("\nG2. who may widen — the escalation path the security pass found")
RT = read("backend", "app", "modules", "commcalc", "router.py")
check("G2a the list of namespaces whose widening is platform-only has ONE home, in the gate",
      "PLATFORM_WIDEN_PREFIXES" in FG and "def widening_is_platform_only(" in FG)
check("G2b every gate KIND is in that list, derived from CAP_PREFIX rather than re-spelled",
      "tuple(CAP_PREFIX[k] for k in KINDS)" in FG)
check("G2c the cap WRITER dereferences it instead of enumerating the prefixes",
      "_feature_gate.widening_is_platform_only(scope, key, label)" in RT and
      not re.search(r"key\.startswith\('(carrier|pos|vertical|closing):'\)", RT),
      "an inline enumeration is the copy that fell behind `connector:`")
ctl("G2c can go red", "_feature_gate.widening_is_platform_only(scope, key, label)" in RT)
check("G2d the refusal asks what the value MEANS, never whether it spells one word",
      "if grant_state(label) is not True:" in FG and
      re.search(r"def widening_is_platform_only[\s\S]{0,700}?!=\s*GRANT_ON", FG) is None,
      "a == 'show' test leaves 'yes' / 'on' / '1' / true walking past it")
check("G2e narrowing is still the tenant's own — the asymmetry is the safety property",
      "GRANT_OFF" in FG and "def starter_grants(" in FG)
check("G2f no OTHER caller-controlled-scope writer reaches the cap scope",
      len(re.findall(r"\.table\('ui_label_override'\)[\s\S]{0,200}?upsert", RT)) <= 2)

print("\nH. RULE TWO, and the house-org trap that produced the bug")
VENDORS = ("cellfonz", "luxelink", "vzone", "boost", "vidapay", "epay", "b2bsoft", "t-cetra")
check("H1 no tenant, carrier, vendor or POS name is decided on in the gate",
      not any(w in code_only(FG).lower() for w in VENDORS),
      str([w for w in VENDORS if w in code_only(FG).lower()]))
check("H1b nor a carrier CODE as a value — 'total' is an English word, so it is pinned as a value "
      "rather than as a substring",
      not re.search(r"=\s*\[?['\"](total|boost|verizon)['\"]", code_only(FG)))
check("H2 nor in the API or the page",
      not any(w in (code_only(API) + tsx_code_only(PAGE)).lower() for w in VENDORS),
      str([w for w in VENDORS if w in (code_only(API) + tsx_code_only(PAGE)).lower()]))
check("H3 the starter policy is a POLICY (a module list), applied as grant rows, never read at "
      "decision time",
      "STARTER_MODULES" in FG and "def starter_grants(" in FG and
      "STARTER_MODULES" not in code_only(FG).split("def decide")[1].split("def allowed")[0])
check("H4 the house org id is not hardcoded into the gate — a tenant that doubles as the house org "
      "is the defect this exists to make visible, not a rule to encode",
      "00000000-0000-0000-0000-000000000001" not in code_only(FG))
check("H5 the migration states the house-org trap, so nobody re-derives the cause",
      "IS A REAL TENANT" in MIG.upper() and "INHERITANCE IS NOT A DECISION" in MIG.upper())
ctl("H1 can go red", "cellfonz" not in code_only(FG).lower())

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
print("OK — the tenant feature gate lock holds." if not F
      else "FAIL — the tenant feature gate can un-wire.")
sys.exit(1 if F else 0)
