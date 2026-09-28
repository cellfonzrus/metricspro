"""LOCK — ONE device classifier, ONE list of Executive-MTD pay categories, every consumer wired (index §6n).
stdlib only; static scan of backend/app + the two rate-editor pages.

THE FACTS AND THEIR HOMES:
  · WHAT DEVICE an activation activated — `installment_category` (mig 245: `resolve_line_category` /
    `resolve_chain_category` over the tenant's `installment_category_rule` rows + the built-in ladder + the
    catalog). `line_class` dereferences it (`_device_of_lines`) for the activation EVENT's device and owns
    only the per-org switch (`devices.enabled`) and the pay decision (`pay_category`, `devices.applies_to`).
  · WHICH categories Exec-MTD pay prices — `activation_bucketing.MTD_CATEGORIES`; the router aliases it, the
    `GET /commcalc/commission-mtd/categories` endpoint serves it, the rate editors read it
    (`_lib/mtdCategories.useMtdCategories`).

FAILS THE BUILD WHEN:
  (a) a second device classifier appears: a device-word membership test ("tablet" / "watch" / "ipad" /
      "wearable" …) in any backend module other than the home and the named, excused pre-existing ones;
      `resolve_chain_category` / `device_of` / `pay_category` / `unit_devices` defined twice; or
      `line_class` stops dereferencing `installment_category.resolve_chain_category`;
  (b) a second copy of the category list appears (a backend tuple / list literal of the categories, or a
      literal category list in a rate-editor page), or a rate editor stops reading `useMtdCategories`;
  (c) a consumer un-wires: the cell pass stops asking `unit_devices`, the sales basis stops splitting the
      device sets, the Exec-MTD rows lose `watch`, the loader stops handing the classifier's rules to
      `line_class`, a category-rule write stops dropping the config memo, the Exec-MTD basis stops bridging
      employee-scope names through `_rep_canon_map`;
  (d) RULE TWO: a product / carrier / tenant word appears in the device code;
  (f) THE SINGLE-ASSIGNMENT REMOVER is the only writer that deletes a commission_plan_assignment row outside the
      two named plan-wide / reassign replacers (the plan save, bulk-assign): any other function deleting from
      that table turns this RED; the remover must filter its delete on org_id + plan_id + id; its route must call
      THE 'commission_plans' gate (`_require_commission_plans_edit`, the only `_can_edit_setting(...,
      "commission_plans")` decision), 404 a miss and drop the config memo; the Commission Plans page removes a
      saved assignment only through that route;
  (e) negative controls — each planted violation turns this lock RED.

    python3 backend/harness_exec_mtd_device_rates_lock.py
"""
import functools
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
FE = os.path.join(os.path.dirname(ROOT), "frontend", "src", "app", "(platform)", "commcalc")
LC = "modules/commcalc/line_class.py"
ICAT = "modules/commcalc/installment_category.py"
AB = "modules/commcalc/activation_bucketing.py"
ROUTER = "modules/commcalc/router.py"
EDITORS = ("commission-plans/page.tsx", "commission-structure/page.tsx")
# device-word membership tests that pre-date this design, each for a DIFFERENT fact — named, never silent
DEVICE_WORD_EXCUSED = {
    ICAT: "THE device classifier (its built-in ladder)",
    "modules/commcalc/discrepancy_engine.py": "pre-existing: the CARRIER rate card's plan-name category in the "
                                              "carrier-discrepancy engine (not rep pay) — reported, 2026-09-28",
    "modules/asset/inventory_buckets.py": "inventory stock buckets (asset module) — not an activation",
    "modules/asset/router.py": "inventory stock buckets (asset module) — not an activation",
    "modules/pos/catalog_suggest.py": "POS catalog department suggestions — not an activation",
    (ROUTER, "get_top_sellers"): "pre-existing top-sellers DISPLAY heuristic (is this a device model row)",
    (AB, "activation_details_bucket"): "pre-existing: the Activation-DETAILS basis buckets the carrier's own AD "
                                       "report rows (its 'Tablet' column); unifying it with the ladder moves "
                                       "AD-basis counts — reported as the remaining sibling, 2026-09-28",
}
_DW = r"(?:tablet|tablets|watch|watches|ipad|wearable|wearables|smartwatch|gizmo)"
# a device-word MEMBERSHIP TEST: `'tablet' in text`, or `any(k in text for k in [..., 'tablet', ...])`
DEVICE_WORD = re.compile(r"""["']%s["']\s+in\s+[\w.(]|\bin\s+[\w.()]+\s+for\s+\w+\s+in\s+[\[\(][^\]\)]*["']%s["']"""
                         % (_DW, _DW), re.I)
HOME_DEFS = {LC: ("device_of", "pay_category", "unit_devices", "_device_of_lines", "resolve_devices"),
             ICAT: ("resolve_chain_category", "resolve_line_category")}
# the Exec-MTD category list = the activation classes PLUS the device / basis categories, in one literal
CAT_LITERAL = re.compile(r"""[\(\[]\s*["']activation["']\s*,\s*["']port["']\s*,\s*["']byod["'][^\)\]]*["'](?:tablet|home_internet|watch)["']""")
FE_CAT_LITERAL = re.compile(r"""key:\s*['"](?:tablet|home_internet|edge|upgrade)['"]""")
# (f) the functions that may DELETE commission_plan_assignment rows — named, never silent
ASSIGN_DELETE_ALLOWED = {
    (ROUTER, "save_commission_plan"): "the plan save: plan-wide REPLACE of rules / tiers / assignments",
    (ROUTER, "bulk_assign_commission_plan"): "bulk-assign: a person MOVING plans (replace_existing), insert-then-delete",
    (ROUTER, "_remove_plan_assignment"): "THE single-assignment remover (DELETE .../assignments/{assignment_id})",
}
REMOVE_ROUTE = '@router.delete("/commission-plans/{plan_id}/assignments/{assignment_id}")'
PLANS_PAGE = "commission-plans/page.tsx"
PRODUCT_WORDS = ("ipad", "iphone", "apple", "samsung", "galaxy", "verizon", "gizmo", "boost", "luxelink")
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:600]))


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def walk(root, exts):
    out = {}
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules", ".next")]
        for f in fs:
            if f.endswith(exts):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, root).replace(os.sep, "/")] = read(p)
    return out


@functools.lru_cache(maxsize=None)
def py_code(src):
    src = re.sub(r'"""[\s\S]*?"""', '""', src)
    return "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))


def ts_code(src):
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return "\n".join(ln.split("//", 1)[0] for ln in src.split("\n"))


def fn_body(src, name):
    m = re.search(r"^(?:async\s+)?def\s+%s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    rest = src[m.start():]
    nxt = re.search(r"^(?:@router\.|def |class |async def )", rest[m.end() - m.start():], re.M)
    return rest[: (m.end() - m.start()) + nxt.start()] if nxt else rest


def route_body(src, route):
    """the code of the handler registered for `route` (a function NAME may be defined twice in router.py)"""
    i = src.find(route)
    if i < 0:
        return ""
    rest = src[i:]
    m = re.search(r"\n(?:@router\.|def |class |async def )", rest[len(route):])
    j = rest.find("\ndef ")
    body_start = j if j >= 0 else 0
    nxt = re.search(r"^(?:@router\.|def |class |async def )", rest[body_start + 5:], re.M)
    return rest[: body_start + 5 + nxt.start()] if nxt else rest


_TOP = re.compile(r"^(?:@|def |class |async def )", re.M)


@functools.lru_cache(maxsize=None)
def top_level_functions(src):
    """[(name, code)] for every top-level function — ONE linear split (router.py is ~30k lines)"""
    code = py_code(src)
    starts = [m.start() for m in _TOP.finditer(code)] + [len(code)]
    out = []
    for a, b in zip(starts, starts[1:]):
        m = re.match(r"(?:async\s+)?def\s+(\w+)\s*\(", code[a:b])
        if m:
            out.append((m.group(1), code[a:b]))
    return out


def functions_with(src, rx):
    """names of the top-level functions whose code matches rx"""
    return [name for name, code in top_level_functions(src) if rx.search(code)]


def scan(be, fe):
    v = []
    # (a) one device classifier
    for rel, src in sorted(be.items()):
        code = py_code(src)
        for home, defs in HOME_DEFS.items():
            if rel == home:
                continue
            for d in defs:
                if re.search(r"^def\s+%s\s*\(" % d, code, re.M):
                    v.append(("a", rel, "a second " + d))
        if rel in DEVICE_WORD_EXCUSED or rel == LC:
            continue
        if DEVICE_WORD.search(code):
            fns = functions_with(src, DEVICE_WORD)
            left = [f for f in fns if (rel, f) not in DEVICE_WORD_EXCUSED]
            if left or not fns:
                v.append(("a", rel, "classifies a device on its own (%s)" % (left or "module level")))
    lc = py_code(be.get(LC, ""))
    if "_icat.resolve_chain_category(" not in py_code(fn_body(be.get(LC, ""), "_device_of_lines")):
        v.append(("a", LC, "_device_of_lines no longer dereferences installment_category.resolve_chain_category"))
    if DEVICE_WORD.search(lc):
        v.append(("a", LC, "line_class grew device words of its own"))
    # (b) one category list
    ab = be.get(AB, "")
    if not re.search(r"^MTD_CATEGORIES\s*=\s*\(", ab, re.M) or '"watch"' not in ab or '"tablet"' not in ab:
        v.append(("b", AB, "MTD_CATEGORIES missing, or without tablet / watch"))
    for rel, src in sorted(be.items()):
        if rel == AB:
            continue
        if CAT_LITERAL.search(py_code(src)):
            v.append(("b", rel, "a second copy of the Exec-MTD category list"))
    if "_MTD_ACT_CATEGORIES = _ab_cats.MTD_CATEGORIES" not in be.get(ROUTER, ""):
        v.append(("b", ROUTER, "_MTD_ACT_CATEGORIES no longer aliases activation_bucketing.MTD_CATEGORIES"))
    if "_MTD_ACT_CATEGORIES" not in py_code(fn_body(be.get(ROUTER, ""), "commission_mtd_categories")):
        v.append(("b", ROUTER, "the categories endpoint no longer serves the one list"))
    for page in EDITORS:
        code = ts_code(fe.get(page, ""))
        if "useMtdCategories()" not in code:
            v.append(("b", page, "the rate editor no longer reads useMtdCategories"))
        if FE_CAT_LITERAL.search(code) or re.search(r"MTD_CATS\s*:\s*\{", code):
            v.append(("b", page, "a literal category list in the rate editor"))
    # (c) consumers wired
    router = be.get(ROUTER, "")
    needs = {"_sales_cell_agg": ["_lc.unit_devices("],
             "_apply_activation_basis": ['a.get("_dev_tablet")', 'a["act_tablet"] = len(_dt)', 'a["act_watch"]'],
             "_line_rules_resolve": ["load_category_rules(", "device_rules="],
             "_commission_mtd_result": ["_rep_canon_map("],

             "_exec_mtd": ["d['watch']", "'watch': _wat"]}
    for fn, ns in needs.items():
        body = py_code(fn_body(router, fn))
        for n in ns:
            if n not in body:
                v.append(("c", ROUTER, "%s no longer carries: %s" % (fn, n)))
    for route in ('@router.post("/plan-installments/category-rules")',
                  '@router.delete("/plan-installments/category-rules/{rid}")'):
        body = py_code(route_body(router, route))
        if body.count("_invalidate_accessory_config(org_id)") < 1:
            v.append(("c", ROUTER, "%s no longer drops the config memo the device dimension reads" % route))
    # (d) RULE TWO over the device code this design added
    for rel, (a, b) in ((LC, ("THE DEVICE DIMENSION", "def resolve_exclusions")),):
        src = be.get(rel, "")
        seg = py_code(src[src.find(a): src.find(b)]) if a in src and b in src else ""
        hits = [w for w in PRODUCT_WORDS if re.search(r"\b%s\b" % w, seg, re.I)]
        if hits:
            v.append(("d", rel, "product / carrier words in the device code: %s" % hits))
    # (f) the single-assignment remover is the only one
    for rel, src in sorted(be.items()):
        if "commission_plan_assignment" not in src:
            continue
        for name, body in top_level_functions(src):
            if "commission_plan_assignment" in body and ".delete()" in body \
                    and (rel, name) not in ASSIGN_DELETE_ALLOWED:
                v.append(("f", rel, "%s deletes commission_plan_assignment rows — use the single-assignment "
                                    "remover" % name))
    rm = py_code(fn_body(router, "_remove_plan_assignment"))
    if '.delete().eq("org_id", org_id).eq("plan_id", pid).eq("id", aid)' not in rm:
        v.append(("f", ROUTER, "_remove_plan_assignment no longer filters its delete on org_id + plan_id + id"))
    rb = py_code(route_body(router, REMOVE_ROUTE))
    for n in ("_require_commission_plans_edit(", "_remove_plan_assignment(", "HTTPException(404",
              "_invalidate_accessory_config(org_id)"):
        if n not in rb:
            v.append(("f", ROUTER, "the remover route no longer carries: %s" % n))
    gates = [(rel, len(re.findall(r"""_can_edit_setting\(\s*\w+\s*,\s*["']commission_plans["']""", py_code(src))))
             for rel, src in be.items()]
    gates = [g for g in gates if g[1]]
    if gates != [(ROUTER, 1)] or '_can_edit_setting(caller, "commission_plans")' not in py_code(
            fn_body(router, "_require_commission_plans_edit")):
        v.append(("f", ROUTER, "the 'commission_plans' permission decision is not in ONE gate: %s" % gates))
    page = ts_code(fe.get(PLANS_PAGE, ""))
    dels = re.findall(r"api\(`[^`]*commission-plans/\$\{[^`]*`\s*,\s*\{\s*method:\s*'DELETE'", page)
    if not any("/assignments/" in d for d in dels):
        v.append(("f", PLANS_PAGE, "the page no longer removes a saved assignment through the remover route"))
    return v


be = walk(APP, (".py",))
fe = walk(FE, (".ts", ".tsx"))
viol = scan(be, fe)
for part, label in (("a", "(a) ONE device classifier (installment_category); line_class dereferences it"),
                    ("b", "(b) ONE Exec-MTD category list, served to the rate editors"),
                    ("c", "(c) every consumer wired: cell pass, sales-basis split, rows, loader, memo, name bridge"),
                    ("d", "(d) RULE TWO: no product / carrier word in the device code"),
                    ("f", "(f) THE single-assignment remover is the only one; org + plan + id; gated; 404; memo")):
    check(label, not [x for x in viol if x[0] == part], [x[1:] for x in viol if x[0] == part])

print("\n  negative controls")


def planted(be_mut=None, fe_mut=None):
    b2, f2 = dict(be), dict(fe)
    b2.update(be_mut or {})
    f2.update(fe_mut or {})
    return scan(b2, f2)


v = planted(be_mut={"modules/commcalc/rep_devices.py":
                    "def kind(p):\n    return 'tablet' if any(w in p for w in ['tablet', 'ipad']) else None\n"})
check("(e) a second device classifier (a device-word test) → RED", any(x[0] == "a" and "rep_devices" in x[1] for x in v))
v = planted(be_mut={LC: be[LC].replace("_icat.resolve_chain_category(", "_own_chain_category(")})
check("(e) line_class classifying devices without the one classifier → RED", any(x[0] == "a" and LC == x[1] for x in v))
v = planted(be_mut={"modules/commcalc/pay_view.py": 'CATS = ("activation", "port", "byod", "tablet", "upgrade")\n'})
check("(e) a second copy of the category list in the backend → RED", any(x[0] == "b" and "pay_view" in x[1] for x in v))
v = planted(fe_mut={EDITORS[0]: fe[EDITORS[0]].replace("useMtdCategories()", "[{ key: 'tablet', label: 'Tablet' }]")})
check("(e) a rate editor spelling its own category list → RED", any(x[0] == "b" and EDITORS[0] in x[1] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace("_lc.unit_devices(", "_lc.unit_devices_off(")})
check("(e) the cell pass no longer asking for the unit's device → RED", any(x[0] == "c" and "_sales_cell_agg" in x[2] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace("dev_rules = _icat_dev.load_category_rules(client, org_id)", "dev_rules = None")})
check("(e) the loader no longer handing the classifier's rules to line_class → RED",
      any(x[0] == "c" and "_line_rules_resolve" in x[2] for x in v))
v = planted(be_mut={LC: be[LC].replace("DEVICE_CLASSES = (\"tablet\", \"watch\")",
                                       "DEVICE_CLASSES = (\"tablet\", \"watch\")\n_IPAD = 'ipad'")})
check("(e) a product word in the device code → RED", any(x[0] in ("a", "d") and LC == x[1] for x in v))
v = planted(be_mut={"modules/commcalc/plan_tools.py":
                    "def drop(client, org_id, i):\n    client.schema('commcalc').table('commission_plan_assignment')"
                    ".delete().eq('org_id', org_id).eq('id', i).execute()\n"})
check("(e) a second single-assignment writer → RED", any(x[0] == "f" and "plan_tools" in x[1] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace('.delete().eq("org_id", org_id).eq("plan_id", pid).eq("id", aid)',
                                               '.delete().eq("org_id", org_id).eq("id", aid)')})
check("(e) the remover dropping its plan filter → RED", any(x[0] == "f" and "plan_id" in x[2] for x in v))
v = planted(be_mut={ROUTER: be[ROUTER].replace(
    '    _require_commission_plans_edit(authorization, org_id, "remove a plan assignment")\n', '')})
check("(e) the remover route losing its gate → RED",
      any(x[0] == "f" and "_require_commission_plans_edit" in x[2] for x in v))
v = planted(be_mut={"modules/commcalc/plan_gate2.py":
                    "def ok(caller):\n    return _can_edit_setting(caller, 'commission_plans')\n"})
check("(e) a second 'commission_plans' permission decision → RED", any(x[0] == "f" and "ONE gate" in x[2] for x in v))
v = planted(fe_mut={PLANS_PAGE: fe[PLANS_PAGE].replace("/assignments/${a.id}", "/assignment-drop/${a.id}")})
check("(e) the page removing a saved assignment some other way → RED", any(x[0] == "f" and PLANS_PAGE == x[1] for x in v))
v = planted()
check("(e) the unmodified tree is GREEN", not v, v)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one device classifier, one category list, every consumer wired, one single-assignment remover.")
