#!/usr/bin/env python3
"""LOCK — the COMPANY PICKER names a company, and `… || org_id` cannot come back.

Owner report 2026-10-30, with a screenshot of the mobile "Choose a company" screen: *"How do I know
which company is which — it should show the company name on the app instead of company id"*. The
screen listed four raw UUIDs. A login cannot choose a company it cannot read.

THE CLASS, not the instance. The instance is "the phone printed an id". The class is that a
company's human LABEL was derived in three places with three different last resorts:
`core/router._my_tenants_payload` (`t.get("name") or "Tenant"` — every unnamed company the same
unchoosable word), the web picker (`{t.name}` raw), and the app's `tenantName`
(`name || org_name || org_id` — the one that printed the defect). Three derivations, and the one at
the end of the chain could render an id.

SIBLING, checked and deliberately separate: `harness_scope_label_lock.py` locks the same RULE for a
FINANCE scope's name (`scope_label || scope_key`, index §13b.1, owner's near-identical report of
2026-10-03). Different registry and different fact — a tenant (`storeops.tenants`) is not an
accounting company (`coa.org_companies`), and one tenant holds many of the latter — so the two locks
stay separate and cross-reference each other. The rule they enforce is one rule: a label a person
reads is never an id.

One home: `app/modules/core/tenant_identity.py` (`picker_label_from`, `picker_labels`,
`looks_like_uuid`, `GENERIC_LABEL`) server-side — the SAME module that already owns "what is this
tenant called" (§POS-02) — and `mobile/src/lib/company-label.ts` (`companyLabel`, `looksLikeOrgId`,
`GENERIC_COMPANY_LABEL`) in the app, which keeps the promise even against a server that does not,
because a phone can be running a bundle months older than this repository.

THIS FAILS THE BUILD WHEN:
  L1  the one home stops existing, or either half stops exporting the three names.
  L2  the server label can be blank or can be an id, for ANY row shape (run, not read).
  L3  the client label rule drifts from the server's: the same rows must produce the same strings,
      including the generic last resort and the slug-read-back-as-words fallback (run, not read).
  L4  any backend file re-introduces a label fallback onto the id or onto a stand-in word
      (`or "Tenant"`, `or org_id`, `name or o`) in a payload that labels a company.
  L5  `_my_tenants_payload` un-wires: it must dereference the one home rather than spell a label.
  L6  a picker/switcher surface renders `org_id` as the label: the two mobile screens and the web
      login picker must show the dereferenced label, never the id field.
  L7  a SECOND copy of the rule appears — an org-id regex or a `'Company'`/`"Tenant"` last resort
      outside the two homes.

Every rule has a NEGATIVE CONTROL: it is run against a synthetic violation and must fire.
Stdlib only, DB-free, no network: `python3 backend/harness_company_label_lock.py`.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from app.modules.core import tenant_identity as ti  # noqa: E402  (path set above)

HOME_PY_REL = "backend/app/modules/core/tenant_identity.py"
HOME_TS_REL = "mobile/src/lib/company-label.ts"
ROUTER_REL = "backend/app/modules/core/router.py"
MOBILE_PICKER = "mobile/app/choose-company.tsx"
MOBILE_SWITCHER = "mobile/app/(app)/(tabs)/settings.tsx"
WEB_PICKER = "frontend/src/app/login/page.tsx"

SKIP_DIRS = {"node_modules", ".next", "out", "build", "__pycache__", "scratchpad", ".git",
             "android", "ios", ".expo"}

HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        for d in (detail or [])[:20]:
            print(f"          {d}")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def strip_py(text):
    """Comments and docstrings removed — this lock is about CODE, and the prose deliberately quotes
    the banned patterns in order to explain them."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    drop = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for stmt in body:
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)):
                for ln in range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1):
                    drop.add(ln)
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        if i in drop:
            out.append("")
            continue
        q = None
        cut = len(ln)
        for j, ch in enumerate(ln):
            if q:
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch
            elif ch == "#":
                cut = j
                break
        out.append(ln[:cut])
    return "\n".join(out)


def strip_ts(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in text.splitlines())


def walk(base, exts):
    for dirpath, dirs, files in os.walk(os.path.join(ROOT, base)):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in sorted(files):
            if fn.endswith(exts):
                yield os.path.relpath(os.path.join(dirpath, fn), ROOT).replace(os.sep, "/")


# ── the client rule, read out of the TypeScript so both halves are run, not compared by eye ─────
def ts_company_label(row):
    """`mobile/src/lib/company-label.ts` re-expressed in Python ONCE, here, and pinned to the real
    file by L1/L7: the file must still define the rule in the shape this mirrors. Running it is how
    L3 proves the two halves agree on every row shape rather than trusting that they do."""
    src = strip_ts(read(HOME_TS_REL))
    generic = re.search(r"GENERIC_COMPANY_LABEL\s*=\s*'([^']+)'", src)
    order = re.search(r"for \(const candidate of \[(.*?)\]\)", src, re.S)
    if not generic or not order:
        return None
    fields = re.findall(r"t\?\.(\w+)", order.group(1))
    for field in fields:
        raw = (row or {}).get(field)
        if field == "slug":
            if ti.looks_like_uuid(raw):
                continue
            text = " ".join(str(raw or "").replace("-", " ").replace("_", " ").split())
            candidate = re.sub(r"\b\w", lambda m: m.group(0).upper(), text)
        else:
            candidate = " ".join(str(raw or "").split())
        if candidate and not ti.looks_like_uuid(candidate):
            return candidate
    return generic.group(1)


print("\n§L1  the one home exists, both halves")
py_src = read(HOME_PY_REL)
ts_src = read(HOME_TS_REL)
check("L1a the server half exports the label fact",
      all(hasattr(ti, n) for n in ("picker_label_from", "picker_labels", "looks_like_uuid", "GENERIC_LABEL")))
check("L1b the app half exports the same fact",
      all(n in ts_src for n in ("export function companyLabel", "export function looksLikeOrgId",
                                "export const GENERIC_COMPANY_LABEL")))
check("L1c the app half imports NOTHING, so CI can prove it without a device",
      not re.search(r"^\s*import\s", strip_ts(ts_src), re.M), [HOME_TS_REL])
check("L1d neither half names a tenant in its behaviour (RULE TWO)",
      not re.search(r"(?i)\b(vzone|luxelink|cellfonz|boost|verizon)\b", strip_py(py_src))
      and not re.search(r"(?i)\b(vzone|luxelink|cellfonz|boost|verizon)\b", strip_ts(ts_src)))

print("\n§L2  the server label is never blank and never an id (RUN, not read)")
ROWS = [None, {}, {"name": ""}, {"name": None, "slug": None}, {"name": "   "},
        {"name": HOUSE}, {"slug": OTHER}, {"name": OTHER, "slug": HOUSE},
        {"name": "Second Retail LLC"}, {"slug": "third-logistics"}]
offenders = [r for r in ROWS
             if not ti.picker_label_from(r, HOUSE) or ti.looks_like_uuid(ti.picker_label_from(r, HOUSE))]
check("L2a no row shape yields a blank or id label", not offenders, [str(r) for r in offenders])
check("L2b the id is not even reachable as a fallback",
      HOUSE not in ti.picker_label_from({}, HOUSE) and HOUSE not in ti.picker_label_from(None, HOUSE))
check("L2c a name wins, a slug is second, the generic word is last",
      ti.picker_label_from({"name": "A Co", "slug": "b-co"}, HOUSE) == "A Co"
      and ti.picker_label_from({"slug": "b-co"}, HOUSE) == "B Co"
      and ti.picker_label_from({}, HOUSE) == ti.GENERIC_LABEL)
check("L2d a missing tenants row is still pickable through the list read",
      ti.picker_labels(object(), [HOUSE]) == {HOUSE: ti.GENERIC_LABEL})

print("\n§L3  the app half agrees with the server half, row for row (RUN, not read)")
drift = []
for row in ROWS + [{"org_name": "Older Payload Co"}, {"name": "", "org_name": OTHER, "slug": "x-y"}]:
    want = ti.picker_label_from(row, HOUSE)
    got = ts_company_label(row)
    # `org_name` is an app-only tolerated alias of a payload shape the server never emits.
    if row and row.get("org_name") and not (row.get("name") or "").strip():
        continue
    if got != want:
        drift.append(f"{row!r}: server {want!r} vs app {got!r}")
check("L3a every row shape reads the same on both halves", not drift, drift)
check("L3b the app's last resort is a word, and the same word the server uses",
      ts_company_label({}) == ti.GENERIC_LABEL)
check("L3c the app rule was actually extracted from the file (the mirror is wired)",
      ts_company_label({"name": "A Co"}) == "A Co" and ts_company_label({"slug": "a-b"}) == "A B")

print("\n§L4  no backend file re-introduces a label fallback")
BAD_PY = re.compile(r'(?:name|label)[^\n]{0,40}\bor\s+(?:"Tenant"|\'Tenant\'|org_id\b)')
offenders = []
for rel in walk("backend/app", (".py",)):
    if rel == HOME_PY_REL.replace("backend/", "backend/"):
        continue
    for i, ln in enumerate(strip_py(read(rel)).splitlines(), 1):
        if BAD_PY.search(ln):
            offenders.append(f"{rel}:{i}: {ln.strip()}")
check("L4a nothing falls back to a stand-in word or to the id", not offenders, offenders)
check("L4a fires on the exact line this lock exists to forbid",
      bool(BAD_PY.search('"name": t.get("name") or "Tenant",'))
      and bool(BAD_PY.search('label = row.get("name") or org_id')))
check("L4a does NOT fire on an ordinary two-column fallback",
      not BAD_PY.search('mode_labels[o.get("key")] = o.get("label") or o.get("key")'))

print("\n§L5  the picker payload dereferences the one home")
router = strip_py(read(ROUTER_REL))
check("L5a _my_tenants_payload calls the one home for the label",
      "tenant_identity.picker_label_from" in router or "_tenant_identity.picker_label_from" in router)
check("L5b and spells no label of its own", 'or "Tenant"' not in router)

print("\n§L6  no picker surface renders the id")
# `key={t.org_id}` is React's list key — never painted. Only an id a PERSON reads is the defect,
# so the pattern is an id used as the thing shown: a `title=`/`label=` prop, or a JSX text child.
RENDERED_ID = re.compile(r"(?:title|label|children)=\{[^}]*\.org_id|>\s*\{\s*\w+\.org_id\s*\}")
surfaces = {MOBILE_PICKER: "tenantName(", MOBILE_SWITCHER: "tenantName(", WEB_PICKER: "{t.name}"}
for rel, needle in surfaces.items():
    raw = read(rel)
    src = strip_ts(raw)
    check(f"L6 {rel} shows the dereferenced label", needle in raw, [f"missing {needle!r}"])
    rendered_id = RENDERED_ID.search(src)
    check(f"L6 {rel} never renders org_id as text", not rendered_id,
          [rendered_id.group(0)] if rendered_id else [])
check("L6 fires on an id used as the button's title",
      bool(RENDERED_ID.search("<Button title={t.org_id} />")))
check("L6 fires on an id painted as a JSX child",
      bool(RENDERED_ID.search("<span>{t.org_id}</span>")))
check("L6 does NOT fire on a React list key",
      not RENDERED_ID.search("<Button key={t.org_id} title={tenantName(t)} />"))

print("\n§L7  no second copy of the rule")
UUID_RE = re.compile(r"\[0-9a-f\]\{8\}-\[0-9a-f\]\{4\}")
# Spelling the id SHAPE is not by itself a second copy of this rule — these three validate that a
# string IS an id (an actor, a pipeline run, a device id). They answer "is this an id", never "what
# do we call this company", so they are not siblings. Anything else that spells it is.
NOT_A_LABEL = {"backend/app/modules/vision/google_sdm.py",
               "backend/app/modules/core/fix_pipeline.py",
               "backend/app/core/actors.py"}
copies = []
for rel in list(walk("backend/app", (".py",))) + list(walk("mobile/src", (".ts", ".tsx"))) \
        + list(walk("mobile/app", (".ts", ".tsx"))):
    if rel in (HOME_PY_REL, HOME_TS_REL) or rel in NOT_A_LABEL:
        continue
    body = strip_py(read(rel)) if rel.endswith(".py") else strip_ts(read(rel))
    if UUID_RE.search(body):
        copies.append(rel)
check("L7a the org-id shape is spelled in the two homes and nowhere else", not copies, copies)
stand_ins = []
for rel in list(walk("mobile/src", (".ts", ".tsx"))) + list(walk("mobile/app", (".ts", ".tsx"))):
    if rel == HOME_TS_REL:
        continue
    if re.search(r"""\|\|\s*['"](?:Company|Tenant)['"]""", strip_ts(read(rel))):
        stand_ins.append(rel)
check("L7b no app file invents its own last-resort company word", not stand_ins, stand_ins)
check("L7a fires on a second copy of the id shape",
      bool(UUID_RE.search("const X = /^[0-9a-f]{8}-[0-9a-f]{4}-/")))
check("L7b fires on a second last-resort word",
      bool(re.search(r"""\|\|\s*['"](?:Company|Tenant)['"]""", "const n = t.name || 'Company'")))

print(f"\n{'='*78}\n  {PASS} passed, {FAIL} failed\n{'='*78}")
sys.exit(1 if FAIL else 0)
